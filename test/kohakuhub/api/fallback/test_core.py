"""Tests for ``src/kohakuhub/api/fallback/core.py`` (#78).

Drives the pure ``probe_chain`` function with a mocked client factory
so behaviour can be verified deterministically per source. Each test
pins one classifier branch of the underlying ``utils.classify_upstream``
priority order, so a future change in classifier semantics will surface
both here and in the production-path tests in ``test_operations.py``.
"""
from __future__ import annotations

import json

import httpx
import pytest

from kohakuhub.api.fallback import core


def _resp(
    status: int,
    *,
    headers: dict[str, str] | None = None,
    url: str = "http://upstream/x",
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=b"",
        headers=headers or {},
        request=httpx.Request("GET", url),
    )


class _FakeClient:
    """Per-source response queue keyed by (url, method)."""

    registry: dict[tuple[str, str], list[object]] = {}
    calls: list[tuple[str, str, str]] = []  # (url, method, path)

    def __init__(self, source_url: str, source_type: str, token=None):
        self.source_url = source_url
        self.source_type = source_type
        self.token = token

    @classmethod
    def reset(cls):
        cls.registry = {}
        cls.calls = []

    @classmethod
    def queue(cls, url: str, method: str, *results: object) -> None:
        cls.registry.setdefault((url, method), []).extend(results)

    async def _dispatch(self, method: str, path: str, **_kwargs) -> httpx.Response:
        type(self).calls.append((self.source_url, method, path))
        queue = type(self).registry.get((self.source_url, method))
        if not queue:
            raise AssertionError(
                f"_FakeClient: no scripted response for {self.source_url} {method} {path}"
            )
        result = queue.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def head(self, path, repo_type, **kw):
        return await self._dispatch("HEAD", path, **kw)

    async def get(self, path, repo_type, **kw):
        return await self._dispatch("GET", path, **kw)

    async def post(self, path, repo_type, **kw):
        return await self._dispatch("POST", path, **kw)


@pytest.fixture(autouse=True)
def _reset_fake_client():
    _FakeClient.reset()


# ---------------------------------------------------------------------------
# Path construction
# ---------------------------------------------------------------------------


def test_build_kohaku_path_resolve():
    p = core._build_kohaku_path("resolve", "model", "owner", "demo", "main", "config.json")
    assert p == "/models/owner/demo/resolve/main/config.json"


def test_build_kohaku_path_tree_with_subpath():
    p = core._build_kohaku_path("tree", "dataset", "owner", "demo", "main", "/docs")
    assert p == "/api/datasets/owner/demo/tree/main/docs"


def test_build_kohaku_path_tree_root():
    # No subpath — must not produce a trailing slash.
    p = core._build_kohaku_path("tree", "model", "owner", "demo", "main", "")
    assert p == "/api/models/owner/demo/tree/main"


def test_build_kohaku_path_paths_info():
    p = core._build_kohaku_path("paths_info", "model", "owner", "demo", "main", "")
    assert p == "/api/models/owner/demo/paths-info/main"


def test_build_kohaku_path_info():
    p = core._build_kohaku_path("info", "space", "owner", "demo", "main", "")
    assert p == "/api/spaces/owner/demo"


# ---------------------------------------------------------------------------
# probe_chain — happy paths
# ---------------------------------------------------------------------------


SRC_A = {"name": "A", "url": "https://a.local", "source_type": "huggingface"}
SRC_B = {"name": "B", "url": "https://b.local", "source_type": "huggingface"}


@pytest.mark.asyncio
async def test_probe_chain_first_source_binds_and_responds():
    """200 at the first source → BIND_AND_RESPOND, chain stops."""
    _FakeClient.queue(SRC_A["url"], "GET", _resp(200))
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A, SRC_B], client_factory=_FakeClient,
    )
    assert report.final_outcome == "BIND_AND_RESPOND"
    assert report.bound_source == SRC_A
    assert len(report.attempts) == 1
    assert report.attempts[0].decision == "BIND_AND_RESPOND"
    assert report.attempts[0].source_name == "A"
    # Other sources untouched.
    assert all(c[0] != SRC_B["url"] for c in _FakeClient.calls)


@pytest.mark.asyncio
async def test_probe_chain_propagates_entry_not_found():
    """X-Error-Code: EntryNotFound → BIND_AND_PROPAGATE, stops chain."""
    _FakeClient.queue(
        SRC_A["url"], "GET",
        _resp(404, headers={"x-error-code": "EntryNotFound"}),
    )
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A, SRC_B], client_factory=_FakeClient,
    )
    assert report.final_outcome == "BIND_AND_PROPAGATE"
    assert report.attempts[0].decision == "BIND_AND_PROPAGATE"
    assert report.attempts[0].x_error_code == "EntryNotFound"
    # B never asked.
    assert not any(c[0] == SRC_B["url"] for c in _FakeClient.calls)


