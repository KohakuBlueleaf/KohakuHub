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
    history: list[httpx.Response] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=content,
        headers=headers,
        request=httpx.Request("GET", url),
        history=history or [],
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


class AbsoluteHeadStub:
    """Scripted replacement for httpx.AsyncClient.head used for extra-HEAD calls.

    When patched in via `monkeypatch.setattr(httpx.AsyncClient, "head", stub)`
    the bound-method descriptor drops the httpx-client self, so our call
    signature only needs (url, **kwargs).
    """

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[object] = []

    def queue(self, *results: object) -> None:
        self.responses.extend(results)

    async def __call__(self, url: str, **kwargs) -> httpx.Response:
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError(f"No scripted response for absolute HEAD {url}")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


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
async def test_try_fallback_resolve_head_rewrites_relative_location_to_absolute(monkeypatch):
    """HEAD must not leak HF's relative /api/resolve-cache Location —
    rewriting it to absolute steers the client back to the upstream for
    the follow-up redirect."""
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://hf.local", "name": "HF", "source_type": "huggingface"},
        ],
    )
    # LFS-shaped 307 (has X-Linked-Size) so no follow-up HEAD is issued.
    path = "/models/owner/demo/resolve/main/weights.safetensors"
    FakeFallbackClient.queue(
        "https://hf.local", "HEAD", path,
        _content_response(
            307,
            headers={
                "location": "/api/resolve-cache/models/owner/demo/sha/weights.safetensors",
                "content-length": "278",
                "x-linked-size": "67840504",
                "x-linked-etag": '"deadbeef"',
                "x-repo-commit": "abc123",
            },
            url="https://hf.local/models/owner/demo/resolve/main/weights.safetensors",
        ),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "weights.safetensors", method="HEAD",
    )

    assert response.status_code == 307
    assert (
        response.headers["location"]
        == "https://hf.local/api/resolve-cache/models/owner/demo/sha/weights.safetensors"
    )
    # LFS metadata preserved; no extra HEAD fired (X-Linked-Size suffices).
    assert response.headers["x-linked-size"] == "67840504"
    assert response.headers["x-repo-commit"] == "abc123"


@pytest.mark.asyncio
async def test_try_fallback_resolve_head_absolute_location_passes_through(monkeypatch):
    """An already-absolute Location (typical of LFS → cas-bridge) is kept
    verbatim — urljoin on an absolute target is a no-op."""
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://hf.local", "name": "HF", "source_type": "huggingface"},
        ],
    )
    absolute = "https://cas-bridge.xethub.hf.co/shard/deadbeef?token=xyz"
    path = "/datasets/owner/demo/resolve/main/data.parquet"
    FakeFallbackClient.queue(
        "https://hf.local", "HEAD", path,
        _content_response(
            302,
            headers={
                "location": absolute,
                "x-linked-size": "1234567",
            },
            url="https://hf.local/datasets/owner/demo/resolve/main/data.parquet",
        ),
    )

    response = await fallback_ops.try_fallback_resolve(
        "dataset", "owner", "demo", "main", "data.parquet", method="HEAD",
    )

    assert response.status_code == 302
    assert response.headers["location"] == absolute


@pytest.mark.asyncio
async def test_try_fallback_resolve_head_non_lfs_307_follows_for_content_length(monkeypatch):
    """Non-LFS 3xx (no X-Linked-Size) needs one extra HEAD to the rewritten
    Location to pick up the real Content-Length and ETag. This is the
    imgutils selected_tags.csv fix."""
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://hf.local", "name": "HF", "source_type": "huggingface"},
        ],
    )
    path = "/models/owner/demo/resolve/main/selected_tags.csv"
    FakeFallbackClient.queue(
        "https://hf.local", "HEAD", path,
        _content_response(
            307,
            headers={
                "location": "/api/resolve-cache/models/owner/demo/sha/selected_tags.csv",
                "content-length": "278",           # 307 body length, wrong
                "etag": '"placeholder"',
                "x-repo-commit": "abc123",
                "x-linked-etag": '"deadbeef"',
            },
            url="https://hf.local/models/owner/demo/resolve/main/selected_tags.csv",
        ),
    )
    stub = AbsoluteHeadStub()
    stub.queue(
        _content_response(
            200,
            headers={"content-length": "308468", "etag": '"deadbeef"'},
        ),
    )
    monkeypatch.setattr(httpx.AsyncClient, "head", stub.__call__)

    response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "selected_tags.csv", method="HEAD",
    )

    assert response.status_code == 307
    assert (
        response.headers["location"]
        == "https://hf.local/api/resolve-cache/models/owner/demo/sha/selected_tags.csv"
    )
    # Content-Length / ETag replaced with the final hop's values.
    assert response.headers["content-length"] == "308468"
    assert response.headers["etag"] == '"deadbeef"'
    # X-Repo-Commit / X-Linked-Etag kept from the initial 307.
    assert response.headers["x-repo-commit"] == "abc123"
    assert response.headers["x-linked-etag"] == '"deadbeef"'
    # Exactly one extra HEAD, against the rewritten absolute URL.
    assert len(stub.calls) == 1
    assert stub.calls[0][0] == response.headers["location"]


