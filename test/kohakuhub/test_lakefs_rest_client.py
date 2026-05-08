"""Unit tests for the LakeFS REST client."""

from __future__ import annotations

from collections import deque

import httpx
import pytest

import kohakuhub.lakefs_rest_client as lakefs_rest


@pytest.fixture(autouse=True)
def _reset_singleton_between_tests():
    """Drop the module-level pooled singleton before/after every test.

    Without this, a test that monkey-patches ``cfg.lakefs`` would build a
    singleton wired to those values; the next test would inherit it even
    after its own ``monkeypatch.setattr`` reverted the cfg. Resetting both
    ways keeps each test isolated.
    """
    lakefs_rest._singleton_client = None
    yield
    lakefs_rest._singleton_client = None


def _response(method: str, url: str, *, status: int = 200, json_data=None, text: str | None = None, content: bytes | None = None):
    request = httpx.Request(method, url)
    kwargs = {}
    if json_data is not None:
        kwargs["json"] = json_data
    if text is not None:
        kwargs["content"] = text.encode()
    if content is not None:
        kwargs["content"] = content
    return httpx.Response(status, request=request, **kwargs)


class _AsyncClientFactory:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    def __call__(self, *args, **kwargs):
        factory = self

        class _Client:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

            async def get(self_inner, url, **kwargs):
                factory.calls.append(("GET", url, kwargs))
                return factory.responses.popleft()

            async def post(self_inner, url, **kwargs):
                factory.calls.append(("POST", url, kwargs))
                return factory.responses.popleft()

            async def put(self_inner, url, **kwargs):
                factory.calls.append(("PUT", url, kwargs))
                return factory.responses.popleft()

            async def delete(self_inner, url, **kwargs):
                factory.calls.append(("DELETE", url, kwargs))
                return factory.responses.popleft()

        return _Client()


@pytest.mark.asyncio
async def test_check_response_and_core_object_commit_methods(monkeypatch):
    client = lakefs_rest.LakeFSRestClient("https://lakefs.example.com/", "ak", "sk")

    error_response = _response(
        "GET",
        "https://lakefs.example.com/api/v1/repositories/repo/refs/main/objects",
        status=500,
        text="boom",
    )
    with pytest.raises(httpx.HTTPStatusError) as response_error:
        client._check_response(error_response)
    assert "LakeFS API error 500" in str(response_error.value)

    factory = _AsyncClientFactory(
        [
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/objects",
                content=b"file-bytes",
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/objects/stat",
                json_data={"path": "file.txt", "size_bytes": 5},
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/objects",
                json_data={"path": "file.txt"},
            ),
            _response(
                "PUT",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/staging/backing",
                json_data={"path": "weights.bin"},
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/commits",
                json_data={"id": "commit-1"},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/commits/commit-1",
                json_data={"id": "commit-1"},
            ),
        ]
    )
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    assert await client.get_object("repo", "main", "file.txt", range_header="bytes=0-9") == b"file-bytes"
    assert await client.stat_object("repo", "main", "file.txt") == {
        "path": "file.txt",
        "size_bytes": 5,
    }
    assert await client.upload_object("repo", "main", "file.txt", b"hello", force=True) == {
        "path": "file.txt"
    }

    staging_metadata = lakefs_rest.StagingMetadata(
        staging=lakefs_rest.StagingLocation(physical_address="s3://bucket/path"),
        checksum="etag",
        size_bytes=5,
        content_type="application/octet-stream",
    )
    assert await client.link_physical_address("repo", "main", "weights.bin", staging_metadata) == {
        "path": "weights.bin"
    }
    assert await client.commit("repo", "main", "Add file", metadata={"email": "alice@example.com"}) == {
        "id": "commit-1"
    }
    assert await client.get_commit("repo", "commit-1") == {"id": "commit-1"}

    get_call = factory.calls[0]
    assert get_call[2]["headers"] == {"Range": "bytes=0-9"}
    assert factory.calls[2][2]["params"] == {"path": "file.txt", "force": True}
    assert factory.calls[3][2]["json"]["checksum"] == "etag"
    assert factory.calls[4][2]["json"] == {
        "message": "Add file",
        "metadata": {"email": "alice@example.com"},
    }