@pytest.mark.asyncio
async def test_probe_chain_advances_on_try_next_source():
    """A returns 503, B returns 200 — chain walks A → B and binds B."""
    _FakeClient.queue(SRC_A["url"], "GET", _resp(503))
    _FakeClient.queue(SRC_B["url"], "GET", _resp(200))
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A, SRC_B], client_factory=_FakeClient,
    )
    assert report.final_outcome == "BIND_AND_RESPOND"
    assert report.bound_source == SRC_B
    assert [a.decision for a in report.attempts] == [
        "TRY_NEXT_SOURCE", "BIND_AND_RESPOND",
    ]


@pytest.mark.asyncio
async def test_probe_chain_exhaustion():
    """All sources TRY_NEXT_SOURCE → final_outcome=CHAIN_EXHAUSTED, no bind."""
    _FakeClient.queue(SRC_A["url"], "GET", _resp(503))
    _FakeClient.queue(SRC_B["url"], "GET", _resp(503))
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A, SRC_B], client_factory=_FakeClient,
    )
    assert report.final_outcome == "CHAIN_EXHAUSTED"
    assert report.bound_source is None
    assert len(report.attempts) == 2
    assert all(a.decision == "TRY_NEXT_SOURCE" for a in report.attempts)


# ---------------------------------------------------------------------------
# probe_chain — transport failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_chain_records_timeout_then_continues():
    _FakeClient.queue(SRC_A["url"], "GET", httpx.TimeoutException("read timed out"))
    _FakeClient.queue(SRC_B["url"], "GET", _resp(200))
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A, SRC_B], client_factory=_FakeClient,
    )
    assert report.attempts[0].decision == "TIMEOUT"
    assert "read timed out" in report.attempts[0].error
    assert report.attempts[0].status_code is None
    assert report.final_outcome == "BIND_AND_RESPOND"
    assert report.bound_source == SRC_B


@pytest.mark.asyncio
async def test_probe_chain_records_network_error_then_continues():
    _FakeClient.queue(SRC_A["url"], "GET", RuntimeError("dns failure"))
    _FakeClient.queue(SRC_B["url"], "GET", _resp(200))
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A, SRC_B], client_factory=_FakeClient,
    )
    assert report.attempts[0].decision == "NETWORK_ERROR"
    assert "dns failure" in report.attempts[0].error
    assert report.attempts[1].decision == "BIND_AND_RESPOND"


# ---------------------------------------------------------------------------
# Per-op HTTP method dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_chain_resolve_uses_HEAD():
    _FakeClient.queue(SRC_A["url"], "HEAD", _resp(200))
    await core.probe_chain(
        op="resolve", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A], file_path="config.json",
        client_factory=_FakeClient,
    )
    assert _FakeClient.calls == [(SRC_A["url"], "HEAD", "/models/owner/demo/resolve/main/config.json")]


@pytest.mark.asyncio
async def test_probe_chain_paths_info_uses_POST():
    _FakeClient.queue(SRC_A["url"], "POST", _resp(200))
    await core.probe_chain(
        op="paths_info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A], paths=["a", "b"],
        client_factory=_FakeClient,
    )
    assert _FakeClient.calls == [(SRC_A["url"], "POST", "/api/models/owner/demo/paths-info/main")]


@pytest.mark.asyncio
async def test_probe_chain_tree_uses_GET():
    _FakeClient.queue(SRC_A["url"], "GET", _resp(200))
    await core.probe_chain(
        op="tree", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A], file_path="docs",
        client_factory=_FakeClient,
    )
    assert _FakeClient.calls == [(SRC_A["url"], "GET", "/api/models/owner/demo/tree/main/docs")]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_chain_empty_source_list():
    """No sources → CHAIN_EXHAUSTED with zero attempts."""
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[], client_factory=_FakeClient,
    )
    assert report.final_outcome == "CHAIN_EXHAUSTED"
    assert report.attempts == []
    assert report.bound_source is None


@pytest.mark.asyncio
async def test_probe_chain_unsupported_op_raises():
    with pytest.raises(ValueError, match="Unsupported probe op"):
        await core.probe_chain(
            op="totally-bogus", repo_type="model", namespace="owner", name="demo",
            sources=[SRC_A], client_factory=_FakeClient,
        )


@pytest.mark.asyncio
async def test_probe_chain_uses_source_url_as_default_name():
    """Source dict without ``name`` falls back to URL."""
    src = {"url": "https://no-name.local", "source_type": "huggingface"}
    _FakeClient.queue(src["url"], "GET", _resp(200))
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[src], client_factory=_FakeClient,
    )
    assert report.attempts[0].source_name == "https://no-name.local"