@pytest.mark.asyncio
async def test_try_fallback_resolve_head_follow_error_falls_back_silently(monkeypatch):
    """If the extra HEAD raises httpx.HTTPError, we keep the 307 response
    we already had instead of failing the request."""
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://hf.local", "name": "HF", "source_type": "huggingface"},
        ],
    )
    path = "/models/owner/demo/resolve/main/selected_tags.csv"
    FakeFallbackClient.queue(
        "https://hf.local", "HEAD", path,
        _content_response(
            307,
            headers={
                "location": "/api/resolve-cache/models/owner/demo/sha/selected_tags.csv",
                "content-length": "278",
                "x-repo-commit": "abc123",
            },
            url="https://hf.local/models/owner/demo/resolve/main/selected_tags.csv",
        ),
    )
    stub = AbsoluteHeadStub()
    stub.queue(httpx.ConnectError("boom"))
    monkeypatch.setattr(httpx.AsyncClient, "head", stub.__call__)

    response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "selected_tags.csv", method="HEAD",
    )

    # Initial 307 headers preserved (content-length stale but still returned).
    assert response.status_code == 307
    assert response.headers["content-length"] == "278"
    assert response.headers["x-repo-commit"] == "abc123"


@pytest.mark.asyncio
async def test_try_fallback_resolve_head_strips_xet_signals(monkeypatch):
    """Xet response headers must be removed so the client stays on the
    classic LFS flow."""
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://hf.local", "name": "HF", "source_type": "huggingface"},
        ],
    )
    path = "/models/owner/demo/resolve/main/weights.safetensors"
    FakeFallbackClient.queue(
        "https://hf.local", "HEAD", path,
        _content_response(
            307,
            headers={
                "location": "https://cas-bridge.xethub.hf.co/shard",
                "x-linked-size": "42",
                "x-xet-hash": "SHOULD_BE_GONE",
                "link": '<https://cas/auth>; rel="xet-auth", <https://next>; rel="next"',
            },
            url="https://hf.local/models/owner/demo/resolve/main/weights.safetensors",
        ),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "weights.safetensors", method="HEAD",
    )

    lower = {k.lower() for k in response.headers.keys()}
    assert not any(k.startswith("x-xet-") for k in lower)
    assert "xet-auth" not in response.headers["link"].lower()
    assert 'rel="next"' in response.headers["link"]


@pytest.mark.asyncio
async def test_try_fallback_resolve_get_strips_xet_signals(monkeypatch):
    """GET proxying must also drop X-Xet-* headers so the client stays on classic LFS."""
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://hf.local", "name": "HF", "source_type": "huggingface"},
        ],
    )
    path = "/models/owner/demo/resolve/main/weights.safetensors"
    FakeFallbackClient.queue(
        "https://hf.local", "HEAD", path, _content_response(200),
    )
    FakeFallbackClient.queue(
        "https://hf.local",
        "GET",
        path,
        _content_response(
            200,
            b"fake-bytes",
            headers={
                "content-type": "application/octet-stream",
                "x-xet-hash": "shardhash",
                "x-xet-cas-url": "https://cas-bridge.xethub.hf.co",
                "etag": '"deadbeef"',
            },
        ),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "weights.safetensors", method="GET",
    )

    assert response.status_code == 200
    assert response.body == b"fake-bytes"
    lower_headers = {k.lower() for k in response.headers.keys()}
    assert not any(k.startswith("x-xet-") for k in lower_headers)
    assert response.headers["etag"] == '"deadbeef"'


