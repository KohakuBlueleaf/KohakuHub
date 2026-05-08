"""Tests for fallback cache behavior.

Covers the post-#79 strict-freshness contract:

- ``compute_tokens_hash`` ordering invariance and emptiness handling
- per-user / per-tokens_hash key isolation
- ``invalidate_repo`` / ``clear_user`` eviction scope
- generation counters (``global_gen`` / ``user_gens`` / ``repo_gens``)
- ``safe_set`` rejection on each of the three race classes
- ``invalidate`` single-entry delete (no gen bump)
- legacy paths kept (``stats``, ``clear``)
"""
import time

import pytest

from kohakuhub.api.fallback.cache import RepoSourceCache, compute_tokens_hash


# ---------------------------------------------------------------------------
# compute_tokens_hash
# ---------------------------------------------------------------------------


def test_compute_tokens_hash_empty_and_none_are_equivalent():
    assert compute_tokens_hash(None) == ""
    assert compute_tokens_hash({}) == ""


def test_compute_tokens_hash_non_empty_is_16_hex():
    h = compute_tokens_hash({"https://huggingface.co": "hf_abc"})
    assert isinstance(h, str)
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_compute_tokens_hash_is_order_invariant():
    a = compute_tokens_hash({"a": "1", "b": "2"})
    b = compute_tokens_hash({"b": "2", "a": "1"})
    assert a == b


def test_compute_tokens_hash_distinguishes_token_changes():
    a = compute_tokens_hash({"https://huggingface.co": "hf_abc"})
    b = compute_tokens_hash({"https://huggingface.co": "hf_DIFFERENT"})
    assert a != b


def test_compute_tokens_hash_distinguishes_url_changes():
    a = compute_tokens_hash({"https://a.local": "tok"})
    b = compute_tokens_hash({"https://b.local": "tok"})
    assert a != b


def test_compute_tokens_hash_distinguishes_extra_entry():
    a = compute_tokens_hash({"a": "1"})
    b = compute_tokens_hash({"a": "1", "b": "2"})
    assert a != b


# ---------------------------------------------------------------------------
# Basic set / get / stats / clear
# ---------------------------------------------------------------------------


def _seed(cache, user_id=None, tokens_hash="", repo_type="model", ns="owner",
          name="demo", url="https://huggingface.co", source_name="HF",
          source_type="huggingface"):
    cache.set(
        user_id, tokens_hash,
        repo_type, ns, name,
        url, source_name, source_type,
    )


def test_cache_set_get_and_stats():
    cache = RepoSourceCache(ttl_seconds=60, maxsize=5)
    _seed(cache)
    cached = cache.get(None, "", "model", "owner", "demo")
    assert cached is not None
    assert cached["source_name"] == "HF"
    assert cached["source_url"] == "https://huggingface.co"
    assert cached["source_type"] == "huggingface"
    assert cached["exists"] is True
    stats = cache.stats()
    assert stats["size"] == 1
    assert stats["maxsize"] == 5
    assert stats["ttl_seconds"] == 60
    assert stats["global_gen"] == 0


def test_cache_get_miss_returns_none():
    cache = RepoSourceCache()
    assert cache.get(None, "", "model", "owner", "absent") is None


def test_cache_invalidate_single_entry_no_gen_bump():
    cache = RepoSourceCache()
    _seed(cache, user_id=42, tokens_hash="abc")
    assert cache.invalidate(42, "abc", "model", "owner", "demo") is True
    assert cache.get(42, "abc", "model", "owner", "demo") is None
    # No generation bump — invalidate is a per-entry hygiene op only.
    assert cache.global_gen == 0
    assert cache.user_gens.get(42, 0) == 0
    assert cache.repo_gens.get(("model", "owner", "demo"), 0) == 0


def test_cache_invalidate_returns_false_when_absent():
    cache = RepoSourceCache()
    assert cache.invalidate(42, "", "model", "owner", "absent") is False


def test_cache_clear_bumps_global_gen_and_wipes_all_buckets():
    cache = RepoSourceCache()
    _seed(cache, user_id=1)
    _seed(cache, user_id=2)
    _seed(cache, user_id=None, name="other")
    assert cache.stats()["size"] == 3
    initial_gen = cache.global_gen
    cache.clear()
    assert cache.stats()["size"] == 0
    assert cache.global_gen == initial_gen + 1


