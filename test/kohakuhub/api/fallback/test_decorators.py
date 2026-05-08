"""Tests for fallback decorators."""

from __future__ import annotations

import base64
import json
import httpx
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
import pytest

import kohakuhub.api.fallback.decorators as fallback_decorators


def _decode_body(result):
    """Return the JSON-decoded body of a ``JSONResponse``-wrapped result.

    The decorator now wraps dict / list returns in a ``JSONResponse`` so
    it can attach the ``X-Chain-Trace`` header (#78). Tests that used
    to compare ``result == {...}`` decode the wrapper's rendered body
    here and compare against that.
    """
    if isinstance(result, Response):
        return json.loads(bytes(result.body))
    return result


def _decode_trace_header(response: Response) -> list[dict]:
    """Decode ``X-Chain-Trace`` from a Response into the hop list."""
    header = response.headers.get("x-chain-trace") or response.headers.get(
        "X-Chain-Trace"
    )
    if not header:
        return []
    try:
        decoded = base64.b64decode(header).decode("utf-8")
        envelope = json.loads(decoded)
        hops = envelope.get("hops")
        return hops if isinstance(hops, list) else []
    except Exception:
        return []


def _request(
    path: str,
    *,
    method: str = "GET",
    query: dict[str, str] | None = None,
    external_tokens: dict[str, str] | None = None,
    probe_id: str | None = "test-probe",
):
    """Synthetic Request stand-in.

    Defaults to sending an ``X-Khub-Probe-Id`` header so the trace-
    emission path runs in the most-common test mode (chain tester is
    the primary consumer of X-Chain-Trace; production-anonymous
    callers shouldn't see it). Pass ``probe_id=None`` explicitly to
    test the anonymous (no-trace) path.
    """
    headers: dict[str, str] = {}
    if probe_id is not None:
        headers["X-Khub-Probe-Id"] = probe_id
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        query_params=query or {},
        state=SimpleNamespace(external_tokens=external_tokens or {}),
        headers=headers,
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

    assert _decode_body(result) == {"resolved": True}
    # The decorator now also injects X-Chain-Trace with at least the
    # local hop (LOCAL_MISS, since we raised 404) — see the dedicated
    # trace-emission tests below for full coverage.
    hops = _decode_trace_header(result)
    assert any(h.get("decision") == "LOCAL_MISS" for h in hops)
    assert merged_inputs == [("owner-user", {"https://hf.local": "header-token"})]
    assert len(resolve_calls) == 1
    args, kwargs = resolve_calls[0]
    assert args == ("dataset", "owner", "demo", "main", "config.json")
    assert kwargs["user_tokens"] == {"https://hf.local": "token"}
    assert kwargs["method"] == "HEAD"
    assert kwargs["user"] == "owner-user"
    # Plan A: the decorator now also threads client_headers (raw
    # request.headers) through; ``try_fallback_resolve`` is responsible
    # for whitelisting Range/If-* and dropping everything else.
    assert "client_headers" in kwargs


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
    assert _decode_body(tree_result) == {"tree": True}
    assert forwarded_tree_calls == [
        (
            ("model", "owner", "demo", "main", "docs"),
            {
                "recursive": True,
                "expand": True,
                "limit": 25,
                "cursor": "page-1",
                "user_tokens": {"https://hf.local": "token"},
                "user": "owner-user",
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
    assert _decode_body(paths_info_result) == [{"path": "README.md"}]
    assert forwarded_paths_info_calls == [
        (
            ("model", "owner", "demo", "main", ["README.md", "docs"]),
            {
                "expand": True,
                "user_tokens": {"https://hf.local": "token"},
                "user": "owner-user",
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


# ===========================================================================
# X-Error-Code gating: local 404 with EntryNotFound / RevisionNotFound
# means the local repo *exists*, only the entry/revision is missing.
# Per the strict-consistency contract, the fallback chain must NOT run
# in that case — a sibling source's same-named-but-different repo
# would mix data from two distinct repos for one ``repo_id``.
# Only RepoNotFound (or no X-Error-Code) triggers fallback.
# ===========================================================================


@pytest.mark.asyncio
async def test_with_repo_fallback_skips_chain_on_local_EntryNotFound_response(monkeypatch):
    """Local handler returns Response(404, X-Error-Code=EntryNotFound).
    The fallback decorator must surface that response unchanged
    instead of calling the chain."""

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("fallback chain must NOT run on local EntryNotFound")

    monkeypatch.setattr(fallback_decorators, "try_fallback_resolve", fail_if_called)

    local_404 = Response(
        status_code=404,
        headers={
            "X-Error-Code": "EntryNotFound",
            "X-Error-Message": "Entry 'foo' not found in repository 'owner/demo'",
        },
    )

    @fallback_decorators.with_repo_fallback("resolve")
    async def handler(
        repo_type, namespace, name, revision, path, request=None,
        fallback: bool = True, user=None,
    ):
        return local_404

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        revision="main", path="foo",
        request=_request("/owner/demo/resolve/main/foo"),
    )
    assert result is local_404
    assert result.status_code == 404
    assert result.headers["X-Error-Code"] == "EntryNotFound"


@pytest.mark.asyncio
async def test_with_repo_fallback_skips_chain_on_local_RevisionNotFound_response(monkeypatch):
    """Same gating but for X-Error-Code=RevisionNotFound."""

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("fallback must NOT run on local RevisionNotFound")

    monkeypatch.setattr(fallback_decorators, "try_fallback_tree", fail_if_called)

    local_404 = Response(
        status_code=404,
        headers={
            "X-Error-Code": "RevisionNotFound",
            "X-Error-Message": "Revision 'no-branch' not found in repository 'owner/demo'",
        },
    )

    @fallback_decorators.with_repo_fallback("tree")
    async def handler(
        repo_type, namespace, name, revision, path="", request=None,
        fallback: bool = True, user=None,
    ):
        return local_404

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        revision="no-branch",
        request=_request("/api/models/owner/demo/tree/no-branch"),
    )
    assert result is local_404


@pytest.mark.asyncio
async def test_with_repo_fallback_skips_chain_on_local_EntryNotFound_HTTPException(monkeypatch):
    """Same gating when the local handler raises
    ``HTTPException(headers={"X-Error-Code": "EntryNotFound"})`` rather
    than returning a Response. The decorator must re-raise the
    HTTPException without consulting the fallback chain. The local
    file-resolve handler in ``api/files.py:_get_file_metadata``
    raises HTTPException(headers=...) on missing-file."""

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("fallback must NOT run on local EntryNotFound HTTPException")

    monkeypatch.setattr(fallback_decorators, "try_fallback_resolve", fail_if_called)

    @fallback_decorators.with_repo_fallback("resolve")
    async def handler(
        repo_type, namespace, name, revision, path, request=None,
        fallback: bool = True, user=None,
    ):
        raise HTTPException(
            status_code=404,
            detail={"error": "Entry 'foo' not found"},
            headers={
                "X-Error-Code": "EntryNotFound",
                "X-Error-Message": "Entry 'foo' not found in repository 'owner/demo'",
            },
        )

    with pytest.raises(HTTPException) as exc:
        await handler(
            repo_type="model", namespace="owner", name="demo",
            revision="main", path="foo",
            request=_request("/owner/demo/resolve/main/foo"),
        )
    assert exc.value.status_code == 404
    assert (exc.value.headers or {}).get("X-Error-Code") == "EntryNotFound"


@pytest.mark.asyncio
async def test_with_repo_fallback_still_triggers_on_RepoNotFound(monkeypatch):
    """Regression guard: when the local handler signals
    ``X-Error-Code: RepoNotFound`` (or no X-Error-Code at all), the
    fallback chain MUST still run — otherwise we'd lose the entire
    "fall through to upstream when the local repo is missing"
    behavior the system is built around."""

    fallback_calls: list[tuple] = []

    async def stub_resolve(*args, **kwargs):
        fallback_calls.append((args, kwargs))
        return Response(status_code=200, content=b"from-fallback")

    monkeypatch.setattr(fallback_decorators, "try_fallback_resolve", stub_resolve)
    monkeypatch.setattr(fallback_decorators, "get_merged_external_tokens", lambda u, h: {})

    @fallback_decorators.with_repo_fallback("resolve")
    async def handler(
        repo_type, namespace, name, revision, path, request=None,
        fallback: bool = True, user=None,
    ):
        return Response(
            status_code=404,
            headers={
                "X-Error-Code": "RepoNotFound",
                "X-Error-Message": "Repository 'owner/demo' not found",
            },
        )

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        revision="main", path="foo",
        request=_request("/owner/demo/resolve/main/foo"),
    )
    # Fallback MUST have been called for RepoNotFound.
    assert len(fallback_calls) == 1
    assert result.body == b"from-fallback"


@pytest.mark.asyncio
async def test_with_repo_fallback_still_triggers_on_HTTPException_without_xerror(monkeypatch):
    """Defensive: a raw HTTPException(404) with no headers (or no
    ``X-Error-Code``) is treated as ``RepoNotFound`` semantics —
    fallback is triggered. This preserves back-compat for any local
    handler that doesn't (yet) attach the HF-compatible header set."""

    fallback_calls: list[tuple] = []

    async def stub_resolve(*args, **kwargs):
        fallback_calls.append((args, kwargs))
        return Response(status_code=200, content=b"from-fallback")

    monkeypatch.setattr(fallback_decorators, "try_fallback_resolve", stub_resolve)
    monkeypatch.setattr(fallback_decorators, "get_merged_external_tokens", lambda u, h: {})

    @fallback_decorators.with_repo_fallback("resolve")
    async def handler(
        repo_type, namespace, name, revision, path, request=None,
        fallback: bool = True, user=None,
    ):
        raise HTTPException(status_code=404, detail="No headers attached")

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        revision="main", path="foo",
        request=_request("/owner/demo/resolve/main/foo"),
    )
    assert len(fallback_calls) == 1
    assert result.body == b"from-fallback"


# ===========================================================================
# with_user_fallback: namespace-existence gating
# ===========================================================================


@pytest.mark.asyncio
async def test_with_user_fallback_local_user_short_circuits_fallback(monkeypatch):
    """If the local DB has a user with the requested username, the
    decorator does NOT call the fallback chain — the local handler
    is authoritative for every feature of that user (profile,
    avatar, repos), even when the local handler returns a 404
    (e.g., user has no avatar uploaded). Otherwise we'd silently
    pull a same-named HF user's avatar into our user's profile —
    a different person."""

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("fallback must NOT run when local user exists")

    monkeypatch.setattr(fallback_decorators, "try_fallback_user_avatar", fail_if_called)

    # Mock get_user_by_username / get_organization to simulate a
    # local user named 'alice'.
    monkeypatch.setattr(
        fallback_decorators, "get_user_by_username",
        lambda name: SimpleNamespace(id=1, username=name) if name == "alice" else None,
    )
    monkeypatch.setattr(
        fallback_decorators, "get_organization", lambda name: None,
    )

    @fallback_decorators.with_user_fallback("avatar")
    async def handler(username, request=None, fallback: bool = True):
        # Local handler "no avatar uploaded" — 404 from local.
        raise HTTPException(status_code=404, detail="No avatar set")

    with pytest.raises(HTTPException) as exc:
        await handler(username="alice", request=_request("/api/users/alice/avatar"))
    assert exc.value.status_code == 404
    assert "No avatar set" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_with_user_fallback_local_org_short_circuits_fallback(monkeypatch):
    """Same rule but for organizations."""

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("fallback must NOT run when local org exists")

    monkeypatch.setattr(fallback_decorators, "try_fallback_org_avatar", fail_if_called)

    monkeypatch.setattr(
        fallback_decorators, "get_user_by_username", lambda name: None,
    )
    monkeypatch.setattr(
        fallback_decorators, "get_organization",
        lambda name: SimpleNamespace(id=2, username=name) if name == "acme" else None,
    )

    @fallback_decorators.with_user_fallback("avatar")
    async def handler(org_name, request=None, fallback: bool = True):
        raise HTTPException(status_code=404, detail="No org avatar set")

    with pytest.raises(HTTPException) as exc:
        await handler(org_name="acme", request=_request("/api/organizations/acme/avatar"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_with_user_fallback_unknown_local_namespace_falls_through(monkeypatch):
    """Regression guard for ``with_user_fallback``: if neither user
    nor org exists locally, the chain MUST still run. The user/org
    namespace doesn't exist in this khub, so an upstream lookup is
    the right action."""

    fallback_calls: list[tuple] = []

    async def stub_profile(*args, **kwargs):
        fallback_calls.append((args, kwargs))
        return {"username": "nobody", "_source": "HF"}

    monkeypatch.setattr(fallback_decorators, "try_fallback_user_profile", stub_profile)
    monkeypatch.setattr(fallback_decorators, "get_merged_external_tokens", lambda u, h: {})
    monkeypatch.setattr(fallback_decorators, "get_user_by_username", lambda name: None)
    monkeypatch.setattr(fallback_decorators, "get_organization", lambda name: None)

    @fallback_decorators.with_user_fallback("profile")
    async def handler(username, request=None, fallback: bool = True):
        raise HTTPException(status_code=404, detail="User not found")

    result = await handler(username="nobody", request=_request("/api/users/nobody/profile"))
    assert len(fallback_calls) == 1
    assert result["_source"] == "HF"


# ===========================================================================
# Per-probe trace cookie (#78 v3): the chain tester sends an
# ``X-Khub-Probe-Id`` header so the decorator additionally Set-Cookie's
# the encoded trace under a per-probe name. Browsers strip
# redirect-chain response headers from JS visibility (W3C Fetch spec
# opaqueredirect filter), so the cookie is the only reliable pickup
# channel after a redirect-follow round trip.
# ===========================================================================


def _request_with_probe_id(path: str, probe_id: str | None, **kw):
    headers = {}
    if probe_id is not None:
        headers["X-Khub-Probe-Id"] = probe_id
    return SimpleNamespace(
        method=kw.get("method", "GET"),
        url=SimpleNamespace(path=path),
        query_params=kw.get("query") or {},
        state=SimpleNamespace(external_tokens=kw.get("external_tokens") or {}),
        # Real Starlette Request.headers is case-insensitive; mimic that
        # so the decorator's lookup of ``X-Khub-Probe-Id`` works.
        headers=headers,
    )


def _decode_set_cookie(set_cookie_value: str) -> tuple[str, str, dict]:
    """Tiny RFC-6265-ish parser for the test (name, value, attrs)."""
    parts = [p.strip() for p in set_cookie_value.split(";") if p.strip()]
    head = parts[0]
    name, value = head.split("=", 1)
    attrs = {}
    for chunk in parts[1:]:
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            attrs[k.strip().lower()] = v.strip()
        else:
            attrs[chunk.strip().lower()] = ""
    return name, value, attrs


@pytest.mark.asyncio
async def test_trace_cookie_value_is_NOT_double_quoted(monkeypatch):
    """Regression guard: Python's ``http.cookies.SimpleCookie`` wraps
    values containing ``=`` in double quotes, but RFC 6265 allows
    ``=`` unquoted in cookie-octets and quoted values trip up the
    SPA's ``atob`` call (``"`` isn't valid base64). The backend must
    bypass SimpleCookie and write the raw Set-Cookie line so the
    base64 trace value reaches the browser unquoted.
    """

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        return Response(status_code=200, content=b'{"ok":true}')

    request = _request_with_probe_id("/api/models/owner/demo", "uuid-123")
    result = await handler(
        repo_type="model", namespace="owner", name="demo", request=request,
    )
    set_cookie = result.headers.get("set-cookie") or result.headers.get(
        "Set-Cookie"
    )
    assert set_cookie
    name, value, _attrs = _decode_set_cookie(set_cookie)
    # The value must be the raw base64 trace, NOT wrapped in quotes.
    # ``atob`` in the browser would reject ``"`` as non-base64 and
    # the SPA would fall through to CHAIN_EXHAUSTED.
    assert not value.startswith('"'), f"cookie value is quoted: {value!r}"
    assert not value.endswith('"'), f"cookie value is quoted: {value!r}"
    # And it should be exactly the same string as the X-Chain-Trace header.
    assert value == result.headers.get("x-chain-trace") or value == result.headers.get(
        "X-Chain-Trace"
    )


@pytest.mark.asyncio
async def test_trace_cookie_set_when_probe_id_header_present(monkeypatch):
    """Probe id supplied → response carries Set-Cookie alongside the
    X-Chain-Trace header. Cookie name = ``_khub_chain_trace_<id>``,
    cookie value = same encoded trace as the header."""

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        return Response(status_code=200, content=b'{"ok":true}')

    request = _request_with_probe_id("/api/models/owner/demo", "abc123uuid")
    result = await handler(
        repo_type="model", namespace="owner", name="demo", request=request,
    )
    # X-Chain-Trace still set on the universal channel.
    trace_header = result.headers.get("x-chain-trace") or result.headers.get(
        "X-Chain-Trace"
    )
    assert trace_header
    # And the cookie is set.
    set_cookie = result.headers.get("set-cookie") or result.headers.get(
        "Set-Cookie"
    )
    assert set_cookie, "Set-Cookie must be present when probe id supplied"
    name, value, attrs = _decode_set_cookie(set_cookie)
    assert name == "_khub_chain_trace_abc123uuid"
    assert value == trace_header  # cookie value == header value
    # Max-Age = 300s (5 minutes) per the user's requirement.
    assert attrs.get("max-age") == "300"
    assert attrs.get("path") == "/"
    assert attrs.get("samesite", "").lower() == "lax"


@pytest.mark.asyncio
async def test_trace_NOT_emitted_when_probe_id_header_absent(monkeypatch):
    """Anonymous caller (no ``X-Khub-Probe-Id``) → neither
    ``X-Chain-Trace`` header nor ``Set-Cookie``. Encoded hops contain
    fallback ``source_url`` + ``source_name`` which is operator-
    internal topology data — emitting it on every fallback-decorated
    response (the early draft of #78) was an info-disclosure
    regression caught in PR review. Trace emission is now
    chain-tester opt-in: probe id ⇒ both channels active, no probe
    id ⇒ neither (the in-process ContextVar is still populated for
    server-side logging, just not put on the wire)."""

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        return Response(status_code=200, content=b'{"ok":true}')

    request = _request_with_probe_id("/api/models/owner/demo", None)
    result = await handler(
        repo_type="model", namespace="owner", name="demo", request=request,
    )
    assert result.headers.get("x-chain-trace") is None
    assert result.headers.get("X-Chain-Trace") is None
    assert (
        result.headers.get("set-cookie") is None
        and result.headers.get("Set-Cookie") is None
    )


@pytest.mark.asyncio
async def test_trace_cookie_concurrent_probes_get_distinct_names(monkeypatch):
    """Two probes with different ids must get different cookie names so
    the SPA can read its own trace without colliding with an in-flight
    probe from another tab (or another logical run within the same
    tab)."""

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        return Response(status_code=200, content=b'{}')

    r1 = await handler(
        repo_type="model", namespace="owner", name="demo",
        request=_request_with_probe_id("/api/models/owner/demo", "id-AAAA"),
    )
    r2 = await handler(
        repo_type="model", namespace="owner", name="demo",
        request=_request_with_probe_id("/api/models/owner/demo", "id-BBBB"),
    )
    n1, _, _ = _decode_set_cookie(r1.headers["set-cookie"])
    n2, _, _ = _decode_set_cookie(r2.headers["set-cookie"])
    assert n1 == "_khub_chain_trace_id-AAAA"
    assert n2 == "_khub_chain_trace_id-BBBB"
    assert n1 != n2


@pytest.mark.asyncio
async def test_trace_rejects_unsafe_probe_id(monkeypatch):
    """A malicious probe id with cookie-name-illegal characters must
    NOT produce *any* trace output: ``sanitize_probe_id`` returns
    None, the request is treated as anonymous, neither X-Chain-Trace
    nor Set-Cookie is emitted. (Pre-fix, X-Chain-Trace was still
    emitted on the assumption that "trace header is universal" — but
    that exposed source URLs to anonymous callers, which is the
    info-disclosure regression we now block.)"""

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        return Response(status_code=200, content=b'{}')

    bad_ids = [
        "abc;def=evil",     # ; injection
        "abc def",          # whitespace
        "abc,def",          # comma
        "abc\nSet-Cookie:x=y",  # newline injection
        "x" * 65,           # too long
        "",                 # empty
    ]
    for bad in bad_ids:
        result = await handler(
            repo_type="model", namespace="owner", name="demo",
            request=_request_with_probe_id("/api/models/owner/demo", bad),
        )
        assert result.headers.get("x-chain-trace") is None, (
            f"unsafe probe id {bad!r} must not produce X-Chain-Trace"
        )
        assert (
            result.headers.get("set-cookie") is None
            and result.headers.get("Set-Cookie") is None
        ), f"unsafe probe id {bad!r} must not produce a Set-Cookie"


@pytest.mark.asyncio
async def test_trace_cookie_set_on_dict_return_jsonresponse_wrap(monkeypatch):
    """LOCAL_HIT dict path goes through the JSONResponse wrap branch of
    ``_attach_trace_to_result``. Cookie injection must work there too."""

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        return {"id": "owner/demo", "private": False}

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        request=_request_with_probe_id("/api/models/owner/demo", "wrapped-id"),
    )
    assert isinstance(result, JSONResponse)
    set_cookie = result.headers.get("set-cookie") or result.headers.get(
        "Set-Cookie"
    )
    assert set_cookie
    name, value, _attrs = _decode_set_cookie(set_cookie)
    assert name == "_khub_chain_trace_wrapped-id"
    assert value == result.headers["x-chain-trace"]


@pytest.mark.asyncio
async def test_trace_cookie_set_on_httpexception_path(monkeypatch):
    """Non-404 HTTPException re-raise: ``inject_trace_into_exception_headers``
    threads the probe id into the headers dict so the cookie rides on
    the error response too."""

    async def fail(*_a, **_k):
        raise AssertionError("fallback must NOT run on non-404")

    monkeypatch.setattr(fallback_decorators, "try_fallback_info", fail)

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        raise HTTPException(status_code=500, detail="boom")

    with pytest.raises(HTTPException) as exc:
        await handler(
            repo_type="model", namespace="owner", name="demo",
            request=_request_with_probe_id("/api/models/owner/demo", "exc-probe"),
        )
    headers = exc.value.headers or {}
    assert headers.get("X-Chain-Trace") or headers.get("x-chain-trace")
    set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie")
    assert set_cookie
    name, _, attrs = _decode_set_cookie(set_cookie)
    assert name == "_khub_chain_trace_exc-probe"
    assert attrs.get("max-age") == "300"


@pytest.mark.asyncio
async def test_trace_cookie_never_set_without_hops(monkeypatch):
    """When fallback is disabled (``cfg.fallback.enabled = False``) the
    decorator short-circuits before ``start_trace``. No hops → no
    cookie even if probe id supplied."""

    monkeypatch.setattr(fallback_decorators.cfg.fallback, "enabled", False)

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        return Response(status_code=200, content=b'{}')

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        request=_request_with_probe_id("/api/models/owner/demo", "ignored-id"),
    )
    assert result.headers.get("x-chain-trace") is None
    assert result.headers.get("set-cookie") is None


# ===========================================================================
# X-Chain-Trace emission contract (#78 redesign)
#
# The chain tester sends a real production request from the browser and
# reads the chain off ``X-Chain-Trace``. The decorator is the single
# place that bootstraps the trace and records the local hop, so these
# tests pin down the per-decision shapes:
#
# - LOCAL_HIT (Response 2xx)   : success path, Response → header attached
# - LOCAL_HIT (dict return)     : success path, dict → JSONResponse wrap + header
# - LOCAL_FILTERED (404 +Entry) : local repo exists, entry missing — no fallback
# - LOCAL_MISS  (404 +RepoMiss) : fallback ran; both local + fallback hops recorded
# - LOCAL_OTHER_ERROR (5xx)     : non-404 surfaces unchanged with trace attached
# - HTTPException 4xx (non-404) : re-raised with X-Chain-Trace in headers
# ===========================================================================


@pytest.mark.asyncio
async def test_trace_local_hit_response_attaches_header(monkeypatch):
    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        # Plain 2xx Response — the LOCAL_HIT path through the decorator.
        return Response(status_code=200, content=b'{"ok":true}', media_type="application/json")

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        request=_request("/api/models/owner/demo"),
    )
    hops = _decode_trace_header(result)
    assert len(hops) == 1
    h = hops[0]
    assert h["kind"] == "local"
    assert h["source_name"] == "local"
    assert h["decision"] == "LOCAL_HIT"
    assert h["status_code"] == 200
    assert isinstance(h["duration_ms"], int)


@pytest.mark.asyncio
async def test_trace_local_hit_dict_return_is_wrapped(monkeypatch):
    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        return {"id": "owner/demo", "private": False}

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        request=_request("/api/models/owner/demo"),
    )
    # Dict was wrapped in JSONResponse so we can attach the trace header
    # — but the body content is byte-identical to what FastAPI's auto
    # conversion would emit (both go through ``jsonable_encoder``).
    assert isinstance(result, JSONResponse)
    assert _decode_body(result) == {"id": "owner/demo", "private": False}
    hops = _decode_trace_header(result)
    assert [h["decision"] for h in hops] == ["LOCAL_HIT"]


