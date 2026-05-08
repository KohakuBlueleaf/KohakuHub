"""Unit tests for the admin cache stats / metrics-reset routes.

These exercise the route functions directly (not through HTTP) so they
can run on any matrix without spinning up the full backend service. The
``verify_admin_token`` dependency is bypassed by calling the function
with the ``_admin=True`` keyword the FastAPI ``Depends`` would resolve
to in production.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import kohakuhub.api.admin.routers.cache as admin_cache


@pytest.mark.asyncio
async def test_get_cache_stats_returns_metrics_and_memory_snapshot():
    """Happy path: ``get_cache_stats`` proxies the in-process snapshot
    plus a Valkey memory probe into a single payload — ready to consume
    by the admin UI without further fan-out.
    """
    fake_metrics = {
        "configured_enabled": True,
        "client_initialized": True,
        "namespace": "kh",
        "hits": {"lakefs": 1},
        "misses": {},
        "errors": {},
        "set_count": {"lakefs": 1},
        "invalidate_count": {},
        "singleflight_contention": 0,
        "last_flush_run_id": "rid",
        "last_flush_at_ms": 1700000000000,
        "last_flushed_keys": 0,
    }
    fake_memory = {
        "available": True,
        "used_memory": 4194304,
        "used_memory_human": "4.00M",
        "maxmemory": 0,
        "maxmemory_policy": "allkeys-lfu",
        "evicted_keys": None,
    }

    with patch.object(admin_cache, "get_metrics_snapshot", return_value=fake_metrics), \
         patch.object(
             admin_cache, "get_memory_info", new=AsyncMock(return_value=fake_memory)
         ):
        result = await admin_cache.get_cache_stats(_admin=True)

    assert result == {"metrics": fake_metrics, "memory": fake_memory}


@pytest.mark.asyncio
async def test_reset_cache_metrics_succeeds_when_enabled():
    """When the cache is operational, the reset endpoint clears counters
    via the cache module's ``reset_metrics`` helper and returns a
    confirmation. We assert ``reset_metrics`` is invoked because that
    is the unit of behavior the route promises.
    """
    with patch.object(admin_cache, "is_enabled", return_value=True), \
         patch.object(admin_cache, "reset_metrics") as reset:
        result = await admin_cache.reset_cache_metrics(_admin=True)
    assert result == {"reset": True}
    reset.assert_called_once()


@pytest.mark.asyncio
async def test_reset_cache_metrics_returns_409_when_disabled():
    """When ``is_enabled`` is False the route must NOT call
    ``reset_metrics`` (would silently no-op anyway, but raising 409
    surfaces the misconfiguration to the operator).
    """
    with patch.object(admin_cache, "is_enabled", return_value=False), \
         patch.object(admin_cache, "reset_metrics") as reset:
        with pytest.raises(HTTPException) as exc:
            await admin_cache.reset_cache_metrics(_admin=True)
    assert exc.value.status_code == 409
    reset.assert_not_called()