@pytest.mark.asyncio
async def test_try_fallback_resolve_continues_past_every_failure_and_aggregates(monkeypatch):
    """Every source must be probed even when the first ones fail with
    non-retryable statuses. Previously the loop exited on the first 4xx
    that `should_retry_source` flagged (e.g. 401), which meant a gated
    first source would hide a healthy third source that would have
    served the file. See `build_aggregate_failure_response` for the
    status-priority rules that decide the final HTTP code."""
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://timeout.local", "name": "Timeout", "source_type": "huggingface"},
            {"url": "https://auth.local", "name": "Auth", "source_type": "huggingface"},
            {"url": "https://missing.local", "name": "Missing", "source_type": "huggingface"},
        ],
    )
    path = "/models/owner/demo/resolve/main/config.json"
    FakeFallbackClient.queue(
        "https://timeout.local",
        "HEAD",
        path,
        httpx.TimeoutException("too slow"),
    )
    # 401 WITH X-Error-Code=GatedRepo — a genuinely gated repo (not the
    # bare-401 anti-enumeration shape HF uses for missing repos).
    FakeFallbackClient.queue(
        "https://auth.local",
        "HEAD",
        path,
        _content_response(401, headers={"X-Error-Code": "GatedRepo"}),
    )
    FakeFallbackClient.queue("https://missing.local", "HEAD", path, _content_response(404))

    response = await fallback_ops.try_fallback_resolve(
        "model",
        "owner",
        "demo",
        "main",
        "config.json",
    )

    # All three sources must have been tried — a 401 on source 2 no
    # longer skips source 3.
    assert [call[0] for call in FakeFallbackClient.calls] == [
        "https://timeout.local",
        "https://auth.local",
        "https://missing.local",
    ]

    # Aggregate: timeout + 401(GatedRepo) + 404. Auth wins the priority
    # contest because it's the most actionable status to surface.
    assert response is not None
    assert response.status_code == 401
    assert response.headers.get("x-error-code") == "GatedRepo"
    body = _decode_aggregate_body(response)
    assert [s["category"] for s in body["sources"]] == [
        "timeout",
        "auth",
        "not-found",
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
        httpx.Response(
            200,
            json=[{"path": "folder/file.txt"}],
            headers={
                "content-type": "application/json",
                "link": '</api/models/owner/demo/tree/main/folder/file.txt?cursor=page-2>; rel="next"',
            },
            request=httpx.Request(
                "GET", "https://source.local/api/models/owner/demo/tree/main/folder/file.txt"
            ),
        ),
    )
    FakeFallbackClient.queue(
        "https://source.local",
        "POST",
        "/api/models/owner/demo/paths-info/main",
        _json_response(200, [{"path": "folder/file.txt", "type": "file"}]),
    )

    info = await fallback_ops.try_fallback_info("model", "owner", "demo")
    tree = await fallback_ops.try_fallback_tree(
        "model",
        "owner",
        "demo",
        "main",
        "/folder/file.txt",
        recursive=True,
        expand=True,
        limit=25,
        cursor="page-1",
    )
    paths_info = await fallback_ops.try_fallback_paths_info(
        "model",
        "owner",
        "demo",
        "main",
        ["folder/file.txt"],
        expand=True,
    )

    assert info["_source"] == "Source"
    assert info["_source_url"] == "https://source.local"
    assert tree.status_code == 200
    assert tree.body == b'[{"path":"folder/file.txt"}]'
    assert tree.headers["link"] == '</api/models/owner/demo/tree/main/folder/file.txt?cursor=page-2>; rel="next"'
    assert paths_info == [{"path": "folder/file.txt", "type": "file"}]
    assert cache.set_calls[0][0][:3] == ("model", "owner", "demo")
    assert FakeFallbackClient.calls[1][3]["params"] == {
        "recursive": True,
        "expand": True,
        "limit": 25,
        "cursor": "page-1",
    }
    assert FakeFallbackClient.calls[-1][3]["data"] == {
        "paths": ["folder/file.txt"],
        "expand": True,
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


@pytest.mark.asyncio
async def test_try_fallback_resolve_wraps_generic_transport_failure_as_502(monkeypatch):
    """A generic transport-level exception (DNS failure, connection
    reset, broken TLS handshake, ...) from the only source is recorded
    as a ``network`` attempt and bubbled up as a 502 Bad Gateway
    aggregate, not discarded. The client needs to know the upstream was
    unreachable rather than seeing a misleading local 404."""
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://broken.local", "name": "Broken", "source_type": "huggingface"}
        ],
    )
    path = "/models/owner/demo/resolve/main/config.json"
    FakeFallbackClient.queue(
        "https://broken.local",
        "HEAD",
        path,
        RuntimeError("boom"),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model",
        "owner",
        "demo",
        "main",
        "config.json",
    )

    assert response is not None
    assert response.status_code == 502
    assert response.headers.get("x-error-code") is None
    body = _decode_aggregate_body(response)
    assert body["error"] == "UpstreamFailure"
    assert len(body["sources"]) == 1
    assert body["sources"][0]["category"] == "network"
    assert "boom" in body["sources"][0]["message"]


