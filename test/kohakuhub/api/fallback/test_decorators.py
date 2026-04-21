"""Tests for fallback decorators."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
import pytest

import kohakuhub.api.fallback.decorators as fallback_decorators


def _request(
    path: str,
    *,
    method: str = "GET",
    query: dict[str, str] | None = None,
    external_tokens: dict[str, str] | None = None,
):
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        query_params=query or {},
        state=SimpleNamespace(external_tokens=external_tokens or {}),
    )


@pytest.fixture(autouse=True)
def _enable_fallback(monkeypatch):
    monkeypatch.setattr(fallback_decorators.cfg.fallback, "enabled", True)
    monkeypatch.setattr(fallback_decorators.cfg.app, "base_url", "https://hub.local")


@pytest.mark.asyncio
async def test_with_repo_fallback_respects_query_override_and_local_success(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("fallback should not be called")

    monkeypatch.setattr(fallback_decorators, "try_fallback_info", fail_if_called)

    @fallback_decorators.with_repo_fallback("info")
    async def handler(namespace: str, name: str, request=None, fallback: bool = True):
        return {"local": True}

    request = _request("/api/models/owner/demo", query={"fallback": "false"})

    assert await handler(namespace="owner", name="demo", request=request) == {
        "local": True
    }


@pytest.mark.asyncio
async def test_with_repo_fallback_uses_resolve_operation_after_http_404(monkeypatch):
    merged_inputs = []
    resolve_calls = []

    monkeypatch.setattr(
        fallback_decorators,
        "get_merged_external_tokens",
        lambda user, header_tokens: merged_inputs.append((user, header_tokens))
        or {"https://hf.local": "token"},
    )

    async def fake_try_fallback_resolve(*args, **kwargs):
        resolve_calls.append((args, kwargs))
        return {"resolved": True}

    monkeypatch.setattr(fallback_decorators, "try_fallback_resolve", fake_try_fallback_resolve)

    @fallback_decorators.with_repo_fallback("resolve")
    async def handler(repo_type=None, namespace: str = "", name: str = "", revision: str = "", path: str = "", request=None, user=None):
        raise HTTPException(status_code=404, detail="missing")

    request = _request(
        "/datasets/owner/demo/resolve/main/config.json",
        method="HEAD",
        external_tokens={"https://hf.local": "header-token"},
    )
    repo_type = SimpleNamespace(value="dataset")

    result = await handler(
        repo_type=repo_type,
        namespace="owner",
        name="demo",
        revision="main",
        path="config.json",
        request=request,
        user="owner-user",
    )

    assert result == {"resolved": True}
    assert merged_inputs == [("owner-user", {"https://hf.local": "header-token"})]
    assert resolve_calls == [
        (
            ("dataset", "owner", "demo", "main", "config.json"),
            {"user_tokens": {"https://hf.local": "token"}, "method": "HEAD"},
        )
    ]


@pytest.mark.asyncio
async def test_with_repo_fallback_returns_original_response_on_fallback_miss(monkeypatch):
    async def fake_try_fallback_tree(*args, **kwargs):
        return None

    monkeypatch.setattr(fallback_decorators, "try_fallback_tree", fake_try_fallback_tree)
    monkeypatch.setattr(
        fallback_decorators,
        "get_merged_external_tokens",
        lambda user, header_tokens: {},
    )

    original = JSONResponse(status_code=404, content={"detail": "missing"})

    @fallback_decorators.with_repo_fallback("tree")
    async def handler(namespace: str, name: str, revision: str, path: str = "", request=None):
        return original

    request = _request("/spaces/acme/demo/tree/main")
    result = await handler(namespace="acme", name="demo", revision="main", request=request)

    assert result is original


@pytest.mark.asyncio
async def test_with_repo_fallback_forwards_tree_and_paths_info_expand_parameters(monkeypatch):
    forwarded_tree_calls = []
    forwarded_paths_info_calls = []

    monkeypatch.setattr(
        fallback_decorators,
        "get_merged_external_tokens",
        lambda user, header_tokens: {"https://hf.local": "token"},
    )

    async def fake_try_fallback_tree(*args, **kwargs):
        forwarded_tree_calls.append((args, kwargs))
        return {"tree": True}

    async def fake_try_fallback_paths_info(*args, **kwargs):
        forwarded_paths_info_calls.append((args, kwargs))
        return [{"path": "README.md"}]

    monkeypatch.setattr(fallback_decorators, "try_fallback_tree", fake_try_fallback_tree)
    monkeypatch.setattr(
        fallback_decorators,
        "try_fallback_paths_info",
        fake_try_fallback_paths_info,
    )

    @fallback_decorators.with_repo_fallback("tree")
    async def tree_handler(
        namespace: str,
        name: str,
        revision: str,
        path: str = "",
        recursive: bool = False,
        expand: bool = False,
        limit: int | None = None,
        cursor: str | None = None,
        request=None,
        user=None,
    ):
        raise HTTPException(status_code=404, detail="missing")

    @fallback_decorators.with_repo_fallback("paths_info")
    async def paths_info_handler(
        repo_type=None,
        namespace: str = "",
        repo_name: str = "",
        revision: str = "",
        paths=None,
        expand: bool = False,
        request=None,
        user=None,
    ):
        raise HTTPException(status_code=404, detail="missing")

    tree_request = _request("/api/models/owner/demo/tree/main/docs")
    tree_result = await tree_handler(
        namespace="owner",
        name="demo",
        revision="main",
        path="docs",
        recursive=True,
        expand=True,
        limit=25,
        cursor="page-1",
        request=tree_request,
        user="owner-user",
    )
    assert tree_result == {"tree": True}
    assert forwarded_tree_calls == [
        (
            ("model", "owner", "demo", "main", "docs"),
            {
                "recursive": True,
                "expand": True,
                "limit": 25,
                "cursor": "page-1",
                "user_tokens": {"https://hf.local": "token"},
            },
        )
    ]

    paths_info_request = _request("/api/models/owner/demo/paths-info/main")
    repo_type = SimpleNamespace(value="model")
    paths_info_result = await paths_info_handler(
        repo_type=repo_type,
        namespace="owner",
        repo_name="demo",
        revision="main",
        paths=["README.md", "docs"],
        expand=True,
        request=paths_info_request,
        user="owner-user",
    )
    assert paths_info_result == [{"path": "README.md"}]
    assert forwarded_paths_info_calls == [
        (
            ("model", "owner", "demo", "main", ["README.md", "docs"]),
            {
                "expand": True,
                "user_tokens": {"https://hf.local": "token"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_with_list_aggregation_merges_local_and_external_results(monkeypatch):
    monkeypatch.setattr(
        fallback_decorators,
        "get_merged_external_tokens",
        lambda user, header_tokens: {"https://hf.local": "token"},
    )
    monkeypatch.setattr(
        fallback_decorators,
        "get_enabled_sources",
        lambda namespace="", user_tokens=None: [
            {"url": "https://hf.local", "name": "HF", "source_type": "huggingface"}
        ],
    )

    async def fake_fetch_external_list(source, repo_type, query_params):
        assert query_params == {"author": "owner", "limit": 3, "sort": "updated"}
        return [
            {"id": "owner/model-b", "lastModified": "2025-01-03T00:00:00Z"},
            {"id": "owner/model-a", "lastModified": "2025-01-02T00:00:00Z"},
        ]

    monkeypatch.setattr(fallback_decorators, "fetch_external_list", fake_fetch_external_list)

    @fallback_decorators.with_list_aggregation("model")
    async def handler(author=None, limit=50, sort="recent", user=None, request=None, fallback=True):
        return [{"id": "owner/model-a", "lastModified": "2025-01-01T00:00:00Z"}]

    request = _request("/api/models", external_tokens={"https://hf.local": "header-token"})
    result = await handler("owner", 3, "updated", "owner-user", request=request)

    assert result == [
        {
            "id": "owner/model-b",
            "lastModified": "2025-01-03T00:00:00Z",
        },
        {
            "id": "owner/model-a",
            "lastModified": "2025-01-01T00:00:00Z",
            "_source": "local",
            "_source_url": "https://hub.local",
        },
    ]


@pytest.mark.asyncio
async def test_with_list_aggregation_bypasses_when_disabled_or_non_list(monkeypatch):
    @fallback_decorators.with_list_aggregation("dataset")
    async def handler(author=None, limit=50, sort="recent", user=None, request=None, fallback=True):
        return {"local": True}

    monkeypatch.setattr(fallback_decorators.cfg.fallback, "enabled", False)
    assert await handler("owner") == {"local": True}


@pytest.mark.asyncio
async def test_with_user_fallback_supports_profile_repos_and_avatar(monkeypatch):
    monkeypatch.setattr(
        fallback_decorators,
        "get_merged_external_tokens",
        lambda user, header_tokens: {"https://hf.local": "token"},
    )

    async def fake_profile(username, user_tokens=None):
        return {"username": username, "_source": "HF"}

    async def fake_repos(username, user_tokens=None):
        return {"models": [{"id": f"{username}/repo"}]}

    async def fake_org_avatar(org_name, user_tokens=None):
        return b"avatar-bytes"

    monkeypatch.setattr(fallback_decorators, "try_fallback_user_profile", fake_profile)
    monkeypatch.setattr(fallback_decorators, "try_fallback_user_repos", fake_repos)
    monkeypatch.setattr(fallback_decorators, "try_fallback_org_avatar", fake_org_avatar)

    @fallback_decorators.with_user_fallback("profile")
    async def profile_handler(username: str, request=None, user=None):
        raise HTTPException(status_code=404, detail="missing")

    @fallback_decorators.with_user_fallback("repos")
    async def repos_handler(username: str, request=None, user=None):
        return Response(status_code=404)

    @fallback_decorators.with_user_fallback("avatar")
    async def avatar_handler(org_name: str, request=None, user=None):
        raise HTTPException(status_code=404, detail="missing")

    request = _request("/api/users/alice/profile", external_tokens={"https://hf.local": "header-token"})

    profile = await profile_handler(username="alice", request=request, user="owner-user")
    repos = await repos_handler(username="alice", request=request, user="owner-user")
    avatar = await avatar_handler(org_name="acme", request=request, user="owner-user")

    assert profile == {"username": "alice", "_source": "HF"}
    assert repos == {"models": [{"id": "alice/repo"}]}
    assert avatar.body == b"avatar-bytes"
    assert avatar.media_type == "image/jpeg"
    assert avatar.headers["Cache-Control"] == "public, max-age=86400"


@pytest.mark.asyncio
async def test_with_user_fallback_re_raises_original_404_on_miss(monkeypatch):
    async def fake_avatar(username, user_tokens=None):
        return None

    monkeypatch.setattr(fallback_decorators, "try_fallback_user_avatar", fake_avatar)
    monkeypatch.setattr(
        fallback_decorators,
        "get_merged_external_tokens",
        lambda user, header_tokens: {},
    )

    @fallback_decorators.with_user_fallback("avatar")
    async def handler(username: str, request=None):
        raise HTTPException(status_code=404, detail="missing")

    with pytest.raises(HTTPException) as exc:
        await handler(username="alice", request=_request("/api/users/alice/avatar"))

    assert exc.value.status_code == 404