@pytest.mark.asyncio
async def test_log_diff_list_repository_and_branch_methods(monkeypatch):
    client = lakefs_rest.LakeFSRestClient("https://lakefs.example.com", "ak", "sk")
    factory = _AsyncClientFactory(
        [
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/commits",
                json_data={"results": []},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/base/diff/head",
                json_data={"results": []},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/objects/ls",
                json_data={"results": []},
            ),
            _response(
                "DELETE",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/objects",
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories",
                json_data={"id": "repo"},
            ),
            _response(
                "DELETE",
                "https://lakefs.example.com/api/v1/repositories/repo",
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo",
                json_data={"id": "repo"},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo",
                status=404,
                text="missing",
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main",
                json_data={"id": "main", "commit_id": "commit-1"},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/branches",
                json_data={"results": []},
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/branches",
            ),
            _response(
                "DELETE",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/dev",
            ),
        ]
    )
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    assert await client.log_commits("repo", "main", after="cursor-1", amount=5) == {"results": []}
    assert await client.diff_refs("repo", "base", "head", after="cursor-2", amount=10) == {"results": []}
    assert await client.list_objects("repo", "main", prefix="data/", after="cursor-3", delimiter="/") == {
        "results": []
    }
    await client.delete_object("repo", "main", "file.txt", force=True)
    assert await client.create_repository("repo", "s3://bucket/repo", default_branch="main") == {"id": "repo"}
    await client.delete_repository("repo", force=True)
    assert await client.get_repository("repo") == {"id": "repo"}
    assert await client.repository_exists("repo") is False
    assert await client.get_branch("repo", "main") == {"id": "main", "commit_id": "commit-1"}
    assert await client.list_branches("repo", after="cursor-4", amount=20) == {"results": []}
    await client.create_branch("repo", "dev", "main")
    await client.delete_branch("repo", "dev", force=True)

    # log_commits emits params as a list-of-tuples so list-valued query
    # params (objects, prefixes — see ``log_commits`` docstring) can be
    # serialised as repeats. The ``after``/``amount`` pair still appears in
    # order at the head of the list.
    assert factory.calls[0][2]["params"] == [("after", "cursor-1"), ("amount", 5)]
    assert factory.calls[1][2]["params"] == {"after": "cursor-2", "amount": 10}
    assert factory.calls[2][2]["params"] == {
        "amount": 1000,
        "prefix": "data/",
        "after": "cursor-3",
        "delimiter": "/",
    }
    assert factory.calls[3][2]["params"] == {"path": "file.txt", "force": True}
    assert factory.calls[4][2]["json"]["storage_namespace"] == "s3://bucket/repo"
    assert factory.calls[5][2]["params"] == {"force": True}
    assert factory.calls[9][2]["params"] == {"after": "cursor-4", "amount": 20}
    assert factory.calls[10][2]["json"] == {"name": "dev", "source": "main"}
    assert factory.calls[11][2]["params"] == {"force": True}


@pytest.mark.asyncio
async def test_list_repositories_and_repository_exists_happy_paths(monkeypatch):
    """Cover the two LakeFS methods the broader test_log_diff suite skips:
    ``list_repositories`` (paginated repo enumeration) and the 200-OK branch
    of ``repository_exists`` (existing repo). These are touched by the pool
    refactor — every method now goes through ``self._httpx()`` — so the
    patch line gets hit only when each method is actually exercised.
    """
    client = lakefs_rest.LakeFSRestClient("https://lakefs.example.com", "ak", "sk")
    factory = _AsyncClientFactory(
        [
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories",
                json_data={"results": [{"id": "repo-a"}, {"id": "repo-b"}]},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo-a",
                json_data={"id": "repo-a"},
            ),
        ]
    )
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    # list_repositories — pagination params surface as a dict.
    listed = await client.list_repositories(amount=50, after="cursor-x")
    assert listed == {"results": [{"id": "repo-a"}, {"id": "repo-b"}]}
    assert factory.calls[0][2]["params"] == {"amount": 50, "after": "cursor-x"}

    # repository_exists — 200 path returns True (the 404 path is already
    # covered in test_log_diff_list_repository_and_branch_methods).
    assert await client.repository_exists("repo-a") is True