# -------------------------------------------------------------------------
# Upstream-error classification contract for fallback resolve.
#
# When a fallback source returns a non-success status, or when a source
# fails with a timeout / network error, try_fallback_resolve records a
# per-source "attempt" and continues to the next source. A mirror that
# doesn't gate the artifact the first source gates, or a mirror that
# simply has a file the first source doesn't, can still serve the
# request — which is the whole point of a multi-source fallback chain.
#
# If every source fails, the function returns an HTTP-level aggregate
# response that pins the information the client needs to pick a
# remediation:
#
#   HTTP status priority:   401 > 403 > 404 > 502
#   X-Error-Code (aligned with huggingface_hub.utils._http):
#       401 → GatedRepo         (→ GatedRepoError on hf_hub_download)
#       404 (all attempts)
#                       → EntryNotFound    (→ EntryNotFoundError)
#       403, 502        → unset             (HF falls back to generic)
#   Body:              { error, detail, sources: [...] }
#   Each sources[*]:   { name, url, status|null, category, message }
#
# Reproduced live against animetimm/mobilenetv3_large_150d.dbv4-full
# (gated model) while developing PR#28. Before this contract the client
# saw a bare 404 RepoNotFound for a file whose repo it had just listed.
# -------------------------------------------------------------------------


def _decode_aggregate_body(response):
    """Parse the structured failure body."""
    import json

    return json.loads(bytes(response.body).decode("utf-8"))


@pytest.mark.asyncio
async def test_try_fallback_resolve_surfaces_upstream_401_as_aggregated_error(
    monkeypatch,
):
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {
                "url": "https://gated.local",
                "name": "GatedHF",
                "source_type": "huggingface",
            }
        ],
    )
    path = "/models/animetimm/gated-demo/resolve/main/model.safetensors"
    gated_body = (
        b"Access to model animetimm/gated-demo is restricted. "
        b"You must have access to it and be authenticated to access "
        b"it. Please log in."
    )
    FakeFallbackClient.queue(
        "https://gated.local",
        "HEAD",
        path,
        _content_response(
            401,
            content=gated_body,
            # X-Error-Code=GatedRepo is what HF sets only when the repo
            # actually exists and is gated — bare 401 means the repo
            # doesn't exist, see test_try_fallback_resolve_bare_401...
            headers={
                "content-type": "text/plain; charset=utf-8",
                "X-Error-Code": "GatedRepo",
            },
        ),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model",
        "animetimm",
        "gated-demo",
        "main",
        "model.safetensors",
        method="HEAD",
    )

    assert response is not None, (
        "fallback must not discard the only 401 attempt — the client "
        "needs the status + upstream body to render an 'auth required' "
        "affordance instead of a misleading 'repo not found'"
    )
    assert response.status_code == 401
    # X-Error-Code uses the huggingface_hub classification so that
    # hf_hub_download raises GatedRepoError (see HF compat test).
    assert response.headers.get("x-error-code") == "GatedRepo"
    # X-Error-Message is the same human-readable summary HF echoes into
    # exception text, so even a bare curl -I user sees something useful.
    assert response.headers.get("x-error-message")

    body = _decode_aggregate_body(response)
    assert body["error"] == "GatedRepo"
    assert len(body["sources"]) == 1
    entry = body["sources"][0]
    assert entry["name"] == "GatedHF"
    assert entry["url"] == "https://gated.local"
    assert entry["status"] == 401
    assert entry["category"] == "auth"
    assert "restricted" in entry["message"]


