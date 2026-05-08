"""Dependency health endpoint for the admin dashboard."""

import time

from fastapi import APIRouter, Depends, Query

from kohakuhub.api.admin.utils import verify_admin_token
from kohakuhub.api.admin.utils.health import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    run_all_probes,
)
from kohakuhub.logger import get_logger

logger = get_logger("ADMIN")
router = APIRouter()


@router.get("/health/dependencies")
async def get_dependency_health(
    timeout_seconds: float = Query(
        default=DEFAULT_PROBE_TIMEOUT_SECONDS,
        ge=0.5,
        le=10.0,
        description="Per-probe timeout in seconds.",
    ),
    _admin: bool = Depends(verify_admin_token),
):
    """Probe Postgres, MinIO, LakeFS and (optionally) SMTP in parallel.

    Returns:
        Aggregated probe results plus a derived overall status:
        ``ok`` (every enabled dependency is up), ``degraded`` (one or more
        ``down``), or ``disabled`` (a probe was disabled by configuration).
    """
    start = time.perf_counter()
    dependencies = await run_all_probes(timeout=timeout_seconds)

    statuses = {dep["status"] for dep in dependencies}
    if "down" in statuses:
        overall = "degraded"
    elif statuses == {"disabled"}:
        overall = "disabled"
    else:
        overall = "ok"

    return {
        "overall_status": overall,
        "checked_at_ms": int(time.time() * 1000),
        "elapsed_ms": int((time.perf_counter() - start) * 1000),
        "timeout_seconds": timeout_seconds,
        "dependencies": dependencies,
    }