@pytest.mark.asyncio
async def test_log_commits_path_filter_params(monkeypatch):
    """``log_commits`` must serialise ``objects`` / ``prefixes`` as repeated
    query params (LakeFS v0.54.0+ logCommits filter), encode ``limit`` and
    ``first_parent`` as ``"true"``/``"false"`` strings, and combine them with
    ``after`` / ``amount`` in the order they were passed.
    """
    client = lakefs_rest.LakeFSRestClient("https://lakefs.example.com", "ak", "sk")
    factory = _AsyncClientFactory(
        [
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/commits",
                json_data={"results": []},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/commits",
                json_data={"results": []},
            ),
        ]
    )
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    # 1) Single-object filter — the canonical "last commit that touched X"
    # call shape used by resolve_last_commits_for_paths.
    await client.log_commits(
        "repo", "main",
        objects=["docs/guide.md"],
        amount=1,
        limit=True,
    )
    assert factory.calls[0][2]["params"] == [
        ("amount", 1),
        ("objects", "docs/guide.md"),
        ("limit", "true"),
    ]

    # 2) Multi-object + prefix + first_parent — exercises the repeated-param
    # serialisation that distinguishes our new code from the pre-rewrite
    # behaviour. ``after`` and ``amount`` retain their leading position when
    # present.
    await client.log_commits(
        "repo", "main",
        after="cursor-7",
        amount=50,
        objects=["a.txt", "b.txt"],
        prefixes=["docs/", "weights/"],
        first_parent=False,
    )
    assert factory.calls[1][2]["params"] == [
        ("after", "cursor-7"),
        ("amount", 50),
        ("objects", "a.txt"),
        ("objects", "b.txt"),
        ("prefixes", "docs/"),
        ("prefixes", "weights/"),
        ("first_parent", "false"),
    ]


@pytest.mark.asyncio
async def test_log_commits_omits_unset_path_filter_params(monkeypatch):
    """When ``objects`` / ``prefixes`` / ``limit`` / ``first_parent`` are not
    passed, the wire request must NOT carry any of those keys. LakeFS's
    handler skips path filtering only when those params are absent — sending
    e.g. ``objects=[]`` or ``limit=false`` could change behaviour on some
    server versions.
    """
    client = lakefs_rest.LakeFSRestClient("https://lakefs.example.com", "ak", "sk")
    factory = _AsyncClientFactory(
        [
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/commits",
                json_data={"results": []},
            ),
        ]
    )
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    await client.log_commits("repo", "main")
    params = factory.calls[0][2]["params"]
    keys = {k for k, _ in params}
    # No path-filter params, no limit, no first_parent — completely bare
    # cursor-less log query.
    assert keys == set(), f"expected no params, got {params!r}"


@pytest.mark.asyncio
async def test_log_commits_first_parent_true(monkeypatch):
    """The ``first_parent`` parameter must serialise as the literal string
    ``"true"`` (not Python ``True``); LakeFS's query-param parser only
    recognises the lowercase string forms.
    """
    client = lakefs_rest.LakeFSRestClient("https://lakefs.example.com", "ak", "sk")
    factory = _AsyncClientFactory(
        [
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/commits",
                json_data={"results": []},
            ),
        ]
    )
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    await client.log_commits("repo", "main", first_parent=True)
    params = factory.calls[0][2]["params"]
    assert params == [("first_parent", "true")]


@pytest.mark.asyncio
async def test_tag_revert_merge_reset_and_factory_cover_optional_payloads(monkeypatch):
    client = lakefs_rest.LakeFSRestClient("https://lakefs.example.com", "ak", "sk")
    factory = _AsyncClientFactory(
        [
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/tags",
                json_data={"id": "v1"},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/tags",
                json_data={"results": []},
            ),
            _response(
                "DELETE",
                "https://lakefs.example.com/api/v1/repositories/repo/tags/v1",
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/revert",
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/feature/merge/main",
                json_data={"reference": "main"},
            ),
            _response(
                "PUT",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/hard_reset",
            ),
        ]
    )
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "endpoint", "https://cfg-lakefs")
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "access_key", "cfg-ak")
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "secret_key", "cfg-sk")

    assert await client.create_tag("repo", "v1", "main", force=True) == {"id": "v1"}
    assert await client.list_tags("repo", after="cursor-5", amount=50) == {"results": []}
    await client.delete_tag("repo", "v1", force=True)
    await client.revert_branch(
        "repo",
        "main",
        "commit-1",
        parent_number=2,
        message="Revert commit",
        metadata={"email": "alice@example.com"},
        force=True,
        allow_empty=True,
    )
    assert await client.merge_into_branch(
        "repo",
        "feature",
        "main",
        message="Merge feature",
        metadata={"email": "alice@example.com"},
        strategy="source-wins",
        force=True,
        allow_empty=True,
        squash_merge=True,
    ) == {"reference": "main"}
    await client.hard_reset_branch("repo", "main", "commit-2", force=True)

    assert factory.calls[0][2]["json"] == {"id": "v1", "ref": "main", "force": True}
    assert factory.calls[1][2]["params"] == {"after": "cursor-5", "amount": 50}
    assert factory.calls[2][2]["params"] == {"force": True}
    assert factory.calls[3][2]["json"] == {
        "ref": "commit-1",
        "parent_number": 2,
        "force": True,
        "allow_empty": True,
        "commit_overrides": {
            "message": "Revert commit",
            "metadata": {"email": "alice@example.com"},
        },
    }
    assert factory.calls[4][2]["json"] == {
        "force": True,
        "allow_empty": True,
        "squash_merge": True,
        "message": "Merge feature",
        "metadata": {"email": "alice@example.com"},
        "strategy": "source-wins",
    }
    assert factory.calls[5][2]["params"] == {"ref": "commit-2", "force": True}

    configured_client = lakefs_rest.get_lakefs_rest_client()
    assert configured_client.endpoint == "https://cfg-lakefs"
    assert configured_client.auth == ("cfg-ak", "cfg-sk")