@pytest.mark.asyncio
async def test_try_fallback_resolve_surfaces_upstream_403_as_aggregated_error(
    monkeypatch,
):
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {
                "url": "https://forbidden.local",
                "name": "ForbiddenHF",
                "source_type": "huggingface",
            }
        ],
    )
    path = "/models/owner/forbidden-demo/resolve/main/model.bin"
    FakeFallbackClient.queue(
        "https://forbidden.local",
        "HEAD",
        path,
        _content_response(
            403,
            content=b"Forbidden: this IP range is denied access.",
            headers={"content-type": "text/plain; charset=utf-8"},
        ),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "forbidden-demo", "main", "model.bin", method="HEAD",
    )

    assert response is not None
    assert response.status_code == 403
    # 403 has no specific HF X-Error-Code mapping — the client sees the
    # plain status and falls back to generic HfHubHTTPError, which is
    # what HF itself does for non-gated denies.
    assert response.headers.get("x-error-code") is None
    body = _decode_aggregate_body(response)
    assert body["error"] == "UpstreamFailure"
    entry = body["sources"][0]
    assert entry["status"] == 403
    assert entry["category"] == "forbidden"
    assert "Forbidden" in entry["message"]


@pytest.mark.asyncio
async def test_try_fallback_resolve_continues_past_401_and_succeeds_on_next_source(
    monkeypatch,
):
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {
                "url": "https://gated.local",
                "name": "GatedHF",
                "source_type": "huggingface",
            },
            {
                "url": "https://mirror.local",
                "name": "OpenMirror",
                "source_type": "huggingface",
            },
        ],
    )
    path = "/models/owner/demo/resolve/main/weights.bin"
    # Source 1 is gated — 401 + X-Error-Code=GatedRepo, not bare 401
    # (which HF uses for non-existent repos and we would classify as
    # not-found instead).
    FakeFallbackClient.queue(
        "https://gated.local",
        "HEAD",
        path,
        _content_response(
            401, content=b"gated", headers={"X-Error-Code": "GatedRepo"}
        ),
    )
    # Source 2 happily serves the same file.
    FakeFallbackClient.queue(
        "https://mirror.local",
        "HEAD",
        path,
        _content_response(307, headers={"etag": "mirror-etag"}),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "weights.bin", method="HEAD",
    )

    assert response is not None
    assert response.status_code == 307
    assert response.headers.get("etag") == "mirror-etag"
    assert response.headers.get("X-Source") == "OpenMirror"
    # Both sources should have been tried.
    tried = [call[0] for call in FakeFallbackClient.calls]
    assert tried == ["https://gated.local", "https://mirror.local"]


