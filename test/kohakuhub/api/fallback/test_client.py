"""Tests for fallback HTTP client helpers."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

import kohakuhub.api.fallback.client as fallback_client_module


def test_map_url_matches_huggingface_and_kohakuhub_rules(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(fallback_client_module.logger, "warning", warnings.append)

    kohaku_client = fallback_client_module.FallbackClient(
        "https://mirror.local/",
        "kohakuhub",
        token="secret",
    )
    hf_client = fallback_client_module.FallbackClient("https://huggingface.co/", "huggingface")

    assert (
        kohaku_client.map_url("/models/owner/demo/resolve/main/file.bin", "model")
        == "https://mirror.local/models/owner/demo/resolve/main/file.bin"
    )
    assert (
        hf_client.map_url("/api/models/owner/demo", "model")
        == "https://huggingface.co/api/models/owner/demo"
    )
    assert (
        hf_client.map_url("/models/owner/demo/resolve/main/file.bin", "model")
        == "https://huggingface.co/owner/demo/resolve/main/file.bin"
    )
    assert (
        hf_client.map_url("/datasets/owner/demo/resolve/main/file.bin", "dataset")
        == "https://huggingface.co/datasets/owner/demo/resolve/main/file.bin"
    )
    assert (
        hf_client.map_url("/spaces/owner/demo/resolve/main/app.py", "space")
        == "https://huggingface.co/spaces/owner/demo/resolve/main/app.py"
    )
    assert (
        hf_client.map_url("/models/owner/demo/resolve/main/file.bin", "dataset")
        == "https://huggingface.co/models/owner/demo/resolve/main/file.bin"
    )
    assert warnings == [
        "Unexpected resolve path pattern: /models/owner/demo/resolve/main/file.bin for type dataset"
    ]


def test_map_url_rejects_unknown_source_type():
    client = fallback_client_module.FallbackClient("https://example.com", "unknown")

    with pytest.raises(ValueError, match="Unknown source_type"):
        client.map_url("/api/models/owner/demo", "model")


@dataclass
class AsyncClientCall:
    """Captured async client call."""

    method: str
    url: str
    kwargs: dict


class FakeAsyncClient:
    """Minimal async client stub used to capture request parameters."""

    calls: list[AsyncClientCall] = []

    def __init__(self, *, timeout: int):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url: str, **kwargs):
        self.calls.append(AsyncClientCall("get", url, kwargs))
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", url))

    async def head(self, url: str, **kwargs):
        self.calls.append(AsyncClientCall("head", url, kwargs))
        return httpx.Response(204, request=httpx.Request("HEAD", url))

    async def post(self, url: str, **kwargs):
        self.calls.append(AsyncClientCall("post", url, kwargs))
        return httpx.Response(201, json={"created": True}, request=httpx.Request("POST", url))


@pytest.mark.asyncio
async def test_http_methods_forward_headers_timeouts_and_redirect_flags(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(fallback_client_module.cfg.fallback, "timeout_seconds", 12)
    monkeypatch.setattr(fallback_client_module.httpx, "AsyncClient", FakeAsyncClient)

    client = fallback_client_module.FallbackClient(
        "https://huggingface.co",
        "huggingface",
        token="user-token",
    )

    get_response = await client.get(
        "/api/models/owner/demo",
        "model",
        follow_redirects=False,
        headers={"X-Test": "1"},
        params={"limit": 10},
    )
    head_response = await client.head(
        "/datasets/owner/demo/resolve/main/file.bin",
        "dataset",
        headers={"X-Head": "1"},
    )
    post_response = await client.post(
        "/api/models/owner/demo/branch/feature",
        "model",
        json={"startingPoint": "main"},
    )

    assert get_response.status_code == 200
    assert head_response.status_code == 204
    assert post_response.status_code == 201
    assert [call.method for call in FakeAsyncClient.calls] == ["get", "head", "post"]
    assert FakeAsyncClient.calls[0].url == "https://huggingface.co/api/models/owner/demo"
    assert FakeAsyncClient.calls[0].kwargs == {
        "headers": {"X-Test": "1", "Authorization": "Bearer user-token"},
        "follow_redirects": False,
        "params": {"limit": 10},
    }
    assert FakeAsyncClient.calls[1].kwargs["headers"] == {
        "X-Head": "1",
        "Authorization": "Bearer user-token",
    }
    assert FakeAsyncClient.calls[2].kwargs["headers"] == {
        "Authorization": "Bearer user-token"
    }