# ---------------------------------------------------------------------------
# Connection-pool semantics (issue #59 follow-up).
#
# These tests pin the invariants the new pooled implementation must hold.
# Mock-only — the real-backend integration coverage lives next to the
# tree route tests.
# ---------------------------------------------------------------------------


class _CountingFactory:
    """Track how many ``httpx.AsyncClient(...)`` constructor calls happen.

    The pooled implementation should construct exactly one underlying
    httpx client per ``LakeFSRestClient`` instance, regardless of how many
    requests are issued through it.
    """

    def __init__(self):
        self.constructor_calls = 0
        self.constructor_kwargs: list[dict] = []
        self.method_calls: list[str] = []

    def __call__(self, *args, **kwargs):
        self.constructor_calls += 1
        self.constructor_kwargs.append(dict(kwargs))
        outer = self

        class _Client:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *args):
                return False

            async def aclose(self_inner):
                return None

            async def get(self_inner, url, **kwargs):
                outer.method_calls.append(f"GET {url}")
                return _response("GET", url, json_data={})

            async def post(self_inner, url, **kwargs):
                outer.method_calls.append(f"POST {url}")
                return _response("POST", url, json_data={})

            async def put(self_inner, url, **kwargs):
                outer.method_calls.append(f"PUT {url}")
                return _response("PUT", url, json_data={})

            async def delete(self_inner, url, **kwargs):
                outer.method_calls.append(f"DELETE {url}")
                return _response("DELETE", url)

        return _Client()


@pytest.mark.asyncio
async def test_pooled_httpx_client_constructed_once_per_instance(monkeypatch):
    """One ``LakeFSRestClient`` should yield ONE underlying httpx client,
    no matter how many requests pass through it. This is the whole point
    of the pool — without it, every method call would pay a fresh
    TCP+TLS handshake.
    """
    factory = _CountingFactory()
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    client = lakefs_rest.LakeFSRestClient("https://x.example", "ak", "sk")
    # Five different methods on the same client instance.
    await client.get_branch("repo", "main")
    await client.list_branches("repo")
    await client.log_commits("repo", "main")
    await client.diff_refs("repo", "left", "right")
    await client.get_repository("repo")

    assert factory.constructor_calls == 1, (
        f"expected 1 httpx.AsyncClient constructor call, got "
        f"{factory.constructor_calls}"
    )
    # All five method calls landed on the SAME underlying client.
    assert len(factory.method_calls) == 5


@pytest.mark.asyncio
async def test_pooled_httpx_client_uses_keepalive_limits(monkeypatch):
    """The pooled client must be constructed with ``httpx.Limits``
    matching ``_HTTPX_LIMITS`` (max_connections=64, max_keepalive=32,
    keepalive_expiry=30s). Drift here defeats the connection pooling.
    """
    factory = _CountingFactory()
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    client = lakefs_rest.LakeFSRestClient("https://x.example", "ak", "sk")
    await client.get_branch("repo", "main")

    assert factory.constructor_calls == 1
    kw = factory.constructor_kwargs[0]
    limits = kw["limits"]
    assert limits is lakefs_rest._HTTPX_LIMITS
    assert limits.max_connections == 64
    assert limits.max_keepalive_connections == 32
    assert limits.keepalive_expiry == 30.0
    # ``timeout=None`` matches the previous unpooled per-call default —
    # we don't want to silently introduce a tighter budget at this layer.
    assert kw["timeout"] is None