@pytest.mark.asyncio
async def test_probe_chain_default_client_factory(monkeypatch):
    """When ``client_factory`` is omitted, ``FallbackClient`` is used."""
    captured = []
    real_fc_module = core

    class _CapturingClient:
        def __init__(self, source_url, source_type, token):
            captured.append((source_url, source_type, token))
            self.source_url = source_url
            self.source_type = source_type
            self.token = token

        async def get(self, path, repo_type, **_):
            return _resp(200)

    monkeypatch.setattr(real_fc_module, "FallbackClient", _CapturingClient)
    await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[{"url": "https://a.local", "source_type": "huggingface", "token": "t"}],
    )
    assert captured == [("https://a.local", "huggingface", "t")]


def test_probe_attempt_to_dict_is_jsonable():
    a = core.ProbeAttempt(
        source_name="A", source_url="u", source_type="huggingface",
        method="GET", upstream_path="/x", status_code=200,
        x_error_code=None, x_error_message=None,
        decision="BIND_AND_RESPOND", duration_ms=10, error=None,
        response_body_preview="hello", response_headers={"content-type": "text/plain"},
    )
    d = a.to_dict()
    assert d["source_name"] == "A"
    assert d["decision"] == "BIND_AND_RESPOND"
    assert d["status_code"] == 200
    assert d["response_body_preview"] == "hello"
    assert d["response_headers"] == {"content-type": "text/plain"}


def test_probe_report_to_dict_is_jsonable():
    a = core.ProbeAttempt(
        source_name="A", source_url="u", source_type="huggingface",
        method="GET", upstream_path="/x", status_code=200,
        x_error_code=None, x_error_message=None,
        decision="BIND_AND_RESPOND", duration_ms=10, error=None,
    )
    r = core.ProbeReport(
        op="info", repo_id="owner/demo", revision=None, file_path=None,
        attempts=[a], final_outcome="BIND_AND_RESPOND",
        bound_source={"url": "u"}, duration_ms=15,
        final_response={"status_code": 200, "headers": {}, "body_preview": ""},
    )
    d = r.to_dict()
    assert d["op"] == "info"
    assert d["attempts"][0]["source_name"] == "A"
    assert d["bound_source"] == {"url": "u"}
    assert d["final_response"]["status_code"] == 200


# ---------------------------------------------------------------------------
# Body preview + curated headers (#78 enrichment)
# ---------------------------------------------------------------------------


def test_preview_body_handles_none_response():
    assert core._preview_body(None) is None


def test_preview_body_returns_empty_for_empty_body():
    r = httpx.Response(200, content=b"", request=httpx.Request("GET", "http://x"))
    assert core._preview_body(r) == ""


def test_preview_body_decodes_utf8_under_limit():
    r = httpx.Response(200, content=b'{"id": "demo"}', request=httpx.Request("GET", "http://x"))
    assert core._preview_body(r) == '{"id": "demo"}'


def test_preview_body_truncates_with_marker_when_over_limit():
    big = b"x" * (core._BODY_PREVIEW_LIMIT + 100)
    r = httpx.Response(200, content=big, request=httpx.Request("GET", "http://x"))
    preview = core._preview_body(r)
    assert preview.startswith("x" * core._BODY_PREVIEW_LIMIT)
    assert "[truncated, total" in preview


def test_preview_body_handles_binary_with_placeholder():
    # 0xff is invalid UTF-8 start byte → fallback to placeholder.
    r = httpx.Response(200, content=b"\xff\xfe\xff", request=httpx.Request("GET", "http://x"))
    preview = core._preview_body(r)
    assert preview == "[binary, 3 bytes]"


def test_curated_headers_returns_only_relevant_keys():
    r = httpx.Response(
        200,
        content=b"",
        headers={
            "Content-Type": "application/json",
            "X-Cache-Hit": "1",  # not in curated set
            "X-Error-Code": "EntryNotFound",
            "Server": "Apache",
        },
        request=httpx.Request("GET", "http://x"),
    )
    h = core._curated_headers(r)
    assert h == {
        "content-type": "application/json",
        "x-error-code": "EntryNotFound",
    }


def test_curated_headers_handles_none():
    assert core._curated_headers(None) == {}


@pytest.mark.asyncio
async def test_probe_chain_captures_body_and_headers_per_attempt():
    """Body and curated headers should ride through to the report."""
    body = b'{"id":"openai-community/gpt2"}'
    _FakeClient.queue(
        SRC_A["url"], "GET",
        httpx.Response(
            200, content=body,
            headers={"Content-Type": "application/json", "ETag": "abc"},
            request=httpx.Request("GET", "http://upstream"),
        ),
    )
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A], client_factory=_FakeClient,
    )
    a = report.attempts[0]
    assert a.response_body_preview == '{"id":"openai-community/gpt2"}'
    assert a.response_headers["content-type"] == "application/json"
    assert a.response_headers["etag"] == "abc"