# ---------------------------------------------------------------------------
# Per-user / per-tokens_hash isolation
# ---------------------------------------------------------------------------


def test_cache_per_user_isolation():
    """Two users with the same repo_id get independent cache buckets."""
    cache = RepoSourceCache()
    _seed(cache, user_id=1, url="https://a.local", source_name="A")
    _seed(cache, user_id=2, url="https://b.local", source_name="B")
    a = cache.get(1, "", "model", "owner", "demo")
    b = cache.get(2, "", "model", "owner", "demo")
    assert a["source_name"] == "A"
    assert b["source_name"] == "B"


def test_cache_anonymous_bucket_isolated_from_authed():
    cache = RepoSourceCache()
    _seed(cache, user_id=None, url="https://anon.local", source_name="Anon")
    _seed(cache, user_id=42, url="https://authed.local", source_name="Authed")
    assert cache.get(None, "", "model", "owner", "demo")["source_name"] == "Anon"
    assert cache.get(42, "", "model", "owner", "demo")["source_name"] == "Authed"


def test_cache_per_tokens_hash_isolation():
    """Same user, different tokens_hash → different buckets."""
    cache = RepoSourceCache()
    _seed(cache, user_id=42, tokens_hash="aaaa", url="https://x.local", source_name="X")
    _seed(cache, user_id=42, tokens_hash="bbbb", url="https://y.local", source_name="Y")
    x = cache.get(42, "aaaa", "model", "owner", "demo")
    y = cache.get(42, "bbbb", "model", "owner", "demo")
    assert x["source_name"] == "X"
    assert y["source_name"] == "Y"


# ---------------------------------------------------------------------------
# invalidate_repo
# ---------------------------------------------------------------------------


def test_invalidate_repo_clears_all_user_buckets_for_one_repo():
    cache = RepoSourceCache()
    _seed(cache, user_id=1)
    _seed(cache, user_id=2)
    _seed(cache, user_id=None)
    _seed(cache, user_id=1, name="other")  # different repo, must NOT be evicted
    assert cache.stats()["size"] == 4
    evicted = cache.invalidate_repo("model", "owner", "demo")
    assert evicted == 3
    assert cache.stats()["size"] == 1
    assert cache.get(1, "", "model", "owner", "other") is not None  # untouched


def test_invalidate_repo_bumps_repo_gen():
    cache = RepoSourceCache()
    _seed(cache, user_id=1)
    initial = cache.repo_gens.get(("model", "owner", "demo"), 0)
    cache.invalidate_repo("model", "owner", "demo")
    assert cache.repo_gens[("model", "owner", "demo")] == initial + 1
    # other repos' gens untouched
    assert cache.repo_gens.get(("model", "owner", "other"), 0) == 0


def test_invalidate_repo_with_no_entries_still_bumps_gen():
    cache = RepoSourceCache()
    evicted = cache.invalidate_repo("model", "ghost", "ghost")
    assert evicted == 0
    # The bump happens regardless — the protection against in-flight
    # probes does not need cache entries to exist.
    assert cache.repo_gens[("model", "ghost", "ghost")] == 1


def test_invalidate_repo_does_not_touch_global_or_user_gens():
    cache = RepoSourceCache()
    _seed(cache, user_id=1)
    cache.invalidate_repo("model", "owner", "demo")
    assert cache.global_gen == 0
    assert cache.user_gens.get(1, 0) == 0


# ---------------------------------------------------------------------------
# clear_user
# ---------------------------------------------------------------------------


def test_clear_user_clears_only_that_users_buckets():
    cache = RepoSourceCache()
    _seed(cache, user_id=1, name="r1")
    _seed(cache, user_id=1, name="r2")
    _seed(cache, user_id=2, name="r1")
    _seed(cache, user_id=None, name="r1")
    evicted = cache.clear_user(1)
    assert evicted == 2
    # Other users + anon untouched
    assert cache.get(2, "", "model", "owner", "r1") is not None
    assert cache.get(None, "", "model", "owner", "r1") is not None
    assert cache.get(1, "", "model", "owner", "r1") is None
    assert cache.get(1, "", "model", "owner", "r2") is None


def test_clear_user_anonymous_clears_anon_bucket():
    cache = RepoSourceCache()
    _seed(cache, user_id=None, name="r1")
    _seed(cache, user_id=42, name="r1")
    evicted = cache.clear_user(None)
    assert evicted == 1
    assert cache.get(None, "", "model", "owner", "r1") is None
    assert cache.get(42, "", "model", "owner", "r1") is not None