@pytest.mark.asyncio
async def test_aclose_drops_pooled_client_and_relazy_init_on_next_use(monkeypatch):
    """``aclose()`` must close the current pooled client and clear the
    cache so the next call lazily rebuilds. This is the same lifecycle
    the FastAPI lifespan hook relies on at shutdown.
    """
    factory = _CountingFactory()
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    client = lakefs_rest.LakeFSRestClient("https://x.example", "ak", "sk")
    await client.get_branch("repo", "main")
    assert factory.constructor_calls == 1
    assert client._httpx_client is not None

    await client.aclose()
    assert client._httpx_client is None

    # aclose must be idempotent — second call is a no-op, not an error.
    await client.aclose()

    # Next request lazily rebuilds the pooled client.
    await client.get_branch("repo", "main")
    assert factory.constructor_calls == 2


@pytest.mark.asyncio
async def test_get_lakefs_rest_client_returns_singleton_across_calls(monkeypatch):
    """``get_lakefs_rest_client()`` must return the same instance across
    calls — the whole point is that all FastAPI handlers share one pooled
    httpx connection bag, not that each handler gets a fresh one.
    """
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "endpoint", "https://lakefs")
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "access_key", "ak")
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "secret_key", "sk")

    a = lakefs_rest.get_lakefs_rest_client()
    b = lakefs_rest.get_lakefs_rest_client()
    c = lakefs_rest.get_lakefs_rest_client()
    assert a is b is c


@pytest.mark.asyncio
async def test_lifespan_shutdown_closes_pooled_client(monkeypatch):
    """The FastAPI lifespan in ``main.py`` calls
    ``close_lakefs_rest_client()`` in its ``finally`` clause so the pooled
    httpx connections are released cleanly on worker shutdown. Drive the
    lifespan context manager by hand and verify the singleton is None
    afterwards.

    Test-state plumbing reloads backend modules under
    ``force_reload=True`` (see ``support/bootstrap.py``); the
    module-level ``import kohakuhub.lakefs_rest_client as lakefs_rest``
    at the top of this file therefore freezes a *pre-reload* module
    reference, while the lifespan's inline ``from
    kohakuhub.lakefs_rest_client import close_lakefs_rest_client``
    resolves through ``sys.modules`` and gets the *post-reload* one. We
    have to look up the live module the same way to actually observe
    the singleton state the lifespan touches.
    """
    import sys
    live_lakefs = sys.modules["kohakuhub.lakefs_rest_client"]

    factory = _CountingFactory()
    monkeypatch.setattr(live_lakefs.httpx, "AsyncClient", factory)
    monkeypatch.setattr(live_lakefs.cfg.lakefs, "endpoint", "https://lakefs")
    monkeypatch.setattr(live_lakefs.cfg.lakefs, "access_key", "ak")
    monkeypatch.setattr(live_lakefs.cfg.lakefs, "secret_key", "sk")

    # Force the singleton into existence so the lifespan finally has
    # something to clean up.
    a = live_lakefs.get_lakefs_rest_client()
    await a.get_branch("repo", "main")
    assert live_lakefs._singleton_client is a

    # Drive the lifespan context manager by hand. ``init_storage`` is
    # mocked because the lifespan calls it eagerly and it'd otherwise
    # try to talk to S3. Use ``importlib`` so we get whichever
    # ``kohakuhub.main`` is currently registered in ``sys.modules``
    # (the test bootstrap may have force-reloaded it; if no test in
    # the session has imported it yet, this triggers a fresh import).
    import importlib
    main_mod = importlib.import_module("kohakuhub.main")
    monkeypatch.setattr(main_mod, "init_storage", lambda: None)

    class _StubApp:
        # The lifespan only consumes ``app`` as its parameter; nothing
        # else on the app is touched.
        ...

    async with main_mod.lifespan(_StubApp()):
        # Inside the lifespan, the singleton is still alive.
        assert live_lakefs._singleton_client is a

    # The finally block must have torn it down.
    assert live_lakefs._singleton_client is None


@pytest.mark.asyncio
async def test_close_lakefs_rest_client_resets_singleton(monkeypatch):
    """``close_lakefs_rest_client()`` must close the underlying client and
    drop the module-level cache so subsequent ``get_lakefs_rest_client()``
    rebuilds. Idempotent — calling close on an already-closed module is OK.
    """
    factory = _CountingFactory()
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "endpoint", "https://lakefs")
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "access_key", "ak")
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "secret_key", "sk")

    a = lakefs_rest.get_lakefs_rest_client()
    await a.get_branch("repo", "main")
    assert lakefs_rest._singleton_client is a

    await lakefs_rest.close_lakefs_rest_client()
    assert lakefs_rest._singleton_client is None

    # Idempotent.
    await lakefs_rest.close_lakefs_rest_client()

    b = lakefs_rest.get_lakefs_rest_client()
    assert b is not a
    assert lakefs_rest._singleton_client is b