@pytest.mark.asyncio
async def test_trace_local_filtered_records_entry_not_found(monkeypatch):
    """Local 404 + EntryNotFound → LOCAL_FILTERED hop, no fallback hop."""

    async def fail(*_a, **_k):
        raise AssertionError("fallback chain must NOT run on EntryNotFound")

    monkeypatch.setattr(fallback_decorators, "try_fallback_resolve", fail)

    @fallback_decorators.with_repo_fallback("resolve")
    async def handler(
        repo_type, namespace, name, revision, path,
        request=None, fallback: bool = True, user=None,
    ):
        return Response(
            status_code=404,
            headers={"X-Error-Code": "EntryNotFound", "X-Error-Message": "missing entry"},
        )

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        revision="main", path="foo",
        request=_request("/owner/demo/resolve/main/foo"),
    )
    hops = _decode_trace_header(result)
    assert len(hops) == 1
    assert hops[0]["decision"] == "LOCAL_FILTERED"
    assert hops[0]["x_error_code"] == "EntryNotFound"
    # Same Response object reference preserved (header injection mutates in place).
    assert result.status_code == 404
    assert result.headers["X-Error-Code"] == "EntryNotFound"


@pytest.mark.asyncio
async def test_trace_local_miss_then_fallback_emits_both_hops(monkeypatch):
    """Local 404 RepoNotFound → fallback runs. We expect a LOCAL_MISS hop
    captured by the decorator and at least one fallback hop captured by
    the operations layer when the chain probes a source."""
    # We reach into the decorator's ``start_trace.__globals__`` to grab
    # the *exact* trace-module instance the OLD ``fallback_decorators``
    # (the one bound at this test file's import) is wired against. After
    # a previous test triggered the conftest's ``clear_backend_modules``
    # reload, ``sys.modules`` will hold a *new* trace module whose
    # ``_chain_trace`` ContextVar is a different object from the old one
    # the OLD decorator uses. Picking the function dictionary off the
    # OLD ``start_trace`` keeps the read + write paths on the same
    # ContextVar instance, otherwise ``record_source_hop`` would no-op
    # against an unset variable.
    trace_module_globals = fallback_decorators.start_trace.__globals__
    record_source_hop_live = trace_module_globals["record_source_hop"]

    async def fake_resolve(*args, **kwargs):
        record_source_hop_live(
            {"name": "HF", "url": "https://hf.example", "source_type": "huggingface"},
            method="HEAD",
            upstream_path="/models/owner/demo/resolve/main/foo",
            response=None,
            decision=None,
            duration_ms=42,
            transport_decision="BIND_AND_RESPOND",
            error=None,
        )
        return Response(status_code=200, content=b"hf-bytes")

    monkeypatch.setattr(fallback_decorators, "try_fallback_resolve", fake_resolve)
    monkeypatch.setattr(fallback_decorators, "get_merged_external_tokens", lambda u, h: {})

    @fallback_decorators.with_repo_fallback("resolve")
    async def handler(
        repo_type, namespace, name, revision, path,
        request=None, fallback: bool = True, user=None,
    ):
        return Response(
            status_code=404,
            headers={"X-Error-Code": "RepoNotFound"},
        )

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        revision="main", path="foo",
        request=_request("/owner/demo/resolve/main/foo"),
    )
    hops = _decode_trace_header(result)
    decisions = [h["decision"] for h in hops]
    assert "LOCAL_MISS" in decisions
    # The fallback hop sits after the local hop and inherits the
    # transport_decision we passed.
    assert hops[0]["kind"] == "local"
    assert any(h["kind"] == "fallback" for h in hops)