@pytest.mark.asyncio
async def test_try_fallback_resolve_aggregates_mixed_failures_across_sources(
    monkeypatch,
):
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {
                "url": "https://gated.local",
                "name": "GatedHF",
                "source_type": "huggingface",
            },
            {
                "url": "https://missing.local",
                "name": "MissingMirror",
                "source_type": "huggingface",
            },
            {
                "url": "https://broken.local",
                "name": "BrokenMirror",
                "source_type": "huggingface",
            },
            {
                "url": "https://slow.local",
                "name": "SlowMirror",
                "source_type": "huggingface",
            },
        ],
    )
    path = "/models/owner/demo/resolve/main/file.bin"
    FakeFallbackClient.queue(
        "https://gated.local", "HEAD", path,
        _content_response(
            401,
            content=b"Auth required",
            headers={"X-Error-Code": "GatedRepo"},
        ),
    )
    FakeFallbackClient.queue(
        "https://missing.local", "HEAD", path,
        _content_response(404, content=b"Not found"),
    )
    FakeFallbackClient.queue(
        "https://broken.local", "HEAD", path,
        _content_response(503, content=b"Service unavailable"),
    )
    FakeFallbackClient.queue(
        "https://slow.local", "HEAD", path,
        httpx.TimeoutException("too slow"),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "file.bin", method="HEAD",
    )

    assert response is not None
    # 401 is the most actionable status in the mix, so it bubbles up.
    assert response.status_code == 401
    assert response.headers.get("x-error-code") == "GatedRepo"

    body = _decode_aggregate_body(response)
    assert body["error"] == "GatedRepo"
    assert len(body["sources"]) == 4

    by_name = {s["name"]: s for s in body["sources"]}
    assert by_name["GatedHF"]["status"] == 401
    assert by_name["GatedHF"]["category"] == "auth"

    assert by_name["MissingMirror"]["status"] == 404
    assert by_name["MissingMirror"]["category"] == "not-found"

    assert by_name["BrokenMirror"]["status"] == 503
    assert by_name["BrokenMirror"]["category"] == "server"

    # Timeout / network failures have no HTTP status — null, not omitted.
    assert by_name["SlowMirror"]["status"] is None
    assert by_name["SlowMirror"]["category"] == "timeout"
    assert "slow" in by_name["SlowMirror"]["message"].lower()

    # Order is the probe order (stable for debuggability).
    assert [s["name"] for s in body["sources"]] == [
        "GatedHF",
        "MissingMirror",
        "BrokenMirror",
        "SlowMirror",
    ]


@pytest.mark.asyncio
async def test_try_fallback_resolve_aggregates_all_404_into_upstream_not_found(
    monkeypatch,
):
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://a.local", "name": "A", "source_type": "huggingface"},
            {"url": "https://b.local", "name": "B", "source_type": "huggingface"},
        ],
    )
    path = "/models/owner/demo/resolve/main/nope.bin"
    FakeFallbackClient.queue(
        "https://a.local", "HEAD", path, _content_response(404),
    )
    FakeFallbackClient.queue(
        "https://b.local", "HEAD", path, _content_response(404),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "nope.bin", method="HEAD",
    )

    assert response is not None
    assert response.status_code == 404
    # EntryNotFound aligns with huggingface_hub's per-file miss
    # classification so hf_hub_download raises EntryNotFoundError.
    assert response.headers.get("x-error-code") == "EntryNotFound"
    body = _decode_aggregate_body(response)
    assert body["error"] == "EntryNotFound"
    assert [s["status"] for s in body["sources"]] == [404, 404]


@pytest.mark.asyncio
async def test_try_fallback_resolve_aggregates_all_unavailable_into_502(
    monkeypatch,
):
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://x.local", "name": "X", "source_type": "huggingface"},
            {"url": "https://y.local", "name": "Y", "source_type": "huggingface"},
        ],
    )
    path = "/models/owner/demo/resolve/main/thing.bin"
    FakeFallbackClient.queue(
        "https://x.local", "HEAD", path, _content_response(500),
    )
    FakeFallbackClient.queue(
        "https://y.local", "HEAD", path, httpx.TimeoutException("too slow"),
    )

    response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "thing.bin", method="HEAD",
    )

    assert response is not None
    assert response.status_code == 502
    # 5xx / timeout / network mixes get no specific X-Error-Code — the
    # HF client's generic 5xx retry path is the right escape hatch.
    assert response.headers.get("x-error-code") is None
    body = _decode_aggregate_body(response)
    assert body["error"] == "UpstreamFailure"
    categories = [s["category"] for s in body["sources"]]
    assert categories == ["server", "timeout"]


