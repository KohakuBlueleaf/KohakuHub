"""Admin API endpoints for fallback source management."""

from datetime import datetime, timezone
from functools import partial
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuhub.db import FallbackSource, db
from kohakuhub.logger import get_logger
from kohakuhub.api.admin.utils.auth import verify_admin_token
from kohakuhub.api.fallback.cache import get_cache

logger = get_logger("ADMIN_FALLBACK")
router = APIRouter()


class FallbackSourceCreate(BaseModel):
    """Model for creating a fallback source."""

    namespace: str = ""  # "" for global, or user/org name
    url: str
    token: Optional[str] = None
    priority: int = 100
    name: str
    source_type: str  # "huggingface" or "kohakuhub"
    enabled: bool = True


class FallbackSourceUpdate(BaseModel):
    """Model for updating a fallback source."""

    url: Optional[str] = None
    token: Optional[str] = None
    priority: Optional[int] = None
    name: Optional[str] = None
    source_type: Optional[str] = None
    enabled: Optional[bool] = None


class FallbackSourceResponse(BaseModel):
    """Model for fallback source response."""

    id: int
    namespace: str
    url: str
    token: Optional[str]
    priority: int
    name: str
    source_type: str
    enabled: bool
    created_at: str
    updated_at: str


@router.post("/fallback-sources", response_model=FallbackSourceResponse)
async def create_fallback_source(
    payload: FallbackSourceCreate, _admin=Depends(verify_admin_token)
):
    """Create a new fallback source.

    Args:
        payload: Fallback source creation data
        _admin: Admin authentication dependency

    Returns:
        Created fallback source
    """
    # Validate source_type
    match payload.source_type:
        case "huggingface" | "kohakuhub":
            pass
        case _:
            raise HTTPException(
                400,
                detail={
                    "error": f"Invalid source_type: {payload.source_type}. Must be 'huggingface' or 'kohakuhub'"
                },
            )

    try:
        source = FallbackSource.create(
            namespace=payload.namespace,
            url=payload.url.rstrip("/"),
            token=payload.token,
            priority=payload.priority,
            name=payload.name,
            source_type=payload.source_type,
            enabled=payload.enabled,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )

        logger.info(f"Created fallback source: {source.name} ({source.url})")

        return FallbackSourceResponse(
            id=source.id,
            namespace=source.namespace,
            url=source.url,
            token=source.token,
            priority=source.priority,
            name=source.name,
            source_type=source.source_type,
            enabled=source.enabled,
            created_at=source.created_at.isoformat(),
            updated_at=source.updated_at.isoformat(),
        )

    except Exception as e:
        logger.error(f"Failed to create fallback source: {e}")
        raise HTTPException(500, detail={"error": f"Failed to create source: {str(e)}"})


@router.get("/fallback-sources", response_model=list[FallbackSourceResponse])
async def list_fallback_sources(
    namespace: Optional[str] = None,
    enabled: Optional[bool] = None,
    _admin=Depends(verify_admin_token),
):
    """List all fallback sources.

    Args:
        namespace: Filter by namespace (optional)
        enabled: Filter by enabled status (optional)
        _admin: Admin authentication dependency

    Returns:
        List of fallback sources
    """
    try:
        query = FallbackSource.select().order_by(FallbackSource.priority)

        if namespace is not None:
            query = query.where(FallbackSource.namespace == namespace)

        if enabled is not None:
            query = query.where(FallbackSource.enabled == enabled)

        sources = list(query)

        return [
            FallbackSourceResponse(
                id=s.id,
                namespace=s.namespace,
                url=s.url,
                token=s.token,
                priority=s.priority,
                name=s.name,
                source_type=s.source_type,
                enabled=s.enabled,
                created_at=s.created_at.isoformat(),
                updated_at=s.updated_at.isoformat(),
            )
            for s in sources
        ]

    except Exception as e:
        logger.error(f"Failed to list fallback sources: {e}")
        raise HTTPException(500, detail={"error": f"Failed to list sources: {str(e)}"})


@router.get("/fallback-sources/{source_id}", response_model=FallbackSourceResponse)
async def get_fallback_source(source_id: int, _admin=Depends(verify_admin_token)):
    """Get a specific fallback source.

    Args:
        source_id: Source ID
        _admin: Admin authentication dependency

    Returns:
        Fallback source
    """
    try:
        source = FallbackSource.get_by_id(source_id)

        return FallbackSourceResponse(
            id=source.id,
            namespace=source.namespace,
            url=source.url,
            token=source.token,
            priority=source.priority,
            name=source.name,
            source_type=source.source_type,
            enabled=source.enabled,
            created_at=source.created_at.isoformat(),
            updated_at=source.updated_at.isoformat(),
        )

    except FallbackSource.DoesNotExist:
        raise HTTPException(404, detail={"error": "Fallback source not found"})
    except Exception as e:
        logger.error(f"Failed to get fallback source: {e}")
        raise HTTPException(500, detail={"error": f"Failed to get source: {str(e)}"})