@pytest.mark.asyncio
async def test_trace_local_other_error_attaches_header_and_surfaces_response(monkeypatch):
    """A 5xx Response from local should NOT trigger fallback and must
    still carry X-Chain-Trace (decision=LOCAL_OTHER_ERROR)."""

    async def fail(*_a, **_k):
        raise AssertionError("fallback must NOT run on non-404")

    monkeypatch.setattr(fallback_decorators, "try_fallback_info", fail)

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        return Response(status_code=503, content=b"backend down")

    result = await handler(
        repo_type="model", namespace="owner", name="demo",
        request=_request("/api/models/owner/demo"),
    )
    assert result.status_code == 503
    hops = _decode_trace_header(result)
    assert [h["decision"] for h in hops] == ["LOCAL_OTHER_ERROR"]


@pytest.mark.asyncio
async def test_trace_local_other_error_httpexception_carries_header(monkeypatch):
    """Same as above but for HTTPException(non-404) — re-raised with
    X-Chain-Trace in the exception headers so the chain tester can read
    it from the error response too."""

    async def fail(*_a, **_k):
        raise AssertionError("fallback must NOT run on non-404 HTTPException")

    monkeypatch.setattr(fallback_decorators, "try_fallback_info", fail)

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        raise HTTPException(status_code=500, detail="boom")

    with pytest.raises(HTTPException) as exc:
        await handler(
            repo_type="model", namespace="owner", name="demo",
            request=_request("/api/models/owner/demo"),
        )
    assert exc.value.status_code == 500
    headers = exc.value.headers or {}
    trace_header = headers.get("X-Chain-Trace") or headers.get("x-chain-trace")
    assert trace_header, "X-Chain-Trace must ride on the HTTPException headers"
    decoded = base64.b64decode(trace_header).decode("utf-8")
    hops = json.loads(decoded)["hops"]
    assert hops[0]["decision"] == "LOCAL_OTHER_ERROR"
    assert hops[0]["status_code"] == 500


