"""Tests for fallback operations."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
from types import SimpleNamespace

import httpx
import pytest

import kohakuhub.api.fallback.operations as fallback_ops


def _json_response(status_code: int, payload, *, url: str = "https://source.local/api") -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", url),
    )


def _content_response(
    status_code: int,
    content: bytes = b"",
    *,
    headers: dict[str, str] | None = None,
    url: str = "https://source.local/file.bin",
) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=content,
        headers=headers,
        request=httpx.Request("GET", url),
    )


class DummyCache:
    """Simple cache spy."""

    def __init__(self, cached: dict | None = None):
        self.cached = cached
        self.set_calls: list[tuple[tuple, dict]] = []

    def get(self, *args):
        return self.cached

    def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))


class FakeFallbackClient:
    """Fallback client stub with per-source response registry."""

    registry: dict[tuple[str, str, str], list[object]] = {}
    calls: list[tuple[str, str, str, dict]] = []

    def __init__(self, source_url: str, source_type: str, token: str | None = None):
        self.source_url = source_url
        self.source_type = source_type
        self.token = token
        self.timeout = 12

    @classmethod
    def reset(cls) -> None:
        cls.registry = {}
        cls.calls = []

    @classmethod
    def queue(cls, source_url: str, method: str, path: str, *results: object) -> None:
        cls.registry[(source_url, method, path)] = list(results)

    def map_url(self, kohaku_path: str, repo_type: str) -> str:
        return f"{self.source_url}{kohaku_path}"

    async def _dispatch(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.calls.append((self.source_url, method, path, kwargs))
        queue = self.registry[(self.source_url, method, path)]
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def head(self, kohaku_path: str, repo_type: str, **kwargs) -> httpx.Response:
        return await self._dispatch("HEAD", kohaku_path, **kwargs)

    async def get(self, kohaku_path: str, repo_type: str, **kwargs) -> httpx.Response:
        return await self._dispatch("GET", kohaku_path, **kwargs)

    async def post(self, kohaku_path: str, repo_type: str, **kwargs) -> httpx.Response:
        return await self._dispatch("POST", kohaku_path, **kwargs)


@pytest.fixture(autouse=True)
def _reset_fallback_env(monkeypatch):
    monkeypatch.setattr(fallback_ops.cfg.fallback, "enabled", True)
    FakeFallbackClient.reset()
    monkeypatch.setattr(fallback_ops, "FallbackClient", FakeFallbackClient)


@pytest.mark.asyncio
async def test_try_fallback_resolve_returns_none_without_sources(monkeypatch):
    monkeypatch.setattr(fallback_ops, "get_enabled_sources", lambda namespace, user_tokens=None: [])

    assert (
        await fallback_ops.try_fallback_resolve(
            "model",
            "owner",
            "demo",
            "main",
            "README.md",
        )
        is None
    )


@pytest.mark.asyncio
async def test_try_fallback_resolve_prefers_cached_source_for_head_requests(monkeypatch):
    cache = DummyCache(
        {
            "exists": True,
            "source_url": "https://secondary.local",
            "source_name": "Secondary",
            "source_type": "huggingface",
        }
    )
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://primary.local", "name": "Primary", "source_type": "huggingface"},
            {"url": "https://secondary.local", "name": "Secondary", "source_type": "huggingface"},
        ],
    )
    FakeFallbackClient.queue(
        "https://secondary.local",
        "HEAD",
        "/models/owner/demo/resolve/main/README.md",
        _content_response(307, headers={"etag": "abc"}),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model",
        "owner",
        "demo",
        "main",
        "README.md",
        method="HEAD",
    )

    assert response.status_code == 307
    assert response.headers["etag"] == "abc"
    assert response.headers["X-Source"] == "Secondary"
    assert FakeFallbackClient.calls[0][:3] == (
        "https://secondary.local",
        "HEAD",
        "/models/owner/demo/resolve/main/README.md",
    )
    assert cache.set_calls[0][0][3:] == (
        "https://secondary.local",
        "Secondary",
        "huggingface",
    )


@pytest.mark.asyncio
async def test_try_fallback_resolve_proxies_get_content_and_continues_after_get_failure(monkeypatch):
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://first.local", "name": "First", "source_type": "huggingface"},
            {"url": "https://second.local", "name": "Second", "source_type": "huggingface"},
        ],
    )
    path = "/models/owner/demo/resolve/main/model.bin"
    FakeFallbackClient.queue("https://first.local", "HEAD", path, _content_response(200))
    FakeFallbackClient.queue("https://first.local", "GET", path, _content_response(500))
    FakeFallbackClient.queue("https://second.local", "HEAD", path, _content_response(200))
    FakeFallbackClient.queue(
        "https://second.local",
        "GET",
        path,
        _content_response(
            200,
            gzip.compress(b"payload"),
            headers={
                "content-type": "application/octet-stream",
                "content-encoding": "gzip",
                "content-length": "999",
                "transfer-encoding": "chunked",
            },
        ),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model",
        "owner",
        "demo",
        "main",
        "model.bin",
    )

    assert response.status_code == 200
    assert response.body == b"payload"
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == "7"
    assert "transfer-encoding" not in response.headers
    assert response.headers["X-Source"] == "Second"


@pytest.mark.asyncio
async def test_try_fallback_resolve_stops_on_non_retryable_status_and_handles_timeouts(monkeypatch):
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://timeout.local", "name": "Timeout", "source_type": "huggingface"},
            {"url": "https://auth.local", "name": "Auth", "source_type": "huggingface"},
            {"url": "https://unused.local", "name": "Unused", "source_type": "huggingface"},
        ],
    )
    path = "/models/owner/demo/resolve/main/config.json"
    FakeFallbackClient.queue(
        "https://timeout.local",
        "HEAD",
        path,
        httpx.TimeoutException("too slow"),
    )
    FakeFallbackClient.queue("https://auth.local", "HEAD", path, _content_response(401))

    response = await fallback_ops.try_fallback_resolve(
        "model",
        "owner",
        "demo",
        "main",
        "config.json",
    )

    assert response is None
    assert [call[0] for call in FakeFallbackClient.calls] == [
        "https://timeout.local",
        "https://auth.local",
    ]


@pytest.mark.asyncio
async def test_try_fallback_info_tree_and_paths_info_cover_success_paths(monkeypatch):
    cache = DummyCache()
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://source.local", "name": "Source", "source_type": "huggingface"}
        ],
    )
    FakeFallbackClient.queue(
        "https://source.local",
        "GET",
        "/api/models/owner/demo",
        _json_response(200, {"id": "owner/demo"}),
    )
    FakeFallbackClient.queue(
        "https://source.local",
        "GET",
        "/api/models/owner/demo/tree/main/folder/file.txt",
        _json_response(200, [{"path": "folder/file.txt"}]),
    )
    FakeFallbackClient.queue(
        "https://source.local",
        "POST",
        "/api/models/owner/demo/paths-info/main",
        _json_response(200, [{"path": "folder/file.txt", "type": "file"}]),
    )

    info = await fallback_ops.try_fallback_info("model", "owner", "demo")
    tree = await fallback_ops.try_fallback_tree("model", "owner", "demo", "main", "/folder/file.txt")
    paths_info = await fallback_ops.try_fallback_paths_info(
        "model",
        "owner",
        "demo",
        "main",
        ["folder/file.txt"],
    )

    assert info["_source"] == "Source"
    assert info["_source_url"] == "https://source.local"
    assert tree == [{"path": "folder/file.txt"}]
    assert paths_info == [{"path": "folder/file.txt", "type": "file"}]
    assert cache.set_calls[0][0][:3] == ("model", "owner", "demo")
    assert FakeFallbackClient.calls[-1][3]["data"] == {
        "paths": ["folder/file.txt"],
        "expand": False,
    }


@pytest.mark.asyncio
async def test_fetch_external_list_tags_results_and_handles_errors(monkeypatch):
    source = {"url": "https://source.local", "name": "Source", "source_type": "huggingface"}

    class SimpleClient:
        def __init__(self, source_url: str, source_type: str, token: str | None = None):
            self.timeout = 9
            self.source_url = source_url

        def map_url(self, kohaku_path: str, repo_type: str) -> str:
            return f"{self.source_url}{kohaku_path}"

    class FakeAsyncHTTPClient:
        calls: list[tuple[str, dict]] = []

        def __init__(self, timeout: int):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str, params: dict):
            self.calls.append((url, params))
            return _json_response(200, [{"id": "owner/demo"}], url=url)

    monkeypatch.setattr(fallback_ops, "FallbackClient", SimpleClient)
    monkeypatch.setattr(fallback_ops.httpx, "AsyncClient", FakeAsyncHTTPClient)

    results = await fallback_ops.fetch_external_list(
        source,
        "model",
        {"author": "owner", "limit": 5, "sort": "updated"},
    )

    assert results == [
        {
            "id": "owner/demo",
            "_source": "Source",
            "_source_url": "https://source.local",
        }
    ]
    assert FakeAsyncHTTPClient.calls == [
        ("https://source.local/api/models", {"author": "owner", "limit": 5})
    ]

    class FailingHTTPClient(FakeAsyncHTTPClient):
        async def get(self, url: str, params: dict):
            raise RuntimeError("network down")

    monkeypatch.setattr(fallback_ops.httpx, "AsyncClient", FailingHTTPClient)
    assert await fallback_ops.fetch_external_list(source, "model", {"author": "owner"}) == []


@pytest.mark.asyncio
async def test_try_fallback_user_profile_supports_hf_user_hf_org_and_kohakuhub(monkeypatch):
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace="", user_tokens=None: [
            {"url": "https://hf.local", "name": "HF", "source_type": "huggingface"},
            {"url": "https://kohaku.local", "name": "Kohaku", "source_type": "kohakuhub"},
        ],
    )
    FakeFallbackClient.queue(
        "https://hf.local",
        "GET",
        "/api/users/alice/overview",
        _json_response(
            200,
            {
                "fullname": "Alice Example",
                "createdAt": "2025-01-01T00:00:00Z",
                "avatarUrl": "https://cdn.local/avatar.jpg",
                "isPro": True,
                "type": "user",
            },
        ),
    )
    FakeFallbackClient.queue(
        "https://hf.local",
        "GET",
        "/api/users/acme/overview",
        _content_response(404),
    )
    FakeFallbackClient.queue(
        "https://hf.local",
        "GET",
        "/api/organizations/acme/members",
        _json_response(200, [{"name": "member"}]),
    )
    FakeFallbackClient.queue(
        "https://kohaku.local",
        "GET",
        "/api/users/bob/profile",
        _json_response(200, {"username": "bob", "full_name": "Bob Example"}),
    )

    user_profile = await fallback_ops.try_fallback_user_profile("alice")
    org_profile = await fallback_ops.try_fallback_user_profile("acme")
    kohaku_profile = await fallback_ops.try_fallback_user_profile("bob")

    assert user_profile["full_name"] == "Alice Example"
    assert user_profile["_hf_type"] == "user"
    assert org_profile["_hf_type"] == "org"
    assert org_profile["_member_count"] == 1
    assert kohaku_profile == {
        "username": "bob",
        "full_name": "Bob Example",
        "_source": "Kohaku",
        "_source_url": "https://kohaku.local",
    }


@pytest.mark.asyncio
async def test_try_fallback_user_and_org_avatar_cover_hf_and_kohakuhub(monkeypatch):
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace="", user_tokens=None: [
            {"url": "https://hf.local", "name": "HF", "source_type": "huggingface"},
            {"url": "https://kohaku.local", "name": "Kohaku", "source_type": "kohakuhub"},
        ],
    )
    FakeFallbackClient.queue(
        "https://hf.local",
        "GET",
        "/api/users/alice/overview",
        _json_response(200, {"avatarUrl": "https://cdn.local/alice.jpg"}),
    )
    FakeFallbackClient.queue(
        "https://kohaku.local",
        "GET",
        "/api/users/bob/avatar",
        _content_response(200, b"bob-avatar"),
    )
    FakeFallbackClient.queue(
        "https://kohaku.local",
        "GET",
        "/api/organizations/acme/avatar",
        _content_response(200, b"org-avatar"),
    )

    class AvatarHTTPClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str):
            return _content_response(200, b"alice-avatar", url=url)

    monkeypatch.setattr(fallback_ops.httpx, "AsyncClient", AvatarHTTPClient)

    assert await fallback_ops.try_fallback_user_avatar("alice") == b"alice-avatar"
    assert await fallback_ops.try_fallback_user_avatar("bob") == b"bob-avatar"
    assert await fallback_ops.try_fallback_org_avatar("acme") == b"org-avatar"


@pytest.mark.asyncio
async def test_try_fallback_user_repos_supports_hf_aggregation_and_kohakuhub(monkeypatch):
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace="", user_tokens=None: [
            {"url": "https://hf.local", "name": "HF", "source_type": "huggingface"}
        ],
    )
    FakeFallbackClient.queue(
        "https://hf.local",
        "GET",
        "/api/models?author=alice&limit=100",
        _json_response(200, [{"id": "alice/model-a"}]),
    )
    FakeFallbackClient.queue(
        "https://hf.local",
        "GET",
        "/api/datasets?author=alice&limit=100",
        RuntimeError("dataset listing failed"),
    )
    FakeFallbackClient.queue(
        "https://hf.local",
        "GET",
        "/api/spaces?author=alice&limit=100",
        _json_response(200, [{"id": "alice/space-a"}]),
    )
    FakeFallbackClient.queue(
        "https://kohaku.local",
        "GET",
        "/api/users/bob/repos",
        _json_response(200, {"models": [{"id": "bob/model-b"}], "datasets": [], "spaces": []}),
    )

    hf_repos = await fallback_ops.try_fallback_user_repos("alice")
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace="", user_tokens=None: [
            {"url": "https://kohaku.local", "name": "Kohaku", "source_type": "kohakuhub"}
        ],
    )
    kohaku_repos = await fallback_ops.try_fallback_user_repos("bob")

    assert hf_repos["models"][0]["_source"] == "HF"
    assert hf_repos["datasets"] == []
    assert hf_repos["spaces"][0]["id"] == "alice/space-a"
    assert kohaku_repos["models"][0]["_source_url"] == "https://kohaku.local"
