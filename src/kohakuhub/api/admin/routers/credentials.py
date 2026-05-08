"""Admin endpoints for global credentials management.

Lists every active session, API token and SSH key on the deployment plus
revoke (single + bulk) for sessions. Keeping these three resources behind
a single namespace lets the admin UI render a unified Credentials page.

Audit logging hooks intentionally absent: that depends on the audit-log
work tracked in #37 and will be wired in once the dependency lands.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from kohakuhub.api.admin.utils import verify_admin_token
from kohakuhub.db import SSHKey, Session, Token, User
from kohakuhub.logger import get_logger

logger = get_logger("ADMIN")
router = APIRouter()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_naive_to_utc(value: datetime) -> datetime:
    """Peewee returns naive datetimes; pin them to UTC for comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _user_filter_or_none(username: str | None) -> User | None:
    if not username:
        return None
    return User.get_or_none(User.username == username)


def _resolve_user_filter(
    username: str | None,
) -> tuple[User | None, dict[str, Any] | None]:
    """Return ``(user, error_envelope)``.

    Returns ``(user, None)`` for a successful lookup, ``(None, None)`` when no
    filter was provided, or ``(None, error_payload)`` when the username does
    not exist (caller raises HTTPException with the payload).
    """
    if username is None:
        return None, None
    user = _user_filter_or_none(username)
    if user is None:
        return None, {"error": f"User '{username}' does not exist"}
    return user, None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _session_payload(session: Session, *, now: datetime) -> dict[str, Any]:
    expires_at = _coerce_naive_to_utc(session.expires_at)
    return {
        "id": session.id,
        "user_id": session.user.id if session.user else None,
        "username": session.user.username if session.user else None,
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "expired": expires_at <= now,
    }