@pytest.mark.asyncio
async def test_try_fallback_info_tree_and_paths_info_cover_cached_and_failure_paths(
    monkeypatch,
):
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
    # Single 403 → aggregated 403 (no X-Error-Code — HF has no specific
    # code for plain 403). Contract parity with try_fallback_resolve.
    FakeFallbackClient.queue(
        "https://secondary.local",
        "GET",
        "/api/models/owner/demo",
        _content_response(403),
    )
    info_resp = await fallback_ops.try_fallback_info("model", "owner", "demo")
    assert info_resp is not None
    assert info_resp.status_code == 403
    assert FakeFallbackClient.calls[0][0] == "https://secondary.local"

    FakeFallbackClient.reset()
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://info.local", "name": "Info", "source_type": "huggingface"}
        ],
    )
    FakeFallbackClient.queue(
        "https://info.local",
        "GET",
        "/api/models/owner/demo",
        RuntimeError("info failed"),
    )
    # Transport-level failure classifies as `network` → aggregate 502.
    info_resp_502 = await fallback_ops.try_fallback_info("model", "owner", "demo")
    assert info_resp_502 is not None
    assert info_resp_502.status_code == 502

    # No sources enabled → still returns None (nothing to aggregate).
    monkeypatch.setattr(fallback_ops, "get_enabled_sources", lambda namespace, user_tokens=None: [])
    assert await fallback_ops.try_fallback_info("model", "owner", "demo") is None
    assert await fallback_ops.try_fallback_tree("model", "owner", "demo", "main") is None
    assert (
        await fallback_ops.try_fallback_paths_info(
            "model",
            "owner",
            "demo",
            "main",
            ["README.md"],
        )
        is None
    )

    # tree is repo-level — all-404 should classify as RepoNotFound,
    # not EntryNotFound, so hf_hub raises RepositoryNotFoundError.
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://tree.local", "name": "Tree", "source_type": "huggingface"}
        ],
    )
    FakeFallbackClient.queue(
        "https://tree.local",
        "GET",
        "/api/models/owner/demo/tree/main/",
        _content_response(404),
    )
    tree_resp = await fallback_ops.try_fallback_tree("model", "owner", "demo", "main")
    assert tree_resp is not None
    assert tree_resp.status_code == 404
    assert tree_resp.headers.get("x-error-code") == "RepoNotFound"

    FakeFallbackClient.reset()
    FakeFallbackClient.queue(
        "https://tree.local",
        "GET",
        "/api/models/owner/demo/tree/main/",
        RuntimeError("tree failed"),
    )
    tree_resp_502 = await fallback_ops.try_fallback_tree(
        "model", "owner", "demo", "main",
    )
    assert tree_resp_502 is not None
    assert tree_resp_502.status_code == 502

    # paths-info is per-file → all-404 keeps EntryNotFound so hf_hub
    # raises EntryNotFoundError for a truly-missing entry.
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": "https://paths.local", "name": "Paths", "source_type": "huggingface"}
        ],
    )
    FakeFallbackClient.queue(
        "https://paths.local",
        "POST",
        "/api/models/owner/demo/paths-info/main",
        _content_response(404),
    )
    paths_resp = await fallback_ops.try_fallback_paths_info(
        "model", "owner", "demo", "main", ["README.md"],
    )
    assert paths_resp is not None
    assert paths_resp.status_code == 404
    assert paths_resp.headers.get("x-error-code") == "EntryNotFound"

    FakeFallbackClient.reset()
    FakeFallbackClient.queue(
        "https://paths.local",
        "POST",
        "/api/models/owner/demo/paths-info/main",
        RuntimeError("paths failed"),
    )
    paths_resp_502 = await fallback_ops.try_fallback_paths_info(
        "model", "owner", "demo", "main", ["README.md"],
    )
    assert paths_resp_502 is not None
    assert paths_resp_502.status_code == 502


@pytest.mark.asyncio
async def test_fetch_external_list_returns_empty_for_non_success_status(monkeypatch):
    source = {"url": "https://source.local", "name": "Source", "source_type": "huggingface"}

    class SimpleClient:
        def __init__(self, source_url: str, source_type: str, token: str | None = None):
            self.timeout = 9
            self.source_url = source_url

        def map_url(self, kohaku_path: str, repo_type: str) -> str:
            return f"{self.source_url}{kohaku_path}"

    class FailingStatusHTTPClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str, params: dict):
            return _content_response(500, b"upstream failure", url=url)

    monkeypatch.setattr(fallback_ops, "FallbackClient", SimpleClient)
    monkeypatch.setattr(fallback_ops.httpx, "AsyncClient", FailingStatusHTTPClient)

    assert await fallback_ops.fetch_external_list(source, "model", {"author": "owner"}) == []


