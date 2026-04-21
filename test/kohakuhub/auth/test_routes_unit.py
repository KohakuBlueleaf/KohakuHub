"""Unit tests for auth routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

import kohakuhub.auth.routes as auth_routes


class _Expr:
    def __init__(self, value):
        self.value = value

    def __and__(self, other):
        return _Expr(("and", self.value, getattr(other, "value", other)))


class _Field:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return _Expr((self.name, "==", other))


class _DeleteQuery:
    def __init__(self, execute_result=1):
        self.execute_result = execute_result
        self.where_calls = []

    def where(self, *args):
        self.where_calls.append(args)
        return self

    def execute(self):
        return self.execute_result


class _AtomicContext:
    def __init__(self, state: dict):
        self.state = state

    def __enter__(self):
        self.state["entered"] = self.state.get("entered", 0) + 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.state["exited"] = self.state.get("exited", 0) + 1
        return False


class _FakeUserModel:
    normalized_name = _Field("normalized_name")
    get_or_none_responses = []

    @classmethod
    def reset(cls):
        cls.get_or_none_responses = []

    @classmethod
    def get_or_none(cls, _expr):
        if cls.get_or_none_responses:
            return cls.get_or_none_responses.pop(0)
        return None


class _FakeSessionModel:
    user = _Field("user")
    get_or_none_responses = []
    delete_query = _DeleteQuery()

    @classmethod
    def reset(cls):
        cls.get_or_none_responses = []
        cls.delete_query = _DeleteQuery()

    @classmethod
    def get_or_none(cls, _expr):
        if cls.get_or_none_responses:
            return cls.get_or_none_responses.pop(0)
        return None

    @classmethod
    def delete(cls):
        return cls.delete_query


class _FakeTokenModel:
    id = _Field("id")
    user = _Field("user")
    get_or_none_responses = []

    @classmethod
    def reset(cls):
        cls.get_or_none_responses = []

    @classmethod
    def get_or_none(cls, _expr):
        if cls.get_or_none_responses:
            return cls.get_or_none_responses.pop(0)
        return None


@pytest.fixture(autouse=True)
def _reset_models():
    _FakeUserModel.reset()
    _FakeSessionModel.reset()
    _FakeTokenModel.reset()


@pytest.mark.asyncio
async def test_register_covers_invitation_reserved_and_conflict_paths(monkeypatch):
    atomic_state = {}
    user = SimpleNamespace(id=1, username="new-user", email="new@example.com")

    monkeypatch.setattr(auth_routes, "User", _FakeUserModel)
    monkeypatch.setattr(
        auth_routes, "db", SimpleNamespace(atomic=lambda: _AtomicContext(atomic_state))
    )
    monkeypatch.setattr(auth_routes.cfg.auth, "invitation_only", True)
    monkeypatch.setattr(auth_routes.cfg.auth, "require_email_verification", False)
    monkeypatch.setattr(auth_routes, "normalize_name", lambda value: value.lower().replace("-", ""))
    monkeypatch.setattr(auth_routes, "get_user_by_username", lambda username: None)
    monkeypatch.setattr(auth_routes, "get_user_by_email", lambda email: None)
    monkeypatch.setattr(auth_routes, "hash_password", lambda password: f"hashed:{password}")
    monkeypatch.setattr(auth_routes, "create_user", lambda **kwargs: user)
    monkeypatch.setattr(auth_routes, "get_invitation", lambda token: None)

    with pytest.raises(HTTPException) as missing_token:
        await auth_routes.register(
            auth_routes.RegisterRequest(
                username="new-user",
                email="new@example.com",
                password="secret",
            )
        )
    assert missing_token.value.status_code == 403

    with pytest.raises(HTTPException) as invalid_token:
        await auth_routes.register(
            auth_routes.RegisterRequest(
                username="new-user",
                email="new@example.com",
                password="secret",
            ),
            invitation_token="bad-token",
        )
    assert invalid_token.value.status_code == 400

    monkeypatch.setattr(
        auth_routes,
        "get_invitation",
        lambda token: SimpleNamespace(action="join_org", parameters="{}"),
    )
    with pytest.raises(HTTPException) as invalid_type:
        await auth_routes.register(
            auth_routes.RegisterRequest(
                username="new-user",
                email="new@example.com",
                password="secret",
            ),
            invitation_token="wrong-type",
        )
    assert invalid_type.value.status_code == 400

    monkeypatch.setattr(
        auth_routes,
        "get_invitation",
        lambda token: SimpleNamespace(action="register_account", parameters="{}"),
    )
    monkeypatch.setattr(
        auth_routes,
        "check_invitation_available",
        lambda invitation: (False, "invitation expired"),
    )
    with pytest.raises(HTTPException) as unavailable:
        await auth_routes.register(
            auth_routes.RegisterRequest(
                username="new-user",
                email="new@example.com",
                password="secret",
            ),
            invitation_token="expired",
        )
    assert unavailable.value.detail == "invitation expired"

    monkeypatch.setattr(auth_routes.cfg.auth, "invitation_only", False)
    with pytest.raises(HTTPException) as reserved:
        await auth_routes.register(
            auth_routes.RegisterRequest(
                username="api",
                email="new@example.com",
                password="secret",
            )
        )
    assert reserved.value.status_code == 400

    monkeypatch.setattr(auth_routes, "get_user_by_username", lambda username: SimpleNamespace())
    with pytest.raises(HTTPException) as username_exists:
        await auth_routes.register(
            auth_routes.RegisterRequest(
                username="new-user",
                email="new@example.com",
                password="secret",
            )
        )
    assert username_exists.value.detail == "Username already exists"

    monkeypatch.setattr(auth_routes, "get_user_by_username", lambda username: None)
    monkeypatch.setattr(auth_routes, "get_user_by_email", lambda email: SimpleNamespace())
    with pytest.raises(HTTPException) as email_exists:
        await auth_routes.register(
            auth_routes.RegisterRequest(
                username="new-user",
                email="new@example.com",
                password="secret",
            )
        )
    assert email_exists.value.detail == "Email already exists"

    monkeypatch.setattr(auth_routes, "get_user_by_email", lambda email: None)
    _FakeUserModel.get_or_none_responses = [SimpleNamespace(username="Taken-User", is_org=True)]
    with pytest.raises(HTTPException) as normalized_conflict:
        await auth_routes.register(
            auth_routes.RegisterRequest(
                username="Taken_User",
                email="new@example.com",
                password="secret",
            )
        )
    assert "organization: Taken-User" in normalized_conflict.value.detail
    assert atomic_state["entered"] >= 3


@pytest.mark.asyncio
async def test_register_covers_invitation_processing_and_email_verification(monkeypatch):
    atomic_state = {}
    create_email_calls = []
    used_invitations = []
    org_memberships = []
    user = SimpleNamespace(id=7, username="fresh-user", email="fresh@example.com")
    org = SimpleNamespace(id=42, username="org-team")

    invitation_valid = SimpleNamespace(
        action="register_account",
        parameters='{"org_id": 42, "org_name": "org-team", "role": "admin"}',
    )
    invitation_invalid_json = SimpleNamespace(
        action="register_account",
        parameters="{not-json",
    )

    async def _fake_to_thread(func, *args):
        return func(*args)

    monkeypatch.setattr(auth_routes, "User", _FakeUserModel)
    monkeypatch.setattr(
        auth_routes, "db", SimpleNamespace(atomic=lambda: _AtomicContext(atomic_state))
    )
    monkeypatch.setattr(auth_routes.cfg.auth, "invitation_only", True)
    monkeypatch.setattr(auth_routes.cfg.auth, "require_email_verification", True)
    monkeypatch.setattr(auth_routes, "normalize_name", lambda value: value.lower())
    monkeypatch.setattr(auth_routes, "get_user_by_username", lambda username: None)
    monkeypatch.setattr(auth_routes, "get_user_by_email", lambda email: None)
    monkeypatch.setattr(auth_routes, "hash_password", lambda password: f"hashed:{password}")
    monkeypatch.setattr(auth_routes, "create_user", lambda **kwargs: user)
    monkeypatch.setattr(auth_routes, "check_invitation_available", lambda invitation: (True, None))
    monkeypatch.setattr(auth_routes, "mark_invitation_used", lambda invitation, target: used_invitations.append((invitation, target.username)))
    monkeypatch.setattr(auth_routes, "get_user_by_id", lambda user_id: org if user_id == 42 else None)
    monkeypatch.setattr(auth_routes, "create_user_organization", lambda target, organization, role: org_memberships.append((target.username, organization.username, role)))
    monkeypatch.setattr(auth_routes, "generate_token", lambda: "verify-token")
    monkeypatch.setattr(auth_routes, "get_expiry_time", lambda hours: f"expiry:{hours}")
    monkeypatch.setattr(
        auth_routes,
        "create_email_verification",
        lambda **kwargs: create_email_calls.append(kwargs),
    )
    monkeypatch.setattr(auth_routes.asyncio, "to_thread", _fake_to_thread)

    invitations = [invitation_valid, invitation_valid]
    monkeypatch.setattr(auth_routes, "get_invitation", lambda token: invitations.pop(0))
    monkeypatch.setattr(auth_routes, "send_verification_email", lambda email, username, token: False)
    first = await auth_routes.register(
        auth_routes.RegisterRequest(
            username="fresh-user",
            email="fresh@example.com",
            password="secret",
        ),
        invitation_token="invite-1",
    )
    assert first == {
        "success": True,
        "message": "User created but failed to send verification email",
        "email_verified": False,
    }
    assert used_invitations == [(invitation_valid, "fresh-user")]
    assert org_memberships == [("fresh-user", "org-team", "admin")]

    invitations = [invitation_invalid_json, invitation_invalid_json]
    monkeypatch.setattr(auth_routes, "get_invitation", lambda token: invitations.pop(0))
    monkeypatch.setattr(auth_routes, "send_verification_email", lambda email, username, token: True)
    second = await auth_routes.register(
        auth_routes.RegisterRequest(
            username="fresh-user",
            email="fresh@example.com",
            password="secret",
        ),
        invitation_token="invite-2",
    )
    assert second == {
        "success": True,
        "message": "User created. Please check your email to verify your account.",
        "email_verified": False,
    }
    assert create_email_calls[-1] == {
        "user": user,
        "token": "verify-token",
        "expires_at": "expiry:24",
    }


@pytest.mark.asyncio
async def test_verify_login_logout_and_token_routes_cover_remaining_paths(monkeypatch):
    now = datetime.now(timezone.utc)
    atomic_state = {}
    session_creations = []
    created_tokens = []
    deleted_tokens = []

    active_user = SimpleNamespace(
        id=9,
        username="alice",
        email="alice@example.com",
        email_verified=True,
        password_hash="stored",
        is_active=True,
        created_at=now,
    )
    disabled_user = SimpleNamespace(
        id=10,
        username="disabled",
        email="disabled@example.com",
        email_verified=True,
        password_hash="stored",
        is_active=False,
        created_at=now,
    )
    unverified_user = SimpleNamespace(
        id=11,
        username="unverified",
        email="unverified@example.com",
        email_verified=False,
        password_hash="stored",
        is_active=True,
        created_at=now,
    )

    monkeypatch.setattr(auth_routes, "Session", _FakeSessionModel)
    monkeypatch.setattr(auth_routes, "Token", _FakeTokenModel)
    monkeypatch.setattr(
        auth_routes, "db", SimpleNamespace(atomic=lambda: _AtomicContext(atomic_state))
    )
    monkeypatch.setattr(auth_routes.cfg.auth, "session_expire_hours", 12)
    monkeypatch.setattr(auth_routes, "update_user", lambda user, **fields: None)
    monkeypatch.setattr(auth_routes, "delete_email_verification", lambda verification: None)
    monkeypatch.setattr(
        auth_routes,
        "create_session",
        lambda **kwargs: session_creations.append(kwargs),
    )
    monkeypatch.setattr(auth_routes, "get_expiry_time", lambda hours: f"expiry:{hours}")
    token_values = iter(["session-id", "login-session-id", "api-token"])
    monkeypatch.setattr(auth_routes, "generate_token", lambda: next(token_values))
    monkeypatch.setattr(auth_routes, "generate_session_secret", lambda: "session-secret")
    monkeypatch.setattr(
        auth_routes, "verify_password", lambda password, password_hash: password == "secret"
    )
    monkeypatch.setattr(
        auth_routes,
        "create_token",
        lambda **kwargs: created_tokens.append(kwargs) or SimpleNamespace(id=55),
    )
    monkeypatch.setattr(auth_routes, "hash_token", lambda token: f"hash:{token}")
    monkeypatch.setattr(auth_routes, "delete_token", lambda token_id: deleted_tokens.append(token_id))
    monkeypatch.setattr(
        auth_routes,
        "list_user_tokens",
        lambda user: [
            SimpleNamespace(id=1, name="first", last_used=None, created_at=now),
            SimpleNamespace(id=2, name="second", last_used=now, created_at=now),
        ],
    )
    monkeypatch.setattr(
        auth_routes,
        "get_email_verification",
        lambda token: {
            "missing-token": None,
            "expired-token": SimpleNamespace(
                expires_at=(now - timedelta(hours=1)).replace(tzinfo=None),
                user=active_user,
            ),
            "no-user-token": SimpleNamespace(
                expires_at=now + timedelta(hours=1),
                user=None,
            ),
            "valid-token": SimpleNamespace(
                expires_at=now + timedelta(hours=1),
                user=active_user,
            ),
        }[token],
    )

    invalid_verify = await auth_routes.verify_email("missing-token", Response())
    assert invalid_verify.headers["location"].startswith("/?error=invalid_token")

    expired_verify = await auth_routes.verify_email(
        "expired-token",
        Response(),
    )
    assert expired_verify.headers["location"].startswith("/?error=invalid_token")

    no_user_verify = await auth_routes.verify_email("no-user-token", Response())
    assert no_user_verify.headers["location"] == "/?error=user_not_found"

    success_verify = await auth_routes.verify_email("valid-token", Response())
    assert success_verify.headers["location"] == "/alice"
    assert any(header[0] == b"set-cookie" for header in success_verify.raw_headers)

    monkeypatch.setattr(
        auth_routes,
        "get_user_by_username",
        lambda username: {
            "alice": active_user,
            "disabled": disabled_user,
            "unverified": unverified_user,
        }.get(username),
    )

    with pytest.raises(HTTPException) as bad_login:
        await auth_routes.login(
            auth_routes.LoginRequest(username="alice", password="wrong"),
            Response(),
        )
    assert bad_login.value.status_code == 401

    with pytest.raises(HTTPException) as disabled_login:
        await auth_routes.login(
            auth_routes.LoginRequest(username="disabled", password="secret"),
            Response(),
        )
    assert disabled_login.value.detail == "Account is disabled"

    monkeypatch.setattr(auth_routes.cfg.auth, "require_email_verification", True)
    with pytest.raises(HTTPException) as unverified_login:
        await auth_routes.login(
            auth_routes.LoginRequest(username="unverified", password="secret"),
            Response(),
        )
    assert unverified_login.value.detail == "Please verify your email first"

    success_response = Response()
    monkeypatch.setattr(auth_routes.cfg.auth, "require_email_verification", False)
    login_payload = await auth_routes.login(
        auth_routes.LoginRequest(username="alice", password="secret"),
        success_response,
    )
    assert login_payload["session_secret"] == "session-secret"
    assert "session_id=login-session-id" in success_response.headers["set-cookie"]

    _FakeSessionModel.delete_query = _DeleteQuery(execute_result=3)
    logout_response = Response()
    logout_payload = await auth_routes.logout(logout_response, active_user)
    assert logout_payload["success"] is True

    me = auth_routes.get_me(active_user)
    assert me["username"] == "alice"

    listed_tokens = await auth_routes.list_tokens(active_user)
    assert listed_tokens["tokens"][0]["last_used"] is None
    assert listed_tokens["tokens"][1]["name"] == "second"

    _FakeSessionModel.get_or_none_responses = [SimpleNamespace(secret="browser-secret")]
    created = await auth_routes.create_token_endpoint(
        auth_routes.CreateTokenRequest(name="cli"),
        active_user,
    )
    assert created["session_secret"] == "browser-secret"
    assert created_tokens[-1] == {
        "user": active_user,
        "token_hash": "hash:api-token",
        "name": "cli",
    }

    _FakeTokenModel.get_or_none_responses = [None]
    with pytest.raises(HTTPException) as missing_token:
        await auth_routes.revoke_token(123, active_user)
    assert missing_token.value.status_code == 404

    _FakeTokenModel.get_or_none_responses = [SimpleNamespace(id=88)]
    revoked = await auth_routes.revoke_token(88, active_user)
    assert revoked["success"] is True
    assert deleted_tokens == [88]

    assert session_creations[0] == {
        "session_id": "session-id",
        "user": active_user,
        "secret": "session-secret",
        "expires_at": "expiry:12",
    }
    assert atomic_state["entered"] >= 1
