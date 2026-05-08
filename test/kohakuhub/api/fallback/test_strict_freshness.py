"""Strict-freshness contract end-to-end tests (#79).

These tests assert the cache invalidation behaviour under the post-#79
contract: per-user / per-tokens_hash isolation, generation-counter
race protection, repo CRUD eviction, and external-token mutation
eviction. The fallback chain probe itself is mocked through
``DummyCache`` and ``FakeFallbackClient`` so the focus stays on the
cache-orchestration semantics, not the upstream HTTP shape (those are
covered in ``test_strict_consistency.py`` end-to-end against
``scenario_hf_server``).

The race tests exercise ``safe_set`` rejection by bumping the
relevant generation counter between the probe-start snapshot and
``cache.safe_set``. Each test pins exactly one of the three
counters to verify the corresponding race class is closed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Optional

import httpx
import pytest

import kohakuhub.api.fallback.operations as fallback_ops
from kohakuhub.api.fallback.cache import (
    RepoSourceCache,
    compute_tokens_hash,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_response(status: int = 200, body: bytes = b"", headers=None):
    return httpx.Response(
        status_code=status,
        content=body or b"",
        headers=headers or {},
        request=httpx.Request("GET", "http://upstream/x"),
    )


class _StubClient:
    """Minimal FallbackClient stub returning a single response.

    Records the (method, path) of each call so tests can assert the
    chain reached or skipped a specific source.
    """

    def __init__(self, source_url: str, source_type: str, token: str | None = None):
        self.source_url = source_url
        self.source_type = source_type
        self.token = token
        self.timeout = 12

    @classmethod
    def reset(cls, registry: dict) -> None:
        cls._registry = registry
        cls.calls = []

    def map_url(self, kohaku_path: str, repo_type: str) -> str:
        return f"{self.source_url}{kohaku_path}"

    async def head(self, kohaku_path: str, repo_type: str, **kwargs) -> httpx.Response:
        type(self).calls.append((self.source_url, "HEAD", kohaku_path))
        return type(self)._registry[(self.source_url, "HEAD")]

    async def get(self, kohaku_path: str, repo_type: str, **kwargs) -> httpx.Response:
        type(self).calls.append((self.source_url, "GET", kohaku_path))
        return type(self)._registry[(self.source_url, "GET")]

    async def post(self, kohaku_path: str, repo_type: str, **kwargs) -> httpx.Response:
        type(self).calls.append((self.source_url, "POST", kohaku_path))
        return type(self)._registry[(self.source_url, "POST")]


def _wire_stub_client(monkeypatch, registry: dict) -> None:
    _StubClient.reset(registry)
    monkeypatch.setattr(fallback_ops, "FallbackClient", _StubClient)


def _user(user_id: int):
    return SimpleNamespace(id=user_id, username=f"user-{user_id}")


# ---------------------------------------------------------------------------
# Per-user / per-tokens_hash binding isolation through the live op layer.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_user_binding_does_not_leak_across_users(monkeypatch):
    """User A binds source X. User B's first call to the same repo
    must NOT read user A's binding — it has its own (empty) bucket
    and re-probes the chain."""
    cache = RepoSourceCache(ttl_seconds=60)
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://x.local", "name": "X", "source_type": "huggingface"},
        ],
    )
    _wire_stub_client(
        monkeypatch,
        {("https://x.local", "GET"): _content_response(200, b'{"id": "owner/demo"}')},
    )

    # User A's first call binds.
    result_a = await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=None, user=_user(1)
    )
    assert result_a == {"id": "owner/demo", "_source": "X", "_source_url": "https://x.local"}
    assert cache.get(1, "", "model", "owner", "demo") is not None
    assert cache.get(2, "", "model", "owner", "demo") is None

    # User B's first call must hit the chain (cache miss for user 2).
    calls_before = list(_StubClient.calls)
    await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=None, user=_user(2)
    )
    assert len(_StubClient.calls) == len(calls_before) + 1, (
        "user 2 must re-probe — separate cache bucket from user 1"
    )
    assert cache.get(2, "", "model", "owner", "demo") is not None


@pytest.mark.asyncio
async def test_anonymous_and_authed_buckets_isolated(monkeypatch):
    cache = RepoSourceCache(ttl_seconds=60)
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://x.local", "name": "X", "source_type": "huggingface"},
        ],
    )
    _wire_stub_client(
        monkeypatch,
        {("https://x.local", "GET"): _content_response(200, b'{"id": "owner/demo"}')},
    )

    # Anonymous request binds.
    await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=None, user=None
    )
    assert cache.get(None, "", "model", "owner", "demo") is not None

    # Authed request: independent bucket; needs to probe.
    calls_before = list(_StubClient.calls)
    await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=None, user=_user(99)
    )
    assert len(_StubClient.calls) == len(calls_before) + 1


@pytest.mark.asyncio
async def test_header_token_change_isolates_bucket(monkeypatch):
    """Same user, different external tokens (e.g. swapping HF tokens
    via Authorization header) → different tokens_hash → separate
    cache buckets."""
    cache = RepoSourceCache(ttl_seconds=60)
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://x.local", "name": "X", "source_type": "huggingface"},
        ],
    )
    _wire_stub_client(
        monkeypatch,
        {("https://x.local", "GET"): _content_response(200, b'{"id": "owner/demo"}')},
    )

    user = _user(42)
    tokens_a = {"https://huggingface.co": "hf_AAAA"}
    tokens_b = {"https://huggingface.co": "hf_BBBB"}

    # First call with tokens_a binds.
    await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=tokens_a, user=user
    )
    h_a = compute_tokens_hash(tokens_a)
    h_b = compute_tokens_hash(tokens_b)
    assert h_a != h_b
    assert cache.get(42, h_a, "model", "owner", "demo") is not None

    # Second call with tokens_b: same user_id, different hash → miss.
    calls_before = list(_StubClient.calls)
    await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=tokens_b, user=user
    )
    assert len(_StubClient.calls) == len(calls_before) + 1
    assert cache.get(42, h_b, "model", "owner", "demo") is not None
    # tokens_a bucket still bound — was not evicted.
    assert cache.get(42, h_a, "model", "owner", "demo") is not None


# ---------------------------------------------------------------------------
# Race protection: safe_set rejects when generations change mid-probe.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_source_mutation_during_probe_rejects_cache_set(monkeypatch):
    """Admin clears the cache (bumping global_gen) while a probe is
    in flight. The probe completes, but its safe_set is rejected
    so the next call re-probes with the post-mutation config."""
    cache = RepoSourceCache(ttl_seconds=60)
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://x.local", "name": "X", "source_type": "huggingface"},
        ],
    )

    # Wrap _StubClient.get so it bumps global_gen (admin mutation
    # landing) before returning. This simulates admin-clear during
    # the probe's HTTP I/O.
    class _RacingClient(_StubClient):
        async def get(self, kohaku_path, repo_type, **kwargs):
            cache.clear()  # admin mutation lands mid-probe
            return await super().get(kohaku_path, repo_type, **kwargs)

    _RacingClient.reset(
        {("https://x.local", "GET"): _content_response(200, b'{"id": "owner/demo"}')}
    )
    monkeypatch.setattr(fallback_ops, "FallbackClient", _RacingClient)

    result = await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=None, user=_user(1)
    )
    # Response came back successfully — caller is unaffected.
    assert result is not None
    # But the cache is empty: safe_set was rejected because
    # global_gen bumped between snapshot and write.
    assert cache.get(1, "", "model", "owner", "demo") is None


@pytest.mark.asyncio
async def test_user_token_mutation_during_probe_rejects_cache_set(monkeypatch):
    """User rotates their per-source token (bumping user_gens[uid])
    mid-probe. safe_set rejects."""
    cache = RepoSourceCache(ttl_seconds=60)
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://x.local", "name": "X", "source_type": "huggingface"},
        ],
    )

    class _RacingClient(_StubClient):
        async def get(self, kohaku_path, repo_type, **kwargs):
            cache.clear_user(1)  # user 1 rotates token mid-probe
            return await super().get(kohaku_path, repo_type, **kwargs)

    _RacingClient.reset(
        {("https://x.local", "GET"): _content_response(200, b'{"id": "owner/demo"}')}
    )
    monkeypatch.setattr(fallback_ops, "FallbackClient", _RacingClient)

    await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=None, user=_user(1)
    )
    # User 1's bucket: empty (safe_set rejected).
    assert cache.get(1, "", "model", "owner", "demo") is None
    # The bump didn't affect user 2 — safe_set for user 2 would still succeed.
    assert cache.user_gens.get(2, 0) == 0


@pytest.mark.asyncio
async def test_repo_crud_during_probe_rejects_cache_set(monkeypatch):
    """Local repo create/delete (bumping repo_gens) lands mid-probe.
    safe_set rejects, breaking the ghost-binding revival path."""
    cache = RepoSourceCache(ttl_seconds=60)
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://x.local", "name": "X", "source_type": "huggingface"},
        ],
    )

    class _RacingClient(_StubClient):
        async def get(self, kohaku_path, repo_type, **kwargs):
            # Local repo CRUD lands mid-probe.
            cache.invalidate_repo("model", "owner", "demo")
            return await super().get(kohaku_path, repo_type, **kwargs)

    _RacingClient.reset(
        {("https://x.local", "GET"): _content_response(200, b'{"id": "owner/demo"}')}
    )
    monkeypatch.setattr(fallback_ops, "FallbackClient", _RacingClient)

    await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=None, user=_user(1)
    )
    # No revival: cache stays empty for owner/demo.
    assert cache.get(1, "", "model", "owner", "demo") is None
    # repo_gen for OTHER repos untouched, so safe_set for them still works.
    assert cache.repo_gens.get(("model", "owner", "other"), 0) == 0


# ---------------------------------------------------------------------------
# Ghost-revival defense: repo CRUD across cycles.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalidate_repo_breaks_ghost_revival(monkeypatch):
    """Pre-CRUD: repo absent locally, cached as fallback binding. Local
    repo created (cache.invalidate_repo fires). Local repo deleted →
    cache must re-probe instead of resurrecting the ghost binding."""
    cache = RepoSourceCache(ttl_seconds=60)
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://x.local", "name": "X", "source_type": "huggingface"},
        ],
    )
    _wire_stub_client(
        monkeypatch,
        {("https://x.local", "GET"): _content_response(200, b'{"id": "owner/ghost"}')},
    )

    user = _user(1)

    # Phase 1: ghost binding written before local repo exists.
    await fallback_ops.try_fallback_info(
        "model", "owner", "ghost", user_tokens=None, user=user
    )
    assert cache.get(1, "", "model", "owner", "ghost") is not None

    # Phase 2: local create triggers invalidate_repo.
    cache.invalidate_repo("model", "owner", "ghost")
    assert cache.get(1, "", "model", "owner", "ghost") is None

    # Phase 3: local delete (or any other event that triggers a
    # subsequent fallback). Re-probe must run; the previous bucket
    # is gone, so a fresh bind happens with current upstream state.
    calls_before = list(_StubClient.calls)
    await fallback_ops.try_fallback_info(
        "model", "owner", "ghost", user_tokens=None, user=user
    )
    assert len(_StubClient.calls) == len(calls_before) + 1


# ---------------------------------------------------------------------------
# clear_user actually clears the per-user bucket end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_user_evicts_only_target_user(monkeypatch):
    cache = RepoSourceCache(ttl_seconds=60)
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://x.local", "name": "X", "source_type": "huggingface"},
        ],
    )
    _wire_stub_client(
        monkeypatch,
        {("https://x.local", "GET"): _content_response(200, b'{"id": "owner/demo"}')},
    )

    # Two users bind for the same repo.
    await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=None, user=_user(1)
    )
    await fallback_ops.try_fallback_info(
        "model", "owner", "demo", user_tokens=None, user=_user(2)
    )
    assert cache.get(1, "", "model", "owner", "demo") is not None
    assert cache.get(2, "", "model", "owner", "demo") is not None

    # User 1 rotates token → clear_user(1).
    cache.clear_user(1)
    assert cache.get(1, "", "model", "owner", "demo") is None
    assert cache.get(2, "", "model", "owner", "demo") is not None