def test_clear_user_bumps_user_gen():
    cache = RepoSourceCache()
    _seed(cache, user_id=42)
    initial = cache.user_gens.get(42, 0)
    cache.clear_user(42)
    assert cache.user_gens[42] == initial + 1
    # Other user gens untouched
    assert cache.user_gens.get(7, 0) == 0


def test_clear_user_with_no_entries_still_bumps_gen():
    cache = RepoSourceCache()
    cache.clear_user(99)
    assert cache.user_gens[99] == 1


def test_clear_user_does_not_touch_global_or_repo_gens():
    cache = RepoSourceCache()
    _seed(cache, user_id=1)
    cache.clear_user(1)
    assert cache.global_gen == 0
    assert cache.repo_gens.get(("model", "owner", "demo"), 0) == 0


# ---------------------------------------------------------------------------
# snapshot + safe_set
# ---------------------------------------------------------------------------


def test_snapshot_initial_zeros():
    cache = RepoSourceCache()
    assert cache.snapshot(42, "model", "owner", "demo") == (0, 0, 0)


def test_snapshot_reflects_each_dimension():
    cache = RepoSourceCache()
    cache.clear()
    cache.clear_user(42)
    cache.invalidate_repo("model", "owner", "demo")
    snap = cache.snapshot(42, "model", "owner", "demo")
    assert snap == (1, 1, 1)
    # Other repo for same user: only global_gen + user_gen[42] match.
    other = cache.snapshot(42, "model", "owner", "other")
    assert other == (1, 1, 0)


def test_safe_set_succeeds_when_gens_unchanged():
    cache = RepoSourceCache()
    gens = cache.snapshot(42, "model", "owner", "demo")
    ok = cache.safe_set(
        42, "abc",
        "model", "owner", "demo",
        "https://x.local", "X", "huggingface",
        gens_at_start=gens,
    )
    assert ok is True
    cached = cache.get(42, "abc", "model", "owner", "demo")
    assert cached is not None
    assert cached["source_name"] == "X"


def test_safe_set_rejected_when_global_gen_bumped():
    cache = RepoSourceCache()
    gens = cache.snapshot(42, "model", "owner", "demo")
    cache.clear()  # bumps global_gen mid-probe
    ok = cache.safe_set(
        42, "abc",
        "model", "owner", "demo",
        "https://x.local", "X", "huggingface",
        gens_at_start=gens,
    )
    assert ok is False
    assert cache.get(42, "abc", "model", "owner", "demo") is None


def test_safe_set_rejected_when_user_gen_bumped():
    cache = RepoSourceCache()
    gens = cache.snapshot(42, "model", "owner", "demo")
    cache.clear_user(42)  # bumps user_gens[42] mid-probe
    ok = cache.safe_set(
        42, "abc",
        "model", "owner", "demo",
        "https://x.local", "X", "huggingface",
        gens_at_start=gens,
    )
    assert ok is False


def test_safe_set_rejected_when_repo_gen_bumped():
    cache = RepoSourceCache()
    gens = cache.snapshot(42, "model", "owner", "demo")
    cache.invalidate_repo("model", "owner", "demo")
    ok = cache.safe_set(
        42, "abc",
        "model", "owner", "demo",
        "https://x.local", "X", "huggingface",
        gens_at_start=gens,
    )
    assert ok is False


def test_safe_set_unaffected_by_other_users_invalidation():
    """A clear_user(7) must not reject user 42's safe_set."""
    cache = RepoSourceCache()
    gens = cache.snapshot(42, "model", "owner", "demo")
    cache.clear_user(7)
    ok = cache.safe_set(
        42, "abc",
        "model", "owner", "demo",
        "https://x.local", "X", "huggingface",
        gens_at_start=gens,
    )
    assert ok is True


def test_safe_set_unaffected_by_other_repos_invalidation():
    """An invalidate_repo on a different repo must not reject this safe_set."""
    cache = RepoSourceCache()
    gens = cache.snapshot(42, "model", "owner", "demo")
    cache.invalidate_repo("model", "owner", "OTHER")
    ok = cache.safe_set(
        42, "abc",
        "model", "owner", "demo",
        "https://x.local", "X", "huggingface",
        gens_at_start=gens,
    )
    assert ok is True