@pytest.mark.asyncio
async def test_probe_chain_final_response_mirrors_bound_attempt():
    body = b'{"id":"openai/gpt"}'
    _FakeClient.queue(
        SRC_A["url"], "GET",
        httpx.Response(
            200, content=body,
            headers={"Content-Type": "application/json", "ETag": "v1"},
            request=httpx.Request("GET", "http://upstream"),
        ),
    )
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A], client_factory=_FakeClient,
    )
    assert report.final_response is not None
    assert report.final_response["status_code"] == 200
    assert report.final_response["body_preview"] == '{"id":"openai/gpt"}'
    assert report.final_response["headers"]["content-type"] == "application/json"


@pytest.mark.asyncio
async def test_probe_chain_chain_exhausted_has_no_final_response():
    """``probe_chain`` (low-level) leaves ``final_response=None`` on
    exhaust — it doesn't have the op + local-hop context needed to
    reconstruct the right aggregate. Its caller
    (``probe_full_chain`` / the simulate endpoint) is responsible for
    filling it in production-faithfully — see
    ``test_probe_full_chain_exhaust_with_sources_uses_aggregate``."""
    _FakeClient.queue(SRC_A["url"], "GET", _resp(503))
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A], client_factory=_FakeClient,
    )
    assert report.final_outcome == "CHAIN_EXHAUSTED"
    assert report.final_response is None


@pytest.mark.asyncio
async def test_probe_chain_propagate_response_lands_in_final_response():
    """BIND_AND_PROPAGATE (e.g. 404 + EntryNotFound) is still a binding —
    the upstream's 4xx body should appear in final_response so the UI
    can show what the bound source said."""
    _FakeClient.queue(
        SRC_A["url"], "GET",
        httpx.Response(
            404,
            content=b'{"error": "no such file"}',
            headers={"X-Error-Code": "EntryNotFound", "Content-Type": "application/json"},
            request=httpx.Request("GET", "http://upstream"),
        ),
    )
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="owner", name="demo",
        sources=[SRC_A], client_factory=_FakeClient,
    )
    assert report.final_outcome == "BIND_AND_PROPAGATE"
    assert report.final_response["status_code"] == 404
    assert report.final_response["headers"]["x-error-code"] == "EntryNotFound"
    assert report.final_response["body_preview"] == '{"error": "no such file"}'


# ===========================================================================
# Resolve fidelity (#78 v3): for op=resolve + BIND_AND_RESPOND the
# probe must replay ``apply_resolve_head_postprocess`` so the timeline
# shows the headers an hf_hub client would actually receive (Location
# rewritten absolute, Content-Length / ETag / X-Repo-Commit backfilled
# from the non-LFS follow-HEAD), not HF's raw 307.
# ===========================================================================


SRC_HF = {
    "name": "HF",
    "url": "https://huggingface.co",
    "source_type": "huggingface",
}


@pytest.mark.asyncio
async def test_probe_chain_resolve_postprocess_rewrites_location_for_lfs():
    """LFS path (``X-Linked-Size`` present) — probe must rewrite the
    relative ``Location`` to absolute via ``apply_resolve_head_postprocess``
    and skip the follow-HEAD branch."""
    _FakeClient.queue(
        SRC_HF["url"],
        "HEAD",
        _resp(
            307,
            headers={
                "location": "https://cdn-lfs.example/path",
                "x-linked-size": "12345",
                "etag": '"abc"',
            },
            url="https://huggingface.co/models/x/y/resolve/main/big.bin",
        ),
    )
    report = await core.probe_chain(
        op="resolve", repo_type="model", namespace="x", name="y",
        revision="main", file_path="big.bin",
        sources=[SRC_HF], client_factory=_FakeClient,
    )
    assert report.final_outcome == "BIND_AND_RESPOND"
    headers = report.final_response["headers"]
    # X-Source* added (postprocess ran).
    assert headers.get("x-source") == "HF"
    assert headers.get("x-source-url") == "https://huggingface.co"
    # Location preserved (already absolute in this case).
    assert headers.get("location") == "https://cdn-lfs.example/path"


