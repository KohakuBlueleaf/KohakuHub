"""API endpoints for managing user external fallback tokens."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuhub.config import cfg
from kohakuhub.constants import ERROR_NOT_AUTHORIZED_MANAGE_TOKENS
from kohakuhub.crypto import mask_token
from kohakuhub.db import FallbackSource, User
from kohakuhub.db_operations import (
    delete_user_external_token,
    get_user_by_username,
    get_user_external_tokens,
    set_user_external_token,
)
from kohakuhub.logger import get_logger
from kohakuhub.utils.datetime_utils import safe_isoformat
from kohakuhub.auth.dependencies import get_current_user
from kohakuhub.api.fallback.cache import get_cache

router = APIRouter()
logger = get_logger("AUTH_EXT_TOKENS")


# ===== Request/Response Models =====


class ExternalTokenRequest(BaseModel):
    """Request to add/update external token."""

    url: str  # Base URL (e.g., "https://huggingface.co")
    token: str  # Plain text token


class BulkExternalTokensRequest(BaseModel):
    """Request to bulk update external tokens."""

    tokens: list[ExternalTokenRequest]


class ExternalTokenResponse(BaseModel):
    """Response with external token info (token is masked)."""

    url: str
    token_preview: str  # Masked token (e.g., "hf_a***")
    created_at: str
    updated_at: str


# ===== Endpoints =====


@router.get("/fallback-sources/available")
async def get_available_fallback_sources() -> list[dict]:
    """Get list of available fallback sources (global + DB, without tokens).

    This endpoint is public - no authentication required.
    Returns source URLs and names for users to configure their own tokens.
    """
    sources = []

    # Load from config (environment variables)
    for source_dict in cfg.fallback.sources:
        sources.append(
            {
                "url": source_dict.get("url", ""),
                "name": source_dict.get("name", "Unknown"),
                "source_type": source_dict.get("source_type", "huggingface"),
                "priority": source_dict.get("priority", 100),
            }
        )

    # Load global sources from database
    try:
        db_sources = (
            FallbackSource.select()
            .where(FallbackSource.namespace == "", FallbackSource.enabled == True)
            .order_by(FallbackSource.priority)
        )

        for source in db_sources:
            sources.append(
                {
                    "url": source.url,
                    "name": source.name,
                    "source_type": source.source_type,
                    "priority": source.priority,
                }
            )
    except Exception as e:
        logger.warning(f"Failed to load sources from database: {e}")

    # Remove duplicates by URL
    seen_urls = set()
    unique_sources = []
    for source in sources:
        if source["url"] not in seen_urls:
            seen_urls.add(source["url"])
            unique_sources.append(source)

    # Sort by priority
    unique_sources.sort(key=lambda s: s["priority"])

    return unique_sources


@router.get("/users/{username}/external-tokens")
async def list_external_tokens(
    username: str, user: User = Depends(get_current_user)
) -> list[ExternalTokenResponse]:
    """List user's external fallback tokens.

    Tokens are masked for security (only first 4 chars shown).
    """
    # Check user can only access their own tokens
    if user.username != username:
        raise HTTPException(403, detail="Not authorized to view these tokens")

    tokens = get_user_external_tokens(user)

    return [
        ExternalTokenResponse(
            url=token["url"],
            token_preview=mask_token(token["token"]),
            created_at=safe_isoformat(token["created_at"]),
            updated_at=safe_isoformat(token["updated_at"]),
        )
        for token in tokens
    ]


@router.post("/users/{username}/external-tokens")
async def add_external_token(
    username: str, data: ExternalTokenRequest, user: User = Depends(get_current_user)
) -> dict:
    """Add or update external token for a URL.

    If token already exists for this URL, it will be updated.
    """
    # Check user can only manage their own tokens
    if user.username != username:
        raise HTTPException(403, detail=ERROR_NOT_AUTHORIZED_MANAGE_TOKENS)

    # Validate URL
    if not data.url or not data.url.startswith("http"):
        raise HTTPException(400, detail="Invalid URL format")

    # Add/update token
    set_user_external_token(user, data.url, data.token)

    # Strict-freshness invalidation (#79): a per-user token mutation
    # changes the user's effective fallback chain auth. Drop every
    # cached binding for this user (across all repos) and bump the
    # user's generation so any in-flight probe's safe_set is rejected.
    evicted = get_cache().clear_user(user.id)

    logger.info(
        f"User {username} set external token for {data.url} "
        f"(cache evicted {evicted} entries)"
    )

    return {"success": True, "message": "External token saved"}


@router.delete("/users/{username}/external-tokens/{url:path}")
async def delete_external_token(
    username: str, url: str, user: User = Depends(get_current_user)
) -> dict:
    """Delete external token for a URL."""
    # Check user can only manage their own tokens
    if user.username != username:
        raise HTTPException(403, detail=ERROR_NOT_AUTHORIZED_MANAGE_TOKENS)

    # Delete token
    deleted = delete_user_external_token(user, url)

    if not deleted:
        raise HTTPException(404, detail="Token not found for this URL")

    # Strict-freshness invalidation (#79): see add_external_token comment.
    evicted = get_cache().clear_user(user.id)

    logger.info(
        f"User {username} deleted external token for {url} "
        f"(cache evicted {evicted} entries)"
    )

    return {"success": True, "message": "External token deleted"}


@router.put("/users/{username}/external-tokens/bulk")
async def bulk_update_external_tokens(
    username: str,
    data: BulkExternalTokensRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Bulk update external tokens.

    Replaces all existing tokens with the provided list.
    """
    # Check user can only manage their own tokens
    if user.username != username:
        raise HTTPException(403, detail=ERROR_NOT_AUTHORIZED_MANAGE_TOKENS)

    # Get existing tokens
    existing_tokens = get_user_external_tokens(user)
    existing_urls = {token["url"] for token in existing_tokens}

    # URLs in new data
    new_urls = {token.url for token in data.tokens}

    # Delete tokens that are no longer in the list
    for url in existing_urls - new_urls:
        delete_user_external_token(user, url)

    # Add/update tokens from the new list
    for token_data in data.tokens:
        if not token_data.url or not token_data.url.startswith("http"):
            raise HTTPException(400, detail=f"Invalid URL format: {token_data.url}")

        set_user_external_token(user, token_data.url, token_data.token)

    # Strict-freshness invalidation (#79): bulk replace is the
    # "every per-source token may have changed" event; drop every
    # cached binding for this user and bump the user generation.
    evicted = get_cache().clear_user(user.id)

    logger.info(
        f"User {username} bulk updated {len(data.tokens)} external tokens "
        f"(cache evicted {evicted} entries)"
    )

    return {
        "success": True,
        "message": f"Updated {len(data.tokens)} external tokens",
    }