@pytest.mark.asyncio
async def test_trace_local_handler_raises_generic_exception_emits_trace(monkeypatch):
    """Production path #2: LakeFS / DB / generic exception from the
    inner handler must NOT propagate before the trace is recorded.
    Pre-fix the decorator only caught HTTPException, so any other
    raised type (httpx.ReadTimeout from LakeFS, peewee
    OperationalError, etc.) skipped record_local_hop entirely and
    surfaced as an unannotated 500 with no X-Chain-Trace.

    Now: catch generic Exception, record LOCAL_OTHER_ERROR, re-raise
    as 500 with the trace gated on probe_id presence (same auth
    contract as every other emission path).
    """

    async def fail(*_a, **_k):
        raise AssertionError("fallback must NOT run on generic exception")

    monkeypatch.setattr(fallback_decorators, "try_fallback_info", fail)

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        raise httpx.ReadTimeout("synthetic LakeFS timeout")

    with pytest.raises(HTTPException) as exc:
        await handler(
            repo_type="model", namespace="owner", name="demo",
            request=_request("/api/models/owner/demo"),
        )
    assert exc.value.status_code == 500
    headers = exc.value.headers or {}
    trace_header = headers.get("X-Chain-Trace") or headers.get("x-chain-trace")
    assert trace_header, (
        "X-Chain-Trace must ride on the 500 response so the chain "
        "tester can still see what went wrong"
    )
    decoded = base64.b64decode(trace_header).decode("utf-8")
    hops = json.loads(decoded)["hops"]
    assert hops[0]["decision"] == "LOCAL_OTHER_ERROR"
    assert hops[0]["status_code"] == 500
    assert "ReadTimeout" in (hops[0]["x_error_message"] or "")