@pytest.mark.asyncio
async def test_probe_chain_resolve_postprocess_runs_follow_head_for_non_lfs(
    monkeypatch,
):
    """Non-LFS 307 (no ``X-Linked-Size``) — probe must run the
    follow-HEAD against the rewritten absolute Location to backfill
    real ``content-length`` / ``etag`` / ``x-repo-commit`` so simulate
    is byte-identical to what an hf_hub client gets in production."""
    captured = []

    class _MockAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def head(self, url, *, headers=None, follow_redirects=False):
            captured.append(url)
            return httpx.Response(
                200,
                headers={
                    "content-length": "987654",
                    "etag": '"real-etag"',
                    "x-repo-commit": "deadbeef",
                },
            )

    monkeypatch.setattr(
        "kohakuhub.api.fallback.utils.httpx.AsyncClient", _MockAsyncClient
    )

    _FakeClient.queue(
        SRC_HF["url"],
        "HEAD",
        _resp(
            307,
            headers={
                "location": "/api/resolve-cache/models/x/y/sha/config.json",
                "content-length": "278",  # bogus redirect-body length
                "etag": '"redirect-etag"',
            },
            url="https://huggingface.co/models/x/y/resolve/main/config.json",
        ),
    )
    report = await core.probe_chain(
        op="resolve", repo_type="model", namespace="x", name="y",
        revision="main", file_path="config.json",
        sources=[SRC_HF], client_factory=_FakeClient,
    )
    # Follow-HEAD ran against the rewritten absolute Location.
    assert captured == [
        "https://huggingface.co/api/resolve-cache/models/x/y/sha/config.json"
    ]
    headers = report.final_response["headers"]
    # Real values from follow-HEAD.
    assert headers.get("content-length") == "987654"
    assert headers.get("etag") == '"real-etag"'
    assert headers.get("x-repo-commit") == "deadbeef"
    # Location is now absolute (matches what hf_hub would actually follow).
    assert (
        headers.get("location")
        == "https://huggingface.co/api/resolve-cache/models/x/y/sha/config.json"
    )


@pytest.mark.asyncio
async def test_probe_chain_resolve_postprocess_skipped_on_propagate(monkeypatch):
    """``BIND_AND_PROPAGATE`` (4xx EntryNotFound / RevisionNotFound) on
    resolve — postprocess is intentionally skipped (no Location to
    rewrite, propagate path doesn't run follow-HEAD). The simulate
    just shows the curated raw upstream headers."""
    follow_called = []

    class _SpyClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def head(self, *a, **k):  # pragma: no cover — must NOT run
            follow_called.append(1)
            raise AssertionError("postprocess must not run on PROPAGATE")

    monkeypatch.setattr(
        "kohakuhub.api.fallback.utils.httpx.AsyncClient", _SpyClient
    )

    _FakeClient.queue(
        SRC_HF["url"],
        "HEAD",
        _resp(
            404,
            headers={
                "x-error-code": "EntryNotFound",
                "x-error-message": "no such file",
            },
            url="https://huggingface.co/models/x/y/resolve/main/missing.bin",
        ),
    )
    report = await core.probe_chain(
        op="resolve", repo_type="model", namespace="x", name="y",
        revision="main", file_path="missing.bin",
        sources=[SRC_HF], client_factory=_FakeClient,
    )
    assert report.final_outcome == "BIND_AND_PROPAGATE"
    assert follow_called == []  # postprocess + follow-HEAD NOT triggered


@pytest.mark.asyncio
async def test_probe_chain_resolve_postprocess_failure_falls_through(monkeypatch):
    """If ``apply_resolve_head_postprocess`` itself raises (very rare —
    follow-HEAD network error is already swallowed inside the helper),
    ``_probe_one_source`` must fall through to the curated raw upstream
    headers rather than crash the simulate."""

    async def _boom_postprocess(*_a, **_k):
        raise RuntimeError("synthetic postprocess explosion")

    # Patch the OLD core module's globals (the one this test file
    # bound at import time) rather than ``sys.modules[...]``, since
    # the conftest's ``clear_backend_modules`` may have reloaded the
    # latter — see ``test_decorators.py`` comment for full explanation.
    monkeypatch.setitem(
        core.probe_chain.__globals__,
        "apply_resolve_head_postprocess",
        _boom_postprocess,
    )

    _FakeClient.queue(
        SRC_HF["url"],
        "HEAD",
        _resp(
            307,
            headers={
                "location": "/api/resolve-cache/x/y/sha/config.json",
                "etag": '"raw-etag"',
                "x-linked-size": "999",
            },
            url="https://huggingface.co/models/x/y/resolve/main/config.json",
        ),
    )
    report = await core.probe_chain(
        op="resolve", repo_type="model", namespace="x", name="y",
        revision="main", file_path="config.json",
        sources=[SRC_HF], client_factory=_FakeClient,
    )
    assert report.final_outcome == "BIND_AND_RESPOND"
    headers = report.final_response["headers"]
    # Raw upstream headers preserved (etag, location relative).
    assert headers.get("etag") == '"raw-etag"'
    assert headers.get("location") == "/api/resolve-cache/x/y/sha/config.json"