@router.put("/fallback-sources/{source_id}", response_model=FallbackSourceResponse)
async def update_fallback_source(
    source_id: int, payload: FallbackSourceUpdate, _admin=Depends(verify_admin_token)
):
    """Update a fallback source.

    Args:
        source_id: Source ID
        payload: Update data
        _admin: Admin authentication dependency

    Returns:
        Updated fallback source
    """
    try:
        source = FallbackSource.get_by_id(source_id)

        # Update fields if provided
        if payload.url is not None:
            source.url = payload.url.rstrip("/")
        if payload.token is not None:
            source.token = payload.token
        if payload.priority is not None:
            source.priority = payload.priority
        if payload.name is not None:
            source.name = payload.name
        if payload.source_type is not None:
            match payload.source_type:
                case "huggingface" | "kohakuhub":
                    source.source_type = payload.source_type
                case _:
                    raise HTTPException(
                        400,
                        detail={
                            "error": f"Invalid source_type: {payload.source_type}. Must be 'huggingface' or 'kohakuhub'"
                        },
                    )
        if payload.enabled is not None:
            source.enabled = payload.enabled

        source.updated_at = datetime.now(tz=timezone.utc)
        source.save()

        logger.info(f"Updated fallback source: {source.name} (ID: {source.id})")

        # Clear cache when source is updated
        cache = get_cache()
        cache.clear()
        logger.info("Cleared fallback cache after source update")

        return FallbackSourceResponse(
            id=source.id,
            namespace=source.namespace,
            url=source.url,
            token=source.token,
            priority=source.priority,
            name=source.name,
            source_type=source.source_type,
            enabled=source.enabled,
            created_at=source.created_at.isoformat(),
            updated_at=source.updated_at.isoformat(),
        )

    except FallbackSource.DoesNotExist:
        raise HTTPException(404, detail={"error": "Fallback source not found"})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update fallback source: {e}")
        raise HTTPException(500, detail={"error": f"Failed to update source: {str(e)}"})


@router.delete("/fallback-sources/{source_id}")
async def delete_fallback_source(source_id: int, _admin=Depends(verify_admin_token)):
    """Delete a fallback source.

    Args:
        source_id: Source ID
        _admin: Admin authentication dependency

    Returns:
        Success message
    """
    try:
        source = FallbackSource.get_by_id(source_id)
        source_name = source.name

        source.delete_instance()

        logger.info(f"Deleted fallback source: {source_name} (ID: {source_id})")

        # Clear cache when source is deleted
        cache = get_cache()
        cache.clear()
        logger.info("Cleared fallback cache after source deletion")

        return {"success": True, "message": f"Fallback source {source_name} deleted"}

    except FallbackSource.DoesNotExist:
        raise HTTPException(404, detail={"error": "Fallback source not found"})
    except Exception as e:
        logger.error(f"Failed to delete fallback source: {e}")
        raise HTTPException(500, detail={"error": f"Failed to delete source: {str(e)}"})


@router.get("/fallback-sources/cache/stats")
async def get_cache_stats(_admin=Depends(verify_admin_token)):
    """Get fallback cache statistics.

    Args:
        _admin: Admin authentication dependency

    Returns:
        Cache statistics
    """
    try:
        cache = get_cache()
        stats = cache.stats()

        return {
            "size": stats["size"],
            "maxsize": stats["maxsize"],
            "ttl_seconds": stats["ttl_seconds"],
            "usage_percent": (
                round((stats["size"] / stats["maxsize"]) * 100, 2)
                if stats["maxsize"] > 0
                else 0
            ),
        }

    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        raise HTTPException(500, detail={"error": f"Failed to get stats: {str(e)}"})


@router.delete("/fallback-sources/cache/clear")
async def clear_cache(_admin=Depends(verify_admin_token)):
    """Clear the fallback cache.

    Args:
        _admin: Admin authentication dependency

    Returns:
        Success message
    """
    try:
        cache = get_cache()
        old_size = cache.stats()["size"]
        cache.clear()

        logger.info(f"Cleared fallback cache (was {old_size} entries)")

        return {
            "success": True,
            "message": f"Cache cleared ({old_size} entries removed)",
            "old_size": old_size,
        }

    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(500, detail={"error": f"Failed to clear cache: {str(e)}"})
