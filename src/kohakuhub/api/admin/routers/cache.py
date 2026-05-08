"""Admin endpoints for inspecting the L2 cache layer.

Exposes hit/miss/error counters per cache namespace, Valkey memory
utilization, eviction count, and the bootstrap-flush metadata. These are
operational signals — they are reset on API process restart by design.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuhub.api.admin.utils import verify_admin_token
from kohakuhub.cache import (
    get_memory_info,
    get_metrics_snapshot,
    is_enabled,
    reset_metrics,
)
from kohakuhub.logger import get_logger

logger = get_logger("ADMIN")
router = APIRouter()


@router.get("/cache/stats")
async def get_cache_stats(_admin: bool = Depends(verify_admin_token)):
    """Return cache hit/miss/error counters and Valkey memory state."""
    metrics = get_metrics_snapshot()
    memory = await get_memory_info()
    return {
        "metrics": metrics,
        "memory": memory,
    }


@router.post("/cache/metrics/reset")
async def reset_cache_metrics(_admin: bool = Depends(verify_admin_token)):
    """Zero out the in-process metric counters.

    Useful when you want to measure the effect of a configuration change
    without restarting the API process. Does NOT touch cache contents.
    """
    if not is_enabled():
        raise HTTPException(
            status_code=409,
            detail="Cache is not enabled / not initialized",
        )
    reset_metrics()
    return {"reset": True}