@pytest.mark.asyncio
async def test_probe_chain_info_postprocess_NOT_applied(monkeypatch):
    """The postprocess only runs for ``op=resolve``. info / tree /
    paths_info don't redirect, so the existing curated_headers path
    is correct and we must skip postprocess to avoid pointless extra
    work + matching production semantics."""

    class _SpyClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def head(self, *a, **k):  # pragma: no cover — must NOT run
            raise AssertionError("info op should not trigger postprocess HEAD")

    monkeypatch.setattr(
        "kohakuhub.api.fallback.utils.httpx.AsyncClient", _SpyClient
    )

    _FakeClient.queue(
        SRC_HF["url"],
        "GET",
        _resp(
            200,
            headers={"content-type": "application/json"},
            url="https://huggingface.co/api/models/x/y",
        ),
    )
    report = await core.probe_chain(
        op="info", repo_type="model", namespace="x", name="y",
        sources=[SRC_HF], client_factory=_FakeClient,
    )
    assert report.final_outcome == "BIND_AND_RESPOND"
    headers = report.final_response["headers"]
    # No X-Source injection (postprocess didn't run for info).
    assert "x-source" not in headers


# ---------------------------------------------------------------------------
# _build_chain_exhausted_aggregate — production-faithful reconstruction
#
# Production's ``with_repo_fallback`` returns ``build_aggregate_failure_response``
# JSON to the client when the chain exhausts (not the local 404 — the
# aggregate body wraps the per-source attempt list). The simulate
# endpoint must mirror that *exactly* in its ``final_response`` so the
# operator-visible "what client gets" panel matches a real hf_hub call.
# Unit-test the reconstruction directly so the production-parity claim
# is regression-pinned.
# ---------------------------------------------------------------------------


def _attempt(
    *, source_name="hf", source_url="https://huggingface.co",
    status_code=401, x_error_code=None, x_error_message=None,
    decision="TRY_NEXT_SOURCE", body_preview="",
    response_headers=None, error=None,
):
    return core.ProbeAttempt(
        source_name=source_name, source_url=source_url,
        source_type="huggingface", method="HEAD",
        upstream_path="/models/x/y/resolve/main/",
        status_code=status_code, x_error_code=x_error_code,
        x_error_message=x_error_message, decision=decision,
        duration_ms=10, error=error,
        response_body_preview=body_preview,
        response_headers=response_headers or {},
    )


def test_build_chain_exhausted_aggregate_bare_401_promotes_to_repo_not_found():
    """Bare 401 (no X-Error-Code) → HF anti-enum signal → aggregate
    promotes to ``RepoNotFound`` even on per-file ops (resolve here),
    matching ``utils.build_aggregate_failure_response``'s repo_miss
    rule. The simulate must surface that exact ``X-Error-Code`` so a
    hf_hub client parsing the simulate panel reaches the same
    ``RepositoryNotFoundError`` it would hitting production."""
    out = core._build_chain_exhausted_aggregate(
        "resolve",
        [_attempt(status_code=401, body_preview="")],
    )
    assert out["status_code"] == 404
    assert out["headers"]["x-error-code"] == "RepoNotFound"
    assert out["headers"]["x-error-message"] == (
        "No fallback source serves this repository."
    )
    body = json.loads(out["body_preview"])
    assert body["error"] == "RepoNotFound"
    assert body["sources"][0]["status"] == 401
    assert body["sources"][0]["category"] == "not-found"
    # Empty HEAD body → message falls through to "HTTP {status}" exactly
    # like production's ``extract_error_message``.
    assert body["sources"][0]["message"] == "HTTP 401"


def test_build_chain_exhausted_aggregate_per_file_scope_keeps_entry_not_found():
    """All-404 with ``EntryNotFound`` codes → aggregate stays at
    ``EntryNotFound`` for per-file ops (no bare-401 escalation to
    ``RepoNotFound``)."""
    out = core._build_chain_exhausted_aggregate(
        "resolve",
        [
            _attempt(
                status_code=404, x_error_code="EntryNotFound",
                body_preview='{"error": "EntryNotFound"}',
            ),
        ],
    )
    assert out["status_code"] == 404
    assert out["headers"]["x-error-code"] == "EntryNotFound"
    body = json.loads(out["body_preview"])
    assert body["error"] == "EntryNotFound"
    assert body["sources"][0]["error_code"] == "EntryNotFound"


def test_build_chain_exhausted_aggregate_repo_scope_uses_repo_not_found():
    """Repo-wide ops (info/tree) classify all-404 as ``RepoNotFound``
    regardless of bare-401 escalation — the scope itself selects the
    right hf_hub exception subclass."""
    out = core._build_chain_exhausted_aggregate(
        "info",
        [_attempt(status_code=404, x_error_code="RepoNotFound")],
    )
    assert out["headers"]["x-error-code"] == "RepoNotFound"
    body = json.loads(out["body_preview"])
    assert body["error"] == "RepoNotFound"


