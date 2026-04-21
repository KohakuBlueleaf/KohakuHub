"""Tests for fallback cache behavior."""

from kohakuhub.api.fallback.cache import RepoSourceCache


def test_cache_set_get_and_stats():
    cache = RepoSourceCache(ttl_seconds=60, maxsize=5)
    cache.set(
        "model",
        "owner",
        "demo",
        source_url="https://huggingface.co",
        source_name="HF",
        source_type="huggingface",
    )

    cached = cache.get("model", "owner", "demo")

    assert cached["source_name"] == "HF"
    assert cache.stats()["size"] == 1


def test_cache_invalidate_and_clear():
    cache = RepoSourceCache(ttl_seconds=60, maxsize=5)
    cache.set(
        "model",
        "owner",
        "demo",
        source_url="https://huggingface.co",
        source_name="HF",
        source_type="huggingface",
    )

    cache.invalidate("model", "owner", "demo")
    assert cache.get("model", "owner", "demo") is None

    cache.set(
        "dataset",
        "owner",
        "corpus",
        source_url="https://huggingface.co",
        source_name="HF",
        source_type="huggingface",
    )
    cache.clear()
    assert cache.stats()["size"] == 0