# ---------------------------------------------------------------------------
# TTL behavior (sanity smoke; the core eviction mechanism is cachetools).
# ---------------------------------------------------------------------------


def test_ttl_zero_evicts_immediately():
    cache = RepoSourceCache(ttl_seconds=0, maxsize=5)
    _seed(cache, user_id=42)
    # Sleep a hair to let the TTL window close.
    time.sleep(0.01)
    assert cache.get(42, "", "model", "owner", "demo") is None


# ---------------------------------------------------------------------------
# get_cache singleton + reset_cache_for_tests
# ---------------------------------------------------------------------------


def test_get_cache_returns_singleton():
    from kohakuhub.api.fallback import cache as cache_module

    cache_module.reset_cache_for_tests()
    a = cache_module.get_cache()
    b = cache_module.get_cache()
    assert a is b


def test_reset_cache_for_tests_drops_singleton():
    from kohakuhub.api.fallback import cache as cache_module

    a = cache_module.get_cache()
    cache_module.reset_cache_for_tests()
    b = cache_module.get_cache()
    assert a is not b


# ---------------------------------------------------------------------------
# Phantom-entry regression (PR #81 review item #1)
# ---------------------------------------------------------------------------


def test_snapshot_does_not_create_phantom_user_gens_entries():
    """Read-only ``snapshot`` MUST NOT materialize entries in
    ``user_gens``. With the old ``defaultdict(int)`` implementation
    every ``snapshot(user_id, ...)`` left a permanent zero-valued
    entry — an unbounded leak under workloads with high user
    cardinality (e.g. every request adds one). The fix uses
    ``dict.get(key, 0)`` in ``snapshot``; this test pins the
    invariant.
    """
    cache = RepoSourceCache()
    for uid in range(1000):
        cache.snapshot(uid, "model", "ns", "demo")
    assert len(cache.user_gens) == 0, (
        "snapshot must not insert into user_gens — that's the leak fixed in #81"
    )


def test_snapshot_does_not_create_phantom_repo_gens_entries():
    """Same invariant for ``repo_gens``: distinct repo identities
    queried via ``snapshot`` must not accumulate phantom entries.
    """
    cache = RepoSourceCache()
    for i in range(1000):
        cache.snapshot(None, "model", "ns", f"repo-{i}")
    assert len(cache.repo_gens) == 0, (
        "snapshot must not insert into repo_gens — that's the leak fixed in #81"
    )


def test_get_does_not_create_phantom_gens_entries():
    """``get`` (cache miss) must not create entries either."""
    cache = RepoSourceCache()
    for i in range(1000):
        cache.get(i, f"hash{i}", "model", "ns", f"repo-{i}")
    assert len(cache.user_gens) == 0
    assert len(cache.repo_gens) == 0


def test_invalidate_repo_only_inserts_for_invalidated_keys():
    """``invalidate_repo`` is the only legitimate writer to
    ``repo_gens``; verify cardinality matches the number of
    distinct repos invalidated."""
    cache = RepoSourceCache()
    cache.invalidate_repo("model", "ns", "r1")
    cache.invalidate_repo("model", "ns", "r2")
    cache.invalidate_repo("dataset", "ns", "r1")
    assert len(cache.repo_gens) == 3


def test_clear_user_only_inserts_for_cleared_users():
    """``clear_user`` is the only legitimate writer to ``user_gens``."""
    cache = RepoSourceCache()
    cache.clear_user(1)
    cache.clear_user(2)
    cache.clear_user(None)  # anonymous bucket
    assert len(cache.user_gens) == 3


# ---------------------------------------------------------------------------
# Key shape sanity (suffix used by invalidate_repo)
# ---------------------------------------------------------------------------


def test_get_key_shape_matches_invalidate_repo_suffix():
    """``invalidate_repo`` walks the cache by ``endswith`` on the
    ``:{repo_type}:{ns}/{name}`` suffix; this test pins the key
    serialization so the suffix-based eviction stays correct."""
    cache = RepoSourceCache()
    key_anon = cache.get_key(None, "", "model", "owner", "demo")
    key_42 = cache.get_key(42, "abc", "model", "owner", "demo")
    suffix = ":model:owner/demo"
    assert key_anon.endswith(suffix)
    assert key_42.endswith(suffix)
    # And the user-prefix matches what clear_user uses.
    assert key_anon.startswith("fallback:repo:u=anon:")
    assert key_42.startswith("fallback:repo:u=42:")