def test_build_chain_exhausted_aggregate_extracts_json_error_field_for_message():
    """Body preview is JSON with ``error`` field → message uses that
    field (matches production's ``extract_error_message``)."""
    out = core._build_chain_exhausted_aggregate(
        "info",
        [_attempt(
            status_code=404, x_error_code="RepoNotFound",
            body_preview='{"error": "Repository not found"}',
        )],
    )
    body = json.loads(out["body_preview"])
    assert body["sources"][0]["message"] == "Repository not found"


def test_build_chain_exhausted_aggregate_handles_truncated_preview():
    """Preview was capped by ``_BODY_PREVIEW_LIMIT`` and carries the
    truncation marker. The marker breaks JSON parsing; the helper
    should strip the marker, fall back to raw text, and not crash."""
    truncated = '{"error": "no' + "\n…[truncated, total 12345 bytes]"
    out = core._build_chain_exhausted_aggregate(
        "info",
        [_attempt(
            status_code=404, x_error_code="RepoNotFound",
            body_preview=truncated,
        )],
    )
    # Stripped marker → still not valid JSON → message is the raw
    # un-truncated head, not a crash.
    body = json.loads(out["body_preview"])
    assert body["sources"][0]["message"] == '{"error": "no'


def test_build_chain_exhausted_aggregate_timeout_attempts_use_timeout_category():
    """Transport-level failures (TIMEOUT / NETWORK_ERROR) keep their
    category through reconstruction — the aggregate's status-priority
    logic then maps the all-timeout case to 502 like production."""
    out = core._build_chain_exhausted_aggregate(
        "info",
        [_attempt(
            status_code=None, decision="TIMEOUT",
            error="read timed out",
        )],
    )
    assert out["status_code"] == 502
    body = json.loads(out["body_preview"])
    assert body["sources"][0]["category"] == "timeout"
    assert body["sources"][0]["status"] is None
    assert "timed out" in body["sources"][0]["message"]


@pytest.mark.asyncio
async def test_probe_full_chain_exhaust_with_no_sources_uses_local_response(
    monkeypatch,
):
    """``probe_full_chain`` with ``sources=[]`` and a LOCAL_MISS goes
    through the "no chain attempts" branch — production's
    ``with_repo_fallback`` returns the original local 404 in that
    case (``try_fallback_*`` returns None when sources is empty), so
    ``final_response`` should mirror the local hop's status/headers/body.
    """

    async def _fake_local(op, repo_type, namespace, name, **kw):
        return core.ProbeAttempt(
            source_name="local", source_url="",
            source_type="local", method="HEAD",
            upstream_path=f"/models/{namespace}/{name}/resolve/main/",
            status_code=404, x_error_code="RepoNotFound",
            x_error_message="not found",
            decision="LOCAL_MISS", duration_ms=1, error=None,
            response_body_preview='{"error": "Repository not found"}',
            response_headers={"content-type": "application/json"},
            kind="local",
        )

    monkeypatch.setattr(
        "kohakuhub.api.fallback.probe_local.probe_local", _fake_local
    )

    report = await core.probe_full_chain(
        op="resolve", repo_type="model", namespace="ns", name="n",
        sources=[], client_factory=_FakeClient,
    )
    assert report.final_outcome == "CHAIN_EXHAUSTED"
    assert report.final_response is not None
    assert report.final_response["status_code"] == 404
    assert report.final_response["body_preview"] == (
        '{"error": "Repository not found"}'
    )


@pytest.mark.asyncio
async def test_probe_full_chain_exhaust_with_sources_uses_aggregate(
    monkeypatch,
):
    """``probe_full_chain`` with sources walked + LOCAL_MISS + every
    source falls through → production hands the client the
    ``build_aggregate_failure_response`` body. Simulate's
    ``final_response`` must match that — *not* the local 404, since
    the production caller never sees the local body when the chain
    has been walked."""

    async def _fake_local(op, repo_type, namespace, name, **kw):
        return core.ProbeAttempt(
            source_name="local", source_url="",
            source_type="local", method="HEAD",
            upstream_path=f"/models/{namespace}/{name}/resolve/main/",
            status_code=404, x_error_code="RepoNotFound",
            x_error_message="not found",
            decision="LOCAL_MISS", duration_ms=1, error=None,
            response_body_preview='{"error": "Repository not found"}',
            response_headers={"content-type": "application/json"},
            kind="local",
        )

    monkeypatch.setattr(
        "kohakuhub.api.fallback.probe_local.probe_local", _fake_local
    )

    SRC = {"name": "hf", "url": "https://huggingface.co", "source_type": "huggingface"}
    _FakeClient.queue(
        SRC["url"], "HEAD",
        _resp(401, headers={"x-error-message": "Invalid username or password."}),
    )
    report = await core.probe_full_chain(
        op="resolve", repo_type="model", namespace="ns", name="n",
        sources=[SRC], client_factory=_FakeClient,
    )
    assert report.final_outcome == "CHAIN_EXHAUSTED"
    assert report.final_response is not None
    # NOT the local 404 body — the aggregate body.
    assert report.final_response["status_code"] == 404
    assert report.final_response["headers"]["x-error-code"] == "RepoNotFound"
    body = json.loads(report.final_response["body_preview"])
    assert body["error"] == "RepoNotFound"
    assert body["detail"] == "No fallback source serves this repository."
    assert body["sources"][0]["name"] == "hf"
    assert body["sources"][0]["status"] == 401