@pytest.mark.asyncio
async def test_trace_local_handler_raises_no_trace_without_probe_id(monkeypatch):
    """Anonymous caller hitting a generic exception still gets a 500
    but with NO X-Chain-Trace (auth gate stays in place even on the
    error path)."""

    async def fail(*_a, **_k):
        raise AssertionError("fallback must NOT run on generic exception")

    monkeypatch.setattr(fallback_decorators, "try_fallback_info", fail)

    @fallback_decorators.with_repo_fallback("info")
    async def handler(repo_type, namespace, name, request=None, fallback: bool = True, user=None):
        raise RuntimeError("boom")

    with pytest.raises(HTTPException) as exc:
        await handler(
            repo_type="model", namespace="owner", name="demo",
            request=_request("/api/models/owner/demo", probe_id=None),
        )
    assert exc.value.status_code == 500
    headers = exc.value.headers or {}
    assert headers.get("X-Chain-Trace") is None
    assert headers.get("x-chain-trace") is None
    assert headers.get("Set-Cookie") is None


@pytest.mark.asyncio
async def test_trace_fallback_miss_carries_local_and_attaches_to_404(monkeypatch):
    """Local 404 RepoNotFound + fallback returns None ⇒ original local
    404 is surfaced, with X-Chain-Trace recording the LOCAL_MISS hop."""

    async def fake_tree(*args, **kwargs):
        return None  # chain exhausted, nothing bound

    monkeypatch.setattr(fallback_decorators, "try_fallback_tree", fake_tree)
    monkeypatch.setattr(fallback_decorators, "get_merged_external_tokens", lambda u, h: {})

    original = JSONResponse(
        status_code=404,
        content={"detail": "missing"},
        headers={"X-Error-Code": "RepoNotFound"},
    )

    @fallback_decorators.with_repo_fallback("tree")
    async def handler(namespace, name, revision, path="", request=None, user=None):
        return original

    result = await handler(
        namespace="owner", name="demo", revision="main",
        request=_request("/api/models/owner/demo/tree/main"),
    )
    # Decorator surfaces the original 404 Response object (same id);
    # header injection mutates in place so X-Chain-Trace is attached.
    assert result is original
    hops = _decode_trace_header(result)
    assert hops[0]["decision"] == "LOCAL_MISS"