@pytest.mark.asyncio
async def test_try_fallback_user_profile_covers_empty_hf_miss_and_non_retryable_kohakuhub(
    monkeypatch,
):
    monkeypatch.setattr(fallback_ops, "get_enabled_sources", lambda namespace="", user_tokens=None: [])
    assert await fallback_ops.try_fallback_user_profile("alice") is None

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
        "/api/users/alice/overview",
        _content_response(404),
    )
    FakeFallbackClient.queue(
        "https://hf.local",
        "GET",
        "/api/organizations/alice/members",
        _content_response(404),
    )
    assert await fallback_ops.try_fallback_user_profile("alice") is None

    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace="", user_tokens=None: [
            {"url": "https://unknown.local", "name": "Unknown", "source_type": "other"},
            {"url": "https://kohaku.local", "name": "Kohaku", "source_type": "kohakuhub"},
        ],
    )
    FakeFallbackClient.queue(
        "https://kohaku.local",
        "GET",
        "/api/users/bob/profile",
        _content_response(403),
    )
    assert await fallback_ops.try_fallback_user_profile("bob") is None


@pytest.mark.asyncio
async def test_try_fallback_user_and_org_avatar_cover_empty_and_non_retryable_paths(
    monkeypatch,
):
    monkeypatch.setattr(fallback_ops, "get_enabled_sources", lambda namespace="", user_tokens=None: [])
    assert await fallback_ops.try_fallback_user_avatar("alice") is None
    assert await fallback_ops.try_fallback_org_avatar("acme") is None

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
        "/api/users/alice/overview",
        _json_response(200, {"name": "Alice"}),
    )
    assert await fallback_ops.try_fallback_user_avatar("alice") is None

    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace="", user_tokens=None: [
            {"url": "https://unknown.local", "name": "Unknown", "source_type": "other"},
            {"url": "https://kohaku.local", "name": "Kohaku", "source_type": "kohakuhub"},
        ],
    )
    FakeFallbackClient.queue(
        "https://kohaku.local",
        "GET",
        "/api/users/bob/avatar",
        _content_response(403),
    )
    assert await fallback_ops.try_fallback_user_avatar("bob") is None

    FakeFallbackClient.reset()
    FakeFallbackClient.queue(
        "https://kohaku.local",
        "GET",
        "/api/organizations/acme/avatar",
        _content_response(403),
    )
    assert await fallback_ops.try_fallback_org_avatar("acme") is None

    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace="", user_tokens=None: [
            {"url": "https://unknown.local", "name": "Unknown", "source_type": "other"},
            {"url": "https://broken.local", "name": "Broken", "source_type": "kohakuhub"},
        ],
    )
    FakeFallbackClient.queue(
        "https://broken.local",
        "GET",
        "/api/organizations/acme/avatar",
        RuntimeError("avatar failed"),
    )
    assert await fallback_ops.try_fallback_org_avatar("acme") is None


@pytest.mark.asyncio
async def test_try_fallback_user_repos_covers_empty_dataset_success_and_failure_paths(
    monkeypatch,
):
    monkeypatch.setattr(fallback_ops, "get_enabled_sources", lambda namespace="", user_tokens=None: [])
    assert await fallback_ops.try_fallback_user_repos("alice") is None

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
        _json_response(200, [{"id": "alice/dataset-a"}]),
    )
    FakeFallbackClient.queue(
        "https://hf.local",
        "GET",
        "/api/spaces?author=alice&limit=100",
        _json_response(200, [{"id": "alice/space-a"}]),
    )
    hf_repos = await fallback_ops.try_fallback_user_repos("alice")
    assert hf_repos["datasets"][0]["_source"] == "HF"

    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace="", user_tokens=None: [
            {"url": "https://unknown.local", "name": "Unknown", "source_type": "other"},
            {"url": "https://kohaku.local", "name": "Kohaku", "source_type": "kohakuhub"},
        ],
    )
    FakeFallbackClient.queue(
        "https://kohaku.local",
        "GET",
        "/api/users/bob/repos",
        _content_response(403),
    )
    assert await fallback_ops.try_fallback_user_repos("bob") is None

    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace="", user_tokens=None: [
            {"url": "https://broken.local", "name": "Broken", "source_type": "kohakuhub"}
        ],
    )
    FakeFallbackClient.queue(
        "https://broken.local",
        "GET",
        "/api/users/carol/repos",
        RuntimeError("repos failed"),
    )
    assert await fallback_ops.try_fallback_user_repos("carol") is None
