"""Cache system for repository→source mappings.

Caches which external source has a given repository to reduce external
API calls. Does NOT cache actual content — only the binding (which
source serves a given ``repo_id``).

Strict-freshness contract (#79):

- Key includes ``user_id`` and ``tokens_hash`` so two users with
  different effective per-source tokens cannot share a binding, and a
  request carrying ``Authorization: Bearer ...|url,token|...`` external
  tokens cannot read another request's binding.
- Three independent monotonic generation counters (``global_gen``,
  ``user_gens``, ``repo_gens``) close the "admin/user/repo mutation
  lands while a probe is in flight, and the probe writes a stale
  binding after the invalidation" race. ``safe_set`` rejects writes
  whose starting snapshot disagrees with the current generations.
- Mutation methods (``clear``, ``clear_user``, ``invalidate_repo``)
  bump their respective counters in addition to deleting cache
  entries.
"""

import hashlib
import json
import time
from typing import Optional

from cachetools import TTLCache

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger

logger = get_logger("FALLBACK_CACHE")


def compute_tokens_hash(user_tokens: dict[str, str] | None) -> str:
    """Compute a stable, order-invariant hash of a user's effective tokens.

    Used as part of the fallback cache key so two requests sharing the
    same effective token set hit the same cache entry, but a request
    with any per-source token difference is isolated to its own bucket.

    Args:
        user_tokens: Dict mapping ``url → token`` or ``None``.

    Returns:
        16-hex-char prefix of ``sha256(canonical_json(sorted_items))``.
        Empty string if ``user_tokens`` is empty/None.
    """
    if not user_tokens:
        return ""
    canonical = json.dumps(
        sorted(user_tokens.items()), separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class RepoSourceCache:
    """TTL+LRU cache for repo→source bindings, keyed per user+tokens_hash.

    Generation counters provide race protection against invalidation
    events landing while a probe is in flight; see ``safe_set``.
    """

    def __init__(self, ttl_seconds: int = 30, maxsize: int = 10000):
        """Initialize cache.

        Default TTL is 30s — short enough that a transient bound-source
        outage self-heals within a half-minute, low enough to keep the
        bound-source consistency window bounded, but still long enough
        to amortise chain-probe cost across a typical hf_hub session
        (which fires 1–10 calls within seconds). Override via
        ``KOHAKU_HUB_FALLBACK_CACHE_TTL`` / ``cfg.fallback.cache_ttl_seconds``
        if a deployment prefers a different self-heal window.

        Args:
            ttl_seconds: TTL for cache entries.
            maxsize: LRU size cap.
        """
        self.cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self.ttl = ttl_seconds
        # Generations are monotonic counters bumped by mutation methods.
        # ``snapshot`` reads them at probe start; ``safe_set`` re-reads at
        # probe end and rejects the write if any has been bumped.
        #
        # Storage is a plain ``dict`` (not ``defaultdict``) so that the
        # read-only ``snapshot`` path uses ``.get(key, 0)`` and never
        # materializes a phantom entry. Without this, every distinct
        # ``(user_id, repo_type, namespace, name)`` ever queried would
        # leave a permanent zero-valued counter in ``user_gens`` /
        # ``repo_gens`` — an unbounded leak under workloads with high
        # repo / user cardinality.
        self.global_gen: int = 0
        self.user_gens: dict[Optional[int], int] = {}
        self.repo_gens: dict[tuple[str, str, str], int] = {}

    @staticmethod
    def _user_key(user_id: Optional[int]) -> str:
        return "anon" if user_id is None else str(user_id)

    def get_key(
        self,
        user_id: Optional[int],
        tokens_hash: str,
        repo_type: str,
        namespace: str,
        name: str,
    ) -> str:
        """Build the canonical cache key.

        Format: ``fallback:repo:u={user_id|anon}:t={tokens_hash|}:{repo_type}:{ns}/{name}``.
        Only the suffix ``:{repo_type}:{ns}/{name}`` is used by
        ``invalidate_repo`` for repo-wide eviction.
        """
        u = self._user_key(user_id)
        h = tokens_hash or ""
        return f"fallback:repo:u={u}:t={h}:{repo_type}:{namespace}/{name}"

    def get(
        self,
        user_id: Optional[int],
        tokens_hash: str,
        repo_type: str,
        namespace: str,
        name: str,
    ) -> Optional[dict]:
        """Get cached source info for one (user, tokens_hash, repo) bucket."""
        key = self.get_key(user_id, tokens_hash, repo_type, namespace, name)
        cached = self.cache.get(key)
        if cached:
            logger.debug(
                f"Cache HIT: u={self._user_key(user_id)} "
                f"{repo_type}/{namespace}/{name} -> {cached.get('source_name')}"
            )
            return cached
        logger.debug(
            f"Cache MISS: u={self._user_key(user_id)} "
            f"{repo_type}/{namespace}/{name}"
        )
        return None

    def set(
        self,
        user_id: Optional[int],
        tokens_hash: str,
        repo_type: str,
        namespace: str,
        name: str,
        source_url: str,
        source_name: str,
        source_type: str,
        exists: bool = True,
    ) -> None:
        """Unconditionally set the cache entry.

        Prefer ``safe_set`` from inside the operations layer — it adds
        the generation-counter race check. Plain ``set`` is provided
        for tests and for the rare path that needs to write without
        race protection.
        """
        key = self.get_key(user_id, tokens_hash, repo_type, namespace, name)
        value = {
            "source_url": source_url,
            "source_name": source_name,
            "source_type": source_type,
            "checked_at": int(time.time()),
            "exists": exists,
        }
        self.cache[key] = value
        logger.debug(
            f"Cache SET: u={self._user_key(user_id)} "
            f"{repo_type}/{namespace}/{name} -> {source_name} (TTL={self.ttl}s)"
        )

    def snapshot(
        self,
        user_id: Optional[int],
        repo_type: str,
        namespace: str,
        name: str,
    ) -> tuple[int, int, int]:
        """Capture ``(global_gen, user_gen, repo_gen)`` for ``safe_set``.

        The probe orchestrator should call this *before* doing any
        upstream I/O, then pass the tuple to ``safe_set`` after the
        probe completes. ``safe_set`` rejects the write if any of the
        three counters has been bumped, indicating an invalidation
        event landed during the probe window.
        """
        # ``.get(key, 0)`` rather than indexing — keeps the read-only
        # snapshot path from creating phantom entries (see __init__).
        return (
            self.global_gen,
            self.user_gens.get(user_id, 0),
            self.repo_gens.get((repo_type, namespace, name), 0),
        )

    def safe_set(
        self,
        user_id: Optional[int],
        tokens_hash: str,
        repo_type: str,
        namespace: str,
        name: str,
        source_url: str,
        source_name: str,
        source_type: str,
        gens_at_start: tuple[int, int, int],
        exists: bool = True,
    ) -> bool:
        """Set cache only if generations are unchanged since ``gens_at_start``.

        Returns True if the entry was written; False if any of the
        three generation counters has been bumped between
        ``gens_at_start`` and now (admin source mutation, user token
        rotation, or repo CRUD landed mid-probe).

        On rejection the cache stays empty for this (user, tokens, repo)
        bucket and the next request will re-probe with the post-mutation
        configuration — strict freshness preserved.
        """
        if self.snapshot(user_id, repo_type, namespace, name) != gens_at_start:
            logger.debug(
                f"Cache SET REJECTED (gen changed mid-probe): "
                f"u={self._user_key(user_id)} {repo_type}/{namespace}/{name}"
            )
            return False
        self.set(
            user_id,
            tokens_hash,
            repo_type,
            namespace,
            name,
            source_url,
            source_name,
            source_type,
            exists=exists,
        )
        return True

    def invalidate(
        self,
        user_id: Optional[int],
        tokens_hash: str,
        repo_type: str,
        namespace: str,
        name: str,
    ) -> bool:
        """Delete a single (user, tokens_hash, repo) entry.

        Does NOT bump any generation counter — used for narrow
        per-entry hygiene (e.g. orphan-source detection in
        ``_run_cached_then_chain``). Repo-wide / user-wide / global
        invalidations should call ``invalidate_repo`` / ``clear_user``
        / ``clear``, which bump the appropriate generations.

        Returns True if an entry was deleted, False if the key was
        already absent.
        """
        key = self.get_key(user_id, tokens_hash, repo_type, namespace, name)
        if key in self.cache:
            del self.cache[key]
            logger.debug(
                f"Cache INVALIDATE: u={self._user_key(user_id)} "
                f"{repo_type}/{namespace}/{name}"
            )
            return True
        return False

    def invalidate_repo(
        self, repo_type: str, namespace: str, name: str
    ) -> int:
        """Evict every (user, tokens_hash, repo) bucket for one repo.

        Bumps ``repo_gens[(repo_type, namespace, name)]`` so any
        probe currently in flight for this repo will have its
        ``safe_set`` rejected. Returns the number of cache entries
        evicted.

        Triggered by local repo create/delete/move/visibility-toggle
        and the admin per-repo eviction endpoint — see the strict
        freshness contract.
        """
        key = (repo_type, namespace, name)
        self.repo_gens[key] = self.repo_gens.get(key, 0) + 1
        suffix = f":{repo_type}:{namespace}/{name}"
        evicted_keys = [k for k in list(self.cache.keys()) if k.endswith(suffix)]
        for k in evicted_keys:
            del self.cache[k]
        if evicted_keys:
            logger.info(
                f"Cache INVALIDATE_REPO: {repo_type}/{namespace}/{name} "
                f"({len(evicted_keys)} entries)"
            )
        return len(evicted_keys)

    def clear_user(self, user_id: Optional[int]) -> int:
        """Evict every (tokens_hash, repo) bucket for one user.

        Bumps ``user_gens[user_id]`` so any probe currently in flight
        for that user has its ``safe_set`` rejected. Returns the
        number of cache entries evicted.

        Triggered by user external-token POST/DELETE/PUT bulk and the
        admin per-user eviction endpoint.
        """
        self.user_gens[user_id] = self.user_gens.get(user_id, 0) + 1
        prefix = f"fallback:repo:u={self._user_key(user_id)}:"
        evicted_keys = [
            k for k in list(self.cache.keys()) if k.startswith(prefix)
        ]
        for k in evicted_keys:
            del self.cache[k]
        if evicted_keys:
            logger.info(
                f"Cache CLEAR_USER: user_id={user_id} "
                f"({len(evicted_keys)} entries)"
            )
        return len(evicted_keys)

    def clear(self) -> None:
        """Wipe the entire cache; bump ``global_gen``.

        Triggered by admin source list mutations (create/update/delete)
        and the admin global cache-clear endpoint.
        """
        self.global_gen += 1
        self.cache.clear()
        logger.info("Cache cleared (global_gen bumped)")

    def stats(self) -> dict:
        """Return cache statistics for the admin / observability surface."""
        return {
            "size": len(self.cache),
            "maxsize": self.cache.maxsize,
            "ttl_seconds": self.ttl,
            "global_gen": self.global_gen,
        }


# Global cache instance
_cache: Optional[RepoSourceCache] = None


def get_cache() -> RepoSourceCache:
    """Get the singleton cache instance.

    Returns:
        Global ``RepoSourceCache``.
    """
    global _cache
    if _cache is None:
        ttl = cfg.fallback.cache_ttl_seconds
        _cache = RepoSourceCache(ttl_seconds=ttl)
        logger.info(f"Initialized fallback cache (TTL={ttl}s)")
    return _cache


def reset_cache_for_tests() -> None:
    """Drop the singleton instance. Test-only hook."""
    global _cache
    _cache = None