# ---------------------------------------------------------------------------
# _build_chain_exhausted_aggregate — NETWORK_ERROR transport branch
# (mirrors the TIMEOUT test above; both are transport-level failures
# that production routes through ``build_aggregate_failure_response``
# with status=None and the per-decision category retained.)
# ---------------------------------------------------------------------------


def test_build_chain_exhausted_aggregate_network_error_attempts_use_network_category():
    """Per-source ``NETWORK_ERROR`` (e.g. DNS failure, connection
    refused, TLS error) keeps the ``network`` category through
    aggregate reconstruction — paired with the existing TIMEOUT test
    above so the simulate's transport-failure branches are both
    pinned. Without this, production-parity for "all sources network-
    failed" simulate output silently regresses."""
    out = core._build_chain_exhausted_aggregate(
        "info",
        [_attempt(
            status_code=None, decision="NETWORK_ERROR",
            error="dns lookup failed: no such host",
        )],
    )
    assert out["status_code"] == 502
    body = json.loads(out["body_preview"])
    assert body["sources"][0]["category"] == "network"
    assert body["sources"][0]["status"] is None
    assert "dns lookup failed" in body["sources"][0]["message"]


def test_build_chain_exhausted_aggregate_network_error_falls_back_to_default_message():
    """When ``error`` is unset on a NETWORK_ERROR attempt, the helper
    fills in ``"network error"`` so the per-source message field is
    never empty in the simulate JSON."""
    out = core._build_chain_exhausted_aggregate(
        "info",
        [_attempt(status_code=None, decision="NETWORK_ERROR", error=None)],
    )
    body = json.loads(out["body_preview"])
    assert body["sources"][0]["message"] == "network error"


# ---------------------------------------------------------------------------
# _extract_message_from_preview — branches not reached on the happy path
#
# The helper is called via ``_build_chain_exhausted_aggregate`` to mirror
# production's ``extract_error_message``. Most production-shape JSON
# uses a top-level string ``error`` field (covered above); these tests
# pin the less-common payload shapes so the simulate doesn't crash on
# (or silently mishandle) responses outside that shape.
# ---------------------------------------------------------------------------


def test_extract_message_from_preview_truncation_only_returns_none():
    """When the preview is *only* the truncation marker (caller
    captured zero bytes before truncation kicked in), stripping the
    marker leaves an empty string. Returning ``None`` lets the caller
    fall back to ``HTTP {status}`` rather than emitting an empty
    message — production's ``extract_error_message`` does the same."""
    assert core._extract_message_from_preview(
        "\n…[truncated, total 9999 bytes]"
    ) is None


def test_extract_message_from_preview_handles_nested_dict_message():
    """Some upstream APIs return ``{"detail": {"message": "..."}}``
    instead of a flat ``{"detail": "..."}``. The extractor descends one
    level so the user-facing message field still surfaces. Pin this
    so a future "flat-only" simplification doesn't silently lose the
    nested-dict payload that some HF mirrors / proxies emit."""
    assert core._extract_message_from_preview(
        '{"detail": {"message": "real cause", "code": 42}}'
    ) == "real cause"


def test_extract_message_from_preview_dict_without_known_fields_returns_str():
    """Dict payload with none of the
    ``error/message/detail/msg`` fields — the extractor falls back to
    ``str(parsed)`` rather than returning ``None`` so the operator
    still sees the raw payload (debugging aid). The simulate JSON's
    ``message`` field then carries the stringified dict, capped at
    ``MAX_ATTEMPT_MESSAGE_LEN`` by the caller."""
    out = core._extract_message_from_preview('{"unrelated": "value", "n": 7}')
    # Python's str(dict) ordering is insertion-stable since 3.7.
    assert out == "{'unrelated': 'value', 'n': 7}"


def test_extract_message_from_preview_top_level_non_dict_returns_str():
    """Valid JSON, but the top level is a list / number / string
    rather than a dict (rare on HF but happens on some proxies that
    return bare arrays). Falls through to ``str(parsed)`` so the
    payload still appears in the simulate."""
    assert core._extract_message_from_preview("[1, 2, 3]") == "[1, 2, 3]"
    assert core._extract_message_from_preview("42") == "42"