@router.get("/sessions")
async def list_sessions(
    user: str | None = None,
    active_only: bool = False,
    created_after: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _admin: bool = Depends(verify_admin_token),
):
    """List active and expired sessions across all users."""
    target_user, err = _resolve_user_filter(user)
    if err is not None:
        raise HTTPException(404, detail=err)

    query = Session.select().join(User)
    if target_user is not None:
        query = query.where(Session.user == target_user)
    if active_only:
        query = query.where(Session.expires_at > _utc_now())
    if created_after is not None:
        query = query.where(Session.created_at >= created_after)

    total = query.count()
    rows = query.order_by(Session.created_at.desc()).limit(limit).offset(offset)

    now = _utc_now()
    return {
        "sessions": [_session_payload(s, now=now) for s in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: int,
    _admin: bool = Depends(verify_admin_token),
):
    """Revoke (hard-delete) a single session row."""
    session = Session.get_or_none(Session.id == session_id)
    if session is None:
        raise HTTPException(404, detail={"error": "Session not found"})
    session.delete_instance()
    logger.info(f"admin revoked session id={session_id}")
    return {"revoked": 1}


class _BulkSessionRevoke(BaseModel):
    user: str | None = Field(
        default=None,
        description="Restrict the bulk delete to this username.",
    )
    before_ts: datetime | None = Field(
        default=None,
        description="Only revoke sessions created strictly before this timestamp.",
    )


@router.post("/sessions/revoke-bulk")
async def revoke_sessions_bulk(
    body: _BulkSessionRevoke,
    _admin: bool = Depends(verify_admin_token),
):
    """Bulk-revoke sessions by user or by created-before timestamp.

    At least one filter must be supplied — empty bodies are rejected to keep
    "log every user out" behind an explicit, future-dated body shape rather
    than a one-character typo.
    """
    if body.user is None and body.before_ts is None:
        raise HTTPException(
            400,
            detail={"error": "At least one of 'user' or 'before_ts' is required"},
        )

    target_user, err = _resolve_user_filter(body.user)
    if err is not None:
        raise HTTPException(404, detail=err)

    query = Session.delete()
    if target_user is not None:
        query = query.where(Session.user == target_user)
    if body.before_ts is not None:
        query = query.where(Session.created_at < body.before_ts)

    revoked = query.execute()
    logger.info(
        f"admin bulk-revoked {revoked} session(s) "
        f"(user={body.user}, before_ts={body.before_ts})"
    )
    return {"revoked": revoked}


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------


def _token_payload(token: Token) -> dict[str, Any]:
    return {
        "id": token.id,
        "user_id": token.user.id if token.user else None,
        "username": token.user.username if token.user else None,
        "name": token.name,
        "created_at": token.created_at.isoformat(),
        "last_used": token.last_used.isoformat() if token.last_used else None,
    }


@router.get("/tokens")
async def list_tokens(
    user: str | None = None,
    unused_for_days: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _admin: bool = Depends(verify_admin_token),
):
    """List API tokens. ``unused_for_days`` matches "never used" plus tokens
    last touched more than N days ago — the typical staleness query."""
    target_user, err = _resolve_user_filter(user)
    if err is not None:
        raise HTTPException(404, detail=err)

    query = Token.select().join(User)
    if target_user is not None:
        query = query.where(Token.user == target_user)
    if unused_for_days is not None:
        cutoff = _utc_now().replace(tzinfo=None) - timedelta(days=unused_for_days)
        query = query.where(
            (Token.last_used.is_null(True)) | (Token.last_used < cutoff)
        )

    total = query.count()
    rows = query.order_by(Token.created_at.desc()).limit(limit).offset(offset)
    return {
        "tokens": [_token_payload(t) for t in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.delete("/tokens/{token_id}")
async def revoke_token(
    token_id: int,
    _admin: bool = Depends(verify_admin_token),
):
    """Revoke (hard-delete) a single API token."""
    token = Token.get_or_none(Token.id == token_id)
    if token is None:
        raise HTTPException(404, detail={"error": "Token not found"})
    token.delete_instance()
    logger.info(f"admin revoked token id={token_id}")
    return {"revoked": 1}


# ---------------------------------------------------------------------------
# SSH keys
# ---------------------------------------------------------------------------


def _ssh_key_payload(key: SSHKey) -> dict[str, Any]:
    return {
        "id": key.id,
        "user_id": key.user.id if key.user else None,
        "username": key.user.username if key.user else None,
        "key_type": key.key_type,
        "fingerprint": key.fingerprint,
        "title": key.title,
        "created_at": key.created_at.isoformat(),
        "last_used": key.last_used.isoformat() if key.last_used else None,
    }


@router.get("/ssh-keys")
async def list_ssh_keys(
    user: str | None = None,
    unused_for_days: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _admin: bool = Depends(verify_admin_token),
):
    """List SSH public keys. ``unused_for_days`` mirrors the tokens filter."""
    target_user, err = _resolve_user_filter(user)
    if err is not None:
        raise HTTPException(404, detail=err)

    query = SSHKey.select().join(User)
    if target_user is not None:
        query = query.where(SSHKey.user == target_user)
    if unused_for_days is not None:
        cutoff = _utc_now().replace(tzinfo=None) - timedelta(days=unused_for_days)
        query = query.where(
            (SSHKey.last_used.is_null(True)) | (SSHKey.last_used < cutoff)
        )

    total = query.count()
    rows = query.order_by(SSHKey.created_at.desc()).limit(limit).offset(offset)
    return {
        "ssh_keys": [_ssh_key_payload(k) for k in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.delete("/ssh-keys/{key_id}")
async def revoke_ssh_key(
    key_id: int,
    _admin: bool = Depends(verify_admin_token),
):
    """Revoke (hard-delete) a single SSH key."""
    key = SSHKey.get_or_none(SSHKey.id == key_id)
    if key is None:
        raise HTTPException(404, detail={"error": "SSH key not found"})
    key.delete_instance()
    logger.info(f"admin revoked ssh key id={key_id}")
    return {"revoked": 1}
