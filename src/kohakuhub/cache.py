"""L2 cache layer (Valkey/Redis-compatible).

This module is the **only** entry point for cache reads/writes. Hand-rolled
``redis.asyncio`` calls outside this file will bypass:

- TTL jitter (stampede prevention)
- Two-level singleflight (cross-worker + intra-worker)
- Silent-degradation contract (a flaky Valkey must produce cache misses,
  never request errors)
- Namespace metrics (admin observability)
- Boot-time flush coordinator (Mode-B namespaces are wiped on every Valkey
  restart, regardless of API restarts; Mode-A namespaces survive)

See ``docs/development/cache.md`` for the full design (L1/L2/L3 architecture,
Mode-A immutable / Mode-B1 generation / Mode-B2 write-through, persistence
+ flush rationale).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis
from cachetools import LRUCache

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger

logger = get_logger("CACHE")

# Sentinel used to encode "verified absent" without colliding with a real
# JSON-encoded ``null``. The negative cache lets us short-circuit repeated
# get_repository("ghost-namespace/ghost-repo") lookups without hitting the
# DB on every probe (anti-DoS for scrapers).
_NEGATIVE_SENTINEL = "__kh_neg_v1__"

# Mode-B namespace prefixes that are flushed on every Valkey restart.
#
# These hold either:
# - mutable state with write-through DEL (sess, tok, lakefs:branch, …) — a
#   write that lands while Valkey is down loses its DEL, and after RDB
#   restore the entry is silently stale. Flushing on boot bounds the
#   staleness window.
# - generation counters (repo:gen, org:gen, user:gen, list_gen) — the
#   counter's source of truth is the Postgres ``cache_gen`` column once
#   that lands. Flushing forces a fresh read from DB.
# - generation-keyed derived caches (repo:info, org:info, user:info,
#   uo:*, list:*) — old gen suffixes are unreachable anyway, but flushing
#   reclaims memory immediately rather than waiting for LFU.
# - negative cache (negative:*) — write-through DEL on entity creation
#   would be lost on a Valkey-down window.
#
# Mode-A namespaces (lakefs:commit, lakefs:stat, lakefs:list — keyed by
# commit_id) are intentionally NOT in this list. They are content-addressed
# and survive across restarts safely; that is the entire reason we accept
# the persistence cost.
MODE_B_PREFIXES: tuple[str, ...] = (
    "lakefs:branch:",
    "repo:gen:",
    "repo:info:",
    "org:gen:",
    "org:info:",
    "user:gen:",
    "user:info:",
    "uo:",
    "sess:",
    "tok:",
    "list_gen:",
    "list:",
    "negative:",
)

BOOTSTRAP_LOCK_KEY = "cache:bootstrap_lock"
BOOTSTRAP_RUN_ID_KEY = "cache:bootstrap_run_id"
BOOTSTRAP_LOCK_TTL_SECONDS = 30
BOOTSTRAP_WAIT_SLOP_SECONDS = 5
SINGLEFLIGHT_LOCK_TTL_SECONDS = 5
SINGLEFLIGHT_POLL_INTERVAL = 0.05
SCAN_BATCH_SIZE = 500


# ----------------------------------------------------------------------------
# Module state
# ----------------------------------------------------------------------------


@dataclass
class CacheMetrics:
    """In-process counters for the admin observability endpoint.

    Reset on process restart by design — these are operational signals, not
    business data.
    """

    hits: dict[str, int] = field(default_factory=dict)
    misses: dict[str, int] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    set_count: dict[str, int] = field(default_factory=dict)
    invalidate_count: dict[str, int] = field(default_factory=dict)
    singleflight_contention: int = 0
    last_flush_run_id: str | None = None
    last_flush_at_ms: int | None = None
    last_flushed_keys: int = 0


_metrics = CacheMetrics()
_pool: aioredis.ConnectionPool | None = None
_client: aioredis.Redis | None = None
_local_locks: LRUCache[str, asyncio.Lock] = LRUCache(maxsize=4096)


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _ns(key: str) -> str:
    """Group metrics by the first ``:`` segment of the key."""
    head = key.split(":", 1)[0]
    return head or "<unknown>"


def _prefixed(key: str) -> str:
    """Apply the configured namespace prefix.

    Putting *every* key behind one prefix lets multiple deployments share a
    Valkey cluster without colliding, and lets tests use a per-session
    namespace to isolate from any leftover state.
    """
    return f"{cfg.cache.namespace}:{key}"


def _jittered_ttl(ttl: int) -> int:
    """Apply ±jitter to TTL to spread expiry across keys.

    Without this, a burst of writes that share a TTL all expire at the same
    instant — the classic synchronized-stampede pattern.
    """
    if cfg.cache.jitter_fraction <= 0 or ttl <= 1:
        return max(1, int(ttl))
    delta = ttl * cfg.cache.jitter_fraction
    return max(1, int(ttl + random.uniform(-delta, delta)))


def _local_lock_for(key: str) -> asyncio.Lock:
    """Per-key in-process lock for intra-worker singleflight."""
    lock = _local_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _local_locks[key] = lock
    return lock


# ----------------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------------


async def init_cache() -> None:
    """Initialize the connection pool and run the boot-time flush coordinator.

    Safe to call multiple times; second call is a no-op. Failures here MUST
    NOT propagate — silent degradation is the contract.
    """
    global _pool, _client

    if _client is not None:
        return

    if not cfg.cache.enabled:
        logger.info(
            "Cache disabled (KOHAKU_HUB_CACHE_ENABLED=false); "
            "all cache calls will silently fall back to source"
        )
        return

    _pool = aioredis.ConnectionPool.from_url(
        cfg.cache.url,
        max_connections=cfg.cache.max_connections,
        socket_timeout=cfg.cache.socket_timeout_seconds,
        socket_connect_timeout=cfg.cache.socket_connect_timeout_seconds,
        decode_responses=True,
    )
    _client = aioredis.Redis(connection_pool=_pool)

    try:
        pong = await _client.ping()
        if pong:
            logger.info(f"Cache connected: {cfg.cache.url}")
    except Exception as e:
        # Stay enabled but with a hot client that will fail on every op —
        # silent-degradation contract handles that path. We do not unset
        # ``_client`` here; callers expect ``cache.enabled in metrics`` to
        # reflect the configured intent.
        logger.warning(
            f"Cache configured ({cfg.cache.url}) but PING failed: {e}; "
            f"operating in degraded mode"
        )
        return

    try:
        await _bootstrap_flush()
    except Exception as e:
        logger.warning(
            f"Cache bootstrap flush failed: {e}; "
            f"continuing with possibly-stale Mode-B keys (TTLs will bound staleness)"
        )


async def close_cache() -> None:
    """Tear down the connection pool. Safe to call when never initialized."""
    global _pool, _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None
    if _pool is not None:
        try:
            await _pool.aclose()
        except Exception:
            pass
        _pool = None


def _get_client() -> aioredis.Redis | None:
    """Return the live client, or None if cache is disabled / not initialized."""
    return _client


def is_enabled() -> bool:
    """Cache is *configured* enabled. Use ``is_available`` for live status."""
    return cfg.cache.enabled and _client is not None


# ----------------------------------------------------------------------------
# Boot-time flush coordinator
# ----------------------------------------------------------------------------


async def _bootstrap_flush() -> None:
    """Flush Mode-B namespaces if Valkey was restarted since last seen ``run_id``.

    ``run_id`` is a Valkey-internal value that changes on every Valkey
    process start. By recording the last-seen value (in Valkey itself, not
    on the API host) we get restart detection that is independent of API
    restarts — restarting the API against a long-running Valkey is a no-op
    for this routine, while a Valkey upgrade flushes Mode-B exactly once
    across all API workers.

    Only one worker performs the flush per detection event. Losers wait
    for the marker to update before returning, so they don't serve Mode-B
    reads against potentially-stale data.
    """
    client = _get_client()
    if client is None:
        return

    info = await client.info("server")
    run_id = info.get("run_id") if isinstance(info, dict) else None
    if not run_id:
        logger.warning(
            "Cache bootstrap: Valkey did not report run_id; "
            "skipping flush (Mode-B keys may be stale across restart)"
        )
        return

    # Namespace the coordinator sentinels too, so multiple deployments
    # sharing a Valkey run independent flush cycles instead of fighting
    # over the same key.
    run_id_key = _prefixed(BOOTSTRAP_RUN_ID_KEY)
    lock_key = _prefixed(BOOTSTRAP_LOCK_KEY)

    seen = await client.get(run_id_key)
    if seen == run_id:
        logger.info(
            f"Cache bootstrap: run_id unchanged ({_short(run_id)}); no flush needed"
        )
        return

    got_lock = bool(
        await client.set(lock_key, "1", nx=True, ex=BOOTSTRAP_LOCK_TTL_SECONDS)
    )
    if not got_lock:
        logger.info(
            f"Cache bootstrap: another worker is flushing; "
            f"waiting for run_id={_short(run_id)} marker..."
        )
        deadline = time.monotonic() + BOOTSTRAP_LOCK_TTL_SECONDS + BOOTSTRAP_WAIT_SLOP_SECONDS
        while time.monotonic() < deadline:
            seen = await client.get(run_id_key)
            if seen == run_id:
                logger.info("Cache bootstrap: peer flush completed")
                return
            await asyncio.sleep(0.1)
        logger.warning(
            "Cache bootstrap: timed out waiting for peer flush; proceeding anyway"
        )
        return

    logger.info(
        f"Cache bootstrap: detected new Valkey run_id ({_short(run_id)}); "
        f"flushing Mode-B namespaces..."
    )
    flushed = 0
    try:
        for prefix in MODE_B_PREFIXES:
            flushed += await _scan_delete(_prefixed(prefix) + "*")
        await client.set(run_id_key, run_id)
        _metrics.last_flush_run_id = run_id
        _metrics.last_flush_at_ms = int(time.time() * 1000)
        _metrics.last_flushed_keys = flushed
        logger.info(
            f"Cache bootstrap: flushed {flushed} Mode-B keys "
            f"across {len(MODE_B_PREFIXES)} namespaces"
        )
    finally:
        try:
            await client.delete(lock_key)
        except Exception:
            pass


def _short(s: str | None) -> str:
    if not s:
        return "<none>"
    return s[:12] + ("..." if len(s) > 12 else "")


# ----------------------------------------------------------------------------
# SCAN-based delete (never KEYS — it blocks the whole Valkey)
# ----------------------------------------------------------------------------


async def _scan_delete(pattern: str, batch: int = SCAN_BATCH_SIZE) -> int:
    """Delete all keys matching ``pattern``.

    Uses SCAN + DEL in batches. Never use KEYS in production: it does an
    O(N) blocking iteration over the whole keyspace.
    """
    client = _get_client()
    if client is None:
        return 0

    cursor = 0
    deleted = 0
    while True:
        cursor, keys = await client.scan(cursor=cursor, match=pattern, count=batch)
        if keys:
            deleted += await client.delete(*keys)
        if cursor == 0:
            break
    return deleted


# ----------------------------------------------------------------------------
# Public read/write API
# ----------------------------------------------------------------------------


async def _get_raw(key: str) -> tuple[bool, Any]:
    """Internal read helper (no metric counting).

    Used by ``cache_get_or_fetch`` to re-check inside the singleflight lock
    without double-counting metrics.
    """
    client = _get_client()
    if client is None:
        return False, None
    try:
        raw = await client.get(_prefixed(key))
    except Exception as e:
        _bump(_metrics.errors, _ns(key))
        logger.debug(f"cache get error for {key}: {e}")
        return False, None
    if raw is None:
        return False, None
    if raw == _NEGATIVE_SENTINEL:
        return True, None
    try:
        return True, json.loads(raw)
    except json.JSONDecodeError as e:
        _bump(_metrics.errors, _ns(key))
        logger.debug(f"cache decode error for {key}: {e}")
        return False, None


async def cache_get_json(key: str) -> tuple[bool, Any]:
    """Read a cached JSON value.

    Returns ``(hit, value)``. ``hit=True, value=None`` means the negative
    sentinel was found (caller should treat as "verified absent"). Errors,
    cache disabled, and decode failures all collapse to ``(False, None)``.
    """
    hit, value = await _get_raw(key)
    if hit:
        _bump(_metrics.hits, _ns(key))
    else:
        _bump(_metrics.misses, _ns(key))
    return hit, value


async def cache_set_json(key: str, value: Any, ttl: int | None = None) -> bool:
    """Store ``value`` as JSON under ``key`` with a jittered TTL.

    Returns True on success, False on any failure (silent degradation).
    """
    client = _get_client()
    if client is None:
        return False
    actual_ttl = _jittered_ttl(
        ttl if ttl is not None else cfg.cache.default_ttl_seconds
    )
    try:
        payload = json.dumps(value)
    except (TypeError, ValueError) as e:
        _bump(_metrics.errors, _ns(key))
        logger.debug(f"cache encode error for {key}: {e}")
        return False
    try:
        await client.set(_prefixed(key), payload, ex=actual_ttl)
        _bump(_metrics.set_count, _ns(key))
        return True
    except Exception as e:
        _bump(_metrics.errors, _ns(key))
        logger.debug(f"cache_set_json error for {key}: {e}")
        return False


async def cache_set_negative(key: str, ttl: int = 15) -> bool:
    """Store the negative sentinel — encodes "verified absent"."""
    client = _get_client()
    if client is None:
        return False
    try:
        await client.set(_prefixed(key), _NEGATIVE_SENTINEL, ex=_jittered_ttl(ttl))
        _bump(_metrics.set_count, _ns(key))
        return True
    except Exception as e:
        _bump(_metrics.errors, _ns(key))
        logger.debug(f"cache_set_negative error for {key}: {e}")
        return False


async def cache_invalidate(*keys: str) -> int:
    """Delete one or more keys. Returns count of keys actually removed."""
    client = _get_client()
    if client is None or not keys:
        return 0
    try:
        n = await client.delete(*[_prefixed(k) for k in keys])
        for k in keys:
            _bump(_metrics.invalidate_count, _ns(k))
        return int(n)
    except Exception as e:
        for k in keys:
            _bump(_metrics.errors, _ns(k))
        logger.debug(f"cache_invalidate error: {e}")
        return 0


async def cache_invalidate_prefix(prefix: str) -> int:
    """Delete all keys starting with ``prefix`` (SCAN-based).

    Use with care — a wide prefix sweeps many keys. Each Mode-B
    namespace flush goes through this path on Valkey restart.
    """
    if _get_client() is None:
        return 0
    try:
        n = await _scan_delete(_prefixed(prefix) + "*")
        _bump(_metrics.invalidate_count, _ns(prefix))
        return n
    except Exception as e:
        _bump(_metrics.errors, _ns(prefix))
        logger.debug(f"cache_invalidate_prefix error: {e}")
        return 0


# ----------------------------------------------------------------------------
# Singleflight + get-or-fetch
# ----------------------------------------------------------------------------


async def cache_get_or_fetch(
    key: str,
    fetch: Callable[[], Awaitable[Any]],
    *,
    ttl: int | None = None,
    negative_ttl: int = 15,
    treat_none_as_negative: bool = False,
) -> Any:
    """Read-through cache with two-level singleflight + silent degradation.

    Behavior:

    1. Cache hit → return value (None means negative-sentinel "verified absent").
    2. Cache miss → per-key in-process lock + Valkey ``SET NX EX`` lock; one
       awaiter calls ``fetch()``, others poll the cache for the resulting fill.
    3. ``fetch()`` raises → propagate (cache is not poisoned). Caller decides.
    4. Cache layer down → ``fetch()`` is invoked every call (silent degradation).

    Set ``treat_none_as_negative=True`` to cache "this entity does not exist"
    under ``negative_ttl``; subsequent lookups return ``None`` without invoking
    ``fetch()`` until the negative TTL expires or the key is invalidated.
    Critical: when the entity is later created, callers MUST invalidate this
    key explicitly — otherwise the negative result hides the new entity until
    TTL expiry.
    """
    hit, value = await _get_raw(key)
    if hit:
        _bump(_metrics.hits, _ns(key))
        return value
    _bump(_metrics.misses, _ns(key))

    local_lock = _local_lock_for(key)
    async with local_lock:
        # Re-check inside the lock: another coroutine in the same worker
        # may have populated the cache while we were waiting.
        hit, value = await _get_raw(key)
        if hit:
            _bump(_metrics.hits, _ns(key))
            _metrics.singleflight_contention += 1
            return value

        client = _get_client()
        cross_lock_key = _prefixed(f"sf:lock:{key}")
        got_cross_lock = False
        if client is not None:
            try:
                got_cross_lock = bool(
                    await client.set(
                        cross_lock_key,
                        "1",
                        nx=True,
                        ex=SINGLEFLIGHT_LOCK_TTL_SECONDS,
                    )
                )
            except Exception:
                got_cross_lock = False

        if not got_cross_lock and client is not None:
            # Another worker is fetching — poll for the cache fill rather
            # than duplicating the round-trip to the source.
            _metrics.singleflight_contention += 1
            deadline = time.monotonic() + SINGLEFLIGHT_LOCK_TTL_SECONDS
            while time.monotonic() < deadline:
                hit, value = await _get_raw(key)
                if hit:
                    _bump(_metrics.hits, _ns(key))
                    return value
                await asyncio.sleep(SINGLEFLIGHT_POLL_INTERVAL)
            # Timed out waiting — fall through and fetch ourselves rather
            # than blocking the request indefinitely.

        try:
            result = await fetch()
        except Exception:
            # Don't poison the cache on fetch errors. Release the cross
            # lock so the next caller doesn't have to wait for its TTL.
            if got_cross_lock and client is not None:
                try:
                    await client.delete(cross_lock_key)
                except Exception:
                    pass
            raise

        if result is None and treat_none_as_negative:
            await cache_set_negative(key, ttl=negative_ttl)
        else:
            await cache_set_json(key, result, ttl=ttl)

        if got_cross_lock and client is not None:
            try:
                await client.delete(cross_lock_key)
            except Exception:
                pass

        return result


# ----------------------------------------------------------------------------
# Generation counters
# ----------------------------------------------------------------------------


async def bump_gen(scope_key: str) -> int:
    """Atomically INCR a Valkey-side generation counter.

    Used to invalidate all derived caches whose key embeds the gen, without
    enumerating the derived keys. ``scope_key`` should be ``repo:gen:{id}``,
    ``user:gen:{id}``, etc. — the prefix matters: it must be in
    ``MODE_B_PREFIXES`` so a Valkey restart resets these counters and the
    SQL ``cache_gen`` column becomes the authoritative source.

    Returns the new value. Returns 0 when cache is unavailable; callers
    should still proceed (the gen-keyed cache will simply not be hit).
    """
    client = _get_client()
    if client is None:
        return 0
    try:
        return int(await client.incr(_prefixed(scope_key)))
    except Exception as e:
        _bump(_metrics.errors, _ns(scope_key))
        logger.debug(f"bump_gen error for {scope_key}: {e}")
        return 0


async def read_gen(scope_key: str) -> int:
    """Read the current generation. Returns 0 when not yet bumped or cache off."""
    client = _get_client()
    if client is None:
        return 0
    try:
        v = await client.get(_prefixed(scope_key))
        return int(v) if v is not None else 0
    except (Exception,) as e:
        _bump(_metrics.errors, _ns(scope_key))
        logger.debug(f"read_gen error for {scope_key}: {e}")
        return 0


# ----------------------------------------------------------------------------
# Observability
# ----------------------------------------------------------------------------


def get_metrics_snapshot() -> dict:
    """Return a snapshot of in-process cache metrics for the admin endpoint."""
    return {
        "configured_enabled": cfg.cache.enabled,
        "client_initialized": _client is not None,
        "namespace": cfg.cache.namespace,
        "hits": dict(_metrics.hits),
        "misses": dict(_metrics.misses),
        "errors": dict(_metrics.errors),
        "set_count": dict(_metrics.set_count),
        "invalidate_count": dict(_metrics.invalidate_count),
        "singleflight_contention": _metrics.singleflight_contention,
        "last_flush_run_id": _metrics.last_flush_run_id,
        "last_flush_at_ms": _metrics.last_flush_at_ms,
        "last_flushed_keys": _metrics.last_flushed_keys,
    }


def reset_metrics() -> None:
    """For tests: zero out all counters."""
    _metrics.hits.clear()
    _metrics.misses.clear()
    _metrics.errors.clear()
    _metrics.set_count.clear()
    _metrics.invalidate_count.clear()
    _metrics.singleflight_contention = 0


async def get_memory_info() -> dict:
    """Probe Valkey for memory + eviction info (for the admin endpoint)."""
    client = _get_client()
    if client is None:
        return {"available": False, "reason": "client not initialized"}
    try:
        info = await client.info("memory")
    except Exception as e:
        return {"available": False, "reason": str(e)}
    if not isinstance(info, dict):
        return {"available": False, "reason": "unexpected INFO response"}
    return {
        "available": True,
        "used_memory": info.get("used_memory"),
        "used_memory_human": info.get("used_memory_human"),
        "maxmemory": info.get("maxmemory"),
        "maxmemory_human": info.get("maxmemory_human"),
        "maxmemory_policy": info.get("maxmemory_policy"),
        "evicted_keys": info.get("evicted_keys"),
    }
