"""Unit tests for repository tree routes."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.responses import JSONResponse

import kohakuhub.api.repo.routers.tree as tree_api


class _FakeLakeFSClient:
    def __init__(self, *, list_responses=None, stat_map=None, list_map=None):
        self.list_responses = list(list_responses or [])
        self.stat_map = dict(stat_map or {})
        self.list_map = dict(list_map or {})
        self.list_calls = []
        self.stat_calls = []

    async def list_objects(self, **kwargs):
        self.list_calls.append(kwargs)
        if self.list_responses:
            result = self.list_responses.pop(0)
        else:
            result = self.list_map[kwargs["prefix"]]
        if isinstance(result, Exception):
            raise result
        return result

    async def stat_object(self, **kwargs):
        self.stat_calls.append(kwargs)
        result = self.stat_map[kwargs["path"]]
        if isinstance(result, Exception):
            raise result
        return result


class _Expression:
    def __init__(self, label: str):
        self.label = label

    def __and__(self, other: "_Expression") -> "_Expression":
        return _Expression(f"({self.label}&{other.label})")


class _Field:
    def __init__(self, label: str):
        self.label = label

    def __eq__(self, other) -> _Expression:  # noqa: ANN001 - Peewee-style stub
        return _Expression(f"{self.label}=={other!r}")

    def in_(self, values) -> _Expression:  # noqa: ANN001 - Peewee-style stub
        return _Expression(f"{self.label}.in_({list(values)!r})")


class _FakeQuery(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.where_expression = None

    def where(self, expression):
        self.where_expression = expression
        return self


def _json_body(response: JSONResponse) -> list[dict]:
    return json.loads(response.body.decode())


def _request(path: str, query=None):
    return SimpleNamespace(
        query_params=query or {},
        url=SimpleNamespace(path=path),
    )


def test_helper_functions_cover_path_formatting_links_and_file_records(monkeypatch):
    assert tree_api._normalize_repo_path("/nested/path/") == "nested/path"
    assert tree_api._normalize_repo_path("/") == ""
    assert tree_api._format_last_modified(None) is None
    assert tree_api._format_last_modified(0) is None
    assert tree_api._format_commit_date(None) is None
    assert tree_api._format_commit_date("2026-04-21T00:00:00.000000Z") == (
        "2026-04-21T00:00:00.000000Z"
    )

    serialized = tree_api._serialize_last_commit(
        {
            "id": "commit-1",
            "message": "Add README",
            "creation_date": 1713657600,
        }
    )
    assert serialized["id"] == "commit-1"
    assert serialized["title"] == "Add README"
    assert serialized["date"].endswith("Z")

    assert tree_api._build_lfs_payload("sha256", 32) == {
        "oid": "sha256",
        "size": 32,
        "pointerSize": 134,
    }

    monkeypatch.setattr(tree_api.cfg.app, "base_url", "https://hub.local/")
    next_link = tree_api._build_public_link(
        _request(
            "/api/models/owner/demo/tree/main/docs",
            query={"recursive": "false", "expand": "true"},
        ),
        limit=50,
        cursor="cursor-2",
    )
    parsed = urlparse(next_link)
    assert parsed.scheme == "https"
    assert parsed.netloc == "hub.local"
    assert parsed.path == "/api/models/owner/demo/tree/main/docs"
    assert parse_qs(parsed.query) == {
        "recursive": ["false"],
        "expand": ["true"],
        "limit": ["50"],
        "cursor": ["cursor-2"],
    }

    rows = [
        SimpleNamespace(path_in_repo="README.md", sha256="sha-readme"),
        SimpleNamespace(path_in_repo="weights/model.bin", sha256="sha-lfs"),
    ]
    fake_query = _FakeQuery(rows)

    class _FakeFileModel:
        repository = _Field("repository")
        path_in_repo = _Field("path_in_repo")
        is_deleted = _Field("is_deleted")

        @staticmethod
        def select():
            return fake_query

    monkeypatch.setattr(tree_api, "File", _FakeFileModel)

    records = tree_api._build_file_record_map(
        SimpleNamespace(id=1),
        ["README.md", "weights/model.bin"],
    )
    assert records == {
        "README.md": rows[0],
        "weights/model.bin": rows[1],
    }
    assert fake_query.where_expression is not None
    assert tree_api._build_file_record_map(SimpleNamespace(id=1), []) == {}


@pytest.mark.asyncio
async def test_fetch_page_and_directory_stats_cover_pagination(monkeypatch):
    page_client = _FakeLakeFSClient(
        list_responses=[
            {
                "results": [{"path": "docs/a.txt", "path_type": "object"}],
                "pagination": {"has_more": False},
            }
        ]
    )
    monkeypatch.setattr(tree_api, "get_lakefs_client", lambda: page_client)

    page = await tree_api.fetch_lakefs_objects_page(
        "lake",
        "main",
        "docs/",
        recursive=False,
        amount=25,
    )
    assert page["results"][0]["path"] == "docs/a.txt"
    assert page_client.list_calls == [
        {
            "repository": "lake",
            "ref": "main",
            "prefix": "docs/",
            "delimiter": "/",
            "amount": 25,
            "after": "",
        }
    ]

    directory_client = _FakeLakeFSClient(
        list_responses=[
            {
                "results": [
                    {"path_type": "object", "size_bytes": 4, "mtime": 10},
                    {"path_type": "common_prefix", "size_bytes": 999, "mtime": 999},
                ],
                "pagination": {"has_more": True, "next_offset": "page-2"},
            },
            {
                "results": [
                    {"path_type": "object", "size_bytes": 6, "mtime": 20},
                ],
                "pagination": {"has_more": False},
            },
        ]
    )
    monkeypatch.setattr(tree_api, "get_lakefs_client", lambda: directory_client)

    total_size, latest_mtime = await tree_api._calculate_directory_stats(
        "lake",
        "main",
        "docs",
    )
    assert total_size == 10
    assert latest_mtime == 20
    assert directory_client.list_calls[0]["prefix"] == "docs/"
    assert directory_client.list_calls[1]["after"] == "page-2"


def test_make_tree_item_covers_file_directory_and_lfs_payload(monkeypatch):
    monkeypatch.setattr(tree_api, "should_use_lfs", lambda repository, path, size: False)

    file_record = SimpleNamespace(sha256="sha256-lfs", lfs=True)
    file_item = tree_api._make_tree_item(
        {
            "path_type": "object",
            "path": "weights/model.bin",
            "size_bytes": 32,
            "checksum": "lakefs-sha",
            "mtime": 1713657600,
        },
        repository=SimpleNamespace(id=1),
        file_records={"weights/model.bin": file_record},
        expand=True,
        last_commit={"id": "commit-1", "title": "Track weights"},
    )
    assert file_item == {
        "type": "file",
        "oid": "sha256-lfs",
        "size": 32,
        "path": "weights/model.bin",
        "lastModified": tree_api._format_last_modified(1713657600),
        "lfs": {
            "oid": "sha256-lfs",
            "size": 32,
            "pointerSize": 134,
        },
        "lastCommit": {"id": "commit-1", "title": "Track weights"},
        "securityFileStatus": None,
    }

    directory_item = tree_api._make_tree_item(
        {
            "path_type": "common_prefix",
            "path": "docs/",
            "checksum": "tree-oid",
            "mtime": 1713657600,
        },
        repository=SimpleNamespace(id=1),
        file_records={},
        expand=True,
        last_commit={"id": "commit-2", "title": "Docs refresh"},
    )
    assert directory_item == {
        "type": "directory",
        "oid": "tree-oid",
        "size": 0,
        "path": "docs",
        "lastModified": tree_api._format_last_modified(1713657600),
        "lastCommit": {"id": "commit-2", "title": "Docs refresh"},
    }

@pytest.mark.asyncio
async def test_resolve_last_commits_for_paths_uses_lakefs_path_filter(monkeypatch):
    """The new implementation issues one ``log_commits`` call per target with
    the matching ``objects=`` (file) or ``prefixes=`` (directory) filter,
    relying on LakeFS's metarange-tree short-circuit instead of walking
    diffs client-side. Verify the call shape and that the response is
    decoded into the HF-compatible ``{id, title, date}`` payload.
    """
    # Targets are fanned out in parallel via asyncio.gather, so we route the
    # mocked LakeFS responses by the ``objects``/``prefixes`` kwarg each call
    # carries — asserting the *set* of calls keeps the test robust to
    # scheduler order.
    canned: dict[tuple[str, str], dict] = {
        ("objects", "weights/model.bin"): {
            "results": [
                {
                    "id": "commit-7",
                    "message": "Refresh model weights",
                    "creation_date": 1713657600,
                    "parents": ["commit-6"],
                }
            ],
            "pagination": {"has_more": False},
        },
        ("prefixes", "docs/"): {
            "results": [
                {
                    "id": "commit-5",
                    "message": "Edit docs",
                    "creation_date": 1713657500,
                    "parents": ["commit-4"],
                }
            ],
            "pagination": {"has_more": False},
        },
        # Path with no qualifying commit anywhere in history → empty results.
        ("objects", "ghost.txt"): {
            "results": [],
            "pagination": {"has_more": False},
        },
    }

    seen_calls: list[dict] = []

    class _RoutingClient:
        async def log_commits(self, **kwargs):
            seen_calls.append(dict(kwargs))
            objs = kwargs.get("objects")
            prefs = kwargs.get("prefixes")
            if objs:
                key = ("objects", objs[0])
            elif prefs:
                key = ("prefixes", prefs[0])
            else:
                raise AssertionError(
                    "resolve_last_commits_for_paths must always pass either "
                    "objects= or prefixes="
                )
            return canned[key]

    monkeypatch.setattr(tree_api, "get_lakefs_rest_client", lambda: _RoutingClient())

    resolved = await tree_api.resolve_last_commits_for_paths(
        "lake",
        "main",
        [
            {"path": "docs", "type": "directory"},
            {"path": "weights/model.bin", "type": "file"},
            {"path": "ghost.txt", "type": "file"},
        ],
    )

    # Output map: file/dir resolved to their commits, ghost path → None.
    assert resolved == {
        "docs": {
            "id": "commit-5",
            "title": "Edit docs",
            "date": tree_api._format_commit_date(1713657500),
        },
        "weights/model.bin": {
            "id": "commit-7",
            "title": "Refresh model weights",
            "date": tree_api._format_commit_date(1713657600),
        },
        "ghost.txt": None,
    }

    # Every call asks LakeFS for at most one commit and pins ``limit=true``
    # so the server stops walking after the first qualifying commit. There
    # are exactly N calls (one per target), no other primitives used.
    assert len(seen_calls) == 3
    for call in seen_calls:
        assert call["repository"] == "lake"
        assert call["ref"] == "main"
        assert call["amount"] == 1
        assert call["limit"] is True
        # Every call carries either objects= or prefixes= but not both.
        has_objects = bool(call.get("objects"))
        has_prefixes = bool(call.get("prefixes"))
        assert has_objects ^ has_prefixes, (
            f"each call must use exactly one of objects/prefixes, got {call!r}"
        )

    # Targets list shape sanity-checks.
    assert await tree_api.resolve_last_commits_for_paths("lake", "main", []) == {}


@pytest.mark.asyncio
async def test_resolve_last_commits_for_paths_handles_errors_and_missing_paths(monkeypatch):
    """Per-target ``log_commits`` failures must not bubble; affected paths
    just resolve to ``None`` and the rest of the page still surfaces. This
    matches the previous diff-walk behaviour, which logged-and-continued on
    LakeFS errors.
    """
    failures = {"alpha.txt"}

    class _PartiallyFailingClient:
        async def log_commits(self, **kwargs):
            objs = kwargs.get("objects") or []
            prefs = kwargs.get("prefixes") or []
            target = objs[0] if objs else prefs[0]
            if target in failures:
                raise RuntimeError("simulated LakeFS hiccup")
            return {
                "results": [
                    {
                        "id": "commit-99",
                        "message": "stable commit",
                        "creation_date": 1713600000,
                        "parents": ["commit-98"],
                    }
                ],
                "pagination": {"has_more": False},
            }

    monkeypatch.setattr(
        tree_api, "get_lakefs_rest_client", lambda: _PartiallyFailingClient()
    )

    resolved = await tree_api.resolve_last_commits_for_paths(
        "lake",
        "main",
        [
            {"path": "alpha.txt", "type": "file"},  # raises → None
            {"path": "beta.txt", "type": "file"},   # resolves → commit-99
            {"path": "", "type": "file"},          # empty path → skipped
        ],
    )
    assert resolved["alpha.txt"] is None
    assert resolved["beta.txt"]["id"] == "commit-99"
    assert "" not in resolved


@pytest.mark.asyncio
async def test_resolve_last_commits_for_paths_concurrency_capped(monkeypatch):
    """The fan-out must respect ``LAST_COMMIT_LOOKUP_CONCURRENCY`` so that a
    50-entry page does not detonate a remote LakeFS connection pool. We
    assert that no more than the configured cap of in-flight calls happens
    at once.
    """
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    class _CountingClient:
        async def log_commits(self, **kwargs):
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            # Yield to let other tasks accumulate before responding.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            async with lock:
                in_flight -= 1
            return {
                "results": [
                    {
                        "id": "c",
                        "message": "m",
                        "creation_date": 0,
                        "parents": [],
                    }
                ],
                "pagination": {"has_more": False},
            }

    monkeypatch.setattr(tree_api, "get_lakefs_rest_client", lambda: _CountingClient())

    targets = [{"path": f"file_{i:03d}.txt", "type": "file"} for i in range(50)]
    resolved = await tree_api.resolve_last_commits_for_paths("lake", "main", targets)

    assert len(resolved) == 50
    assert peak <= tree_api.LAST_COMMIT_LOOKUP_CONCURRENCY, (
        f"peak in-flight {peak} exceeded the configured concurrency cap "
        f"{tree_api.LAST_COMMIT_LOOKUP_CONCURRENCY}"
    )


@pytest.mark.asyncio
async def test_resolve_last_commits_for_paths_directory_filter_uses_trailing_slash(
    monkeypatch,
):
    """Directory targets must be passed to LakeFS as a strict-prefix filter
    (``prefixes=[path + "/"]``). Without the trailing slash, LakeFS would
    match siblings that share the directory's basename leading edge — e.g.
    ``prefixes=["docs"]`` would also match ``docs.txt`` or ``docs-old/``.
    Pin the exact wire shape so this regression cannot creep back in.
    """
    seen_calls: list[dict] = []

    class _CapturingClient:
        async def log_commits(self, **kwargs):
            seen_calls.append(dict(kwargs))
            return {
                "results": [
                    {
                        "id": "stub-commit",
                        "message": "stub",
                        "creation_date": 0,
                        "parents": [],
                    }
                ],
                "pagination": {"has_more": False},
            }

    monkeypatch.setattr(tree_api, "get_lakefs_rest_client", lambda: _CapturingClient())

    await tree_api.resolve_last_commits_for_paths(
        "lake",
        "main",
        [
            {"path": "docs", "type": "directory"},
            {"path": "nested/sub-tree", "type": "directory"},
            {"path": "model.bin", "type": "file"},
        ],
    )

    by_target: dict[str, dict] = {}
    for call in seen_calls:
        if call.get("objects"):
            by_target[call["objects"][0]] = call
        else:
            by_target[call["prefixes"][0]] = call

    # Directory entries are passed with the trailing slash exactly.
    assert "docs/" in by_target
    assert by_target["docs/"]["prefixes"] == ["docs/"]
    assert "objects" not in by_target["docs/"]
    assert "nested/sub-tree/" in by_target
    assert by_target["nested/sub-tree/"]["prefixes"] == ["nested/sub-tree/"]
    # File entries are passed verbatim, NO trailing slash, NO prefix mode.
    assert "model.bin" in by_target
    assert by_target["model.bin"]["objects"] == ["model.bin"]
    assert "prefixes" not in by_target["model.bin"]


@pytest.mark.asyncio
async def test_resolve_last_commits_for_paths_uses_only_first_returned_commit(
    monkeypatch,
):
    """Defensive: even though we ask LakeFS for ``amount=1``, a future server
    or proxy could return more than one row. The decoder must take results[0]
    and ignore the rest — taking last() or merging would silently change the
    visible ``lastCommit`` for the page.
    """

    class _OverProvisioningClient:
        async def log_commits(self, **kwargs):
            return {
                "results": [
                    {
                        "id": "newest",
                        "message": "newest",
                        "creation_date": 1700000200,
                        "parents": ["a"],
                    },
                    {
                        "id": "older",
                        "message": "older",
                        "creation_date": 1700000100,
                        "parents": ["b"],
                    },
                ],
                "pagination": {"has_more": True, "next_offset": "page-2"},
            }

    monkeypatch.setattr(
        tree_api, "get_lakefs_rest_client", lambda: _OverProvisioningClient()
    )

    resolved = await tree_api.resolve_last_commits_for_paths(
        "lake", "main", [{"path": "weights/model.bin", "type": "file"}]
    )
    assert resolved["weights/model.bin"]["id"] == "newest"


@pytest.mark.asyncio
async def test_resolve_last_commits_for_paths_skips_blank_path_entries(monkeypatch):
    """Targets with empty ``path`` (which can show up as residue from
    ``_normalize_repo_path('/')``) must be skipped *before* hitting LakeFS,
    not silently sent through. Sending ``objects=['']`` would 400 on LakeFS.
    """
    captured: list[dict] = []

    class _NoBlankClient:
        async def log_commits(self, **kwargs):
            captured.append(dict(kwargs))
            objs = kwargs.get("objects") or []
            prefs = kwargs.get("prefixes") or []
            for v in objs + prefs:
                assert v, "empty path must never reach LakeFS"
            return {"results": [], "pagination": {"has_more": False}}

    monkeypatch.setattr(tree_api, "get_lakefs_rest_client", lambda: _NoBlankClient())

    resolved = await tree_api.resolve_last_commits_for_paths(
        "lake",
        "main",
        [
            {"path": "", "type": "file"},
            {"path": "real.txt", "type": "file"},
            {"path": "", "type": "directory"},
        ],
    )

    # Only the real path issued a LakeFS call.
    assert len(captured) == 1
    assert captured[0]["objects"] == ["real.txt"]
    # And the output dict is keyed only on non-empty paths.
    assert "" not in resolved
    assert "real.txt" in resolved


@pytest.mark.asyncio
async def test_process_single_path_covers_file_directory_missing_and_errors(monkeypatch):
    class _NotFoundError(Exception):
        pass

    client = _FakeLakeFSClient(
        stat_map={
            "weights/model.bin": {
                "size_bytes": 32,
                "checksum": "lakefs-sha",
                "mtime": 1713657600,
            },
            "docs": _NotFoundError("missing file"),
            "ghost": _NotFoundError("missing path"),
            "broken-dir": _NotFoundError("broken dir"),
            "docs-error": _NotFoundError("directory stats failed"),
            "broken": RuntimeError("server error"),
        },
        list_map={
            "docs/": {"results": [{"checksum": "tree-oid", "mtime": 1713657610}]},
            "ghost/": {"results": []},
            "broken-dir/": RuntimeError("list failed"),
            "docs-error/": {
                "results": [{"checksum": "tree-oid-2", "mtime": 1713657615}]
            },
        },
    )
    monkeypatch.setattr(tree_api, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(
        tree_api,
        "should_use_lfs",
        lambda repository, path, size: path == "weights/model.bin",
    )
    monkeypatch.setattr(
        tree_api,
        "is_lakefs_not_found_error",
        lambda error: isinstance(error, _NotFoundError),
    )

    async def _fake_directory_stats(*args, **kwargs):
        if kwargs["directory_path"] == "docs-error":
            raise RuntimeError("stats failed")
        return (15, 1713657620)

    monkeypatch.setattr(tree_api, "_calculate_directory_stats", _fake_directory_stats)
    semaphore = asyncio.Semaphore(1)

    file_result = await tree_api._process_single_path(
        "lake",
        "main",
        SimpleNamespace(id=1),
        "weights/model.bin",
        {"weights/model.bin": SimpleNamespace(sha256="sha256-lfs", lfs=True)},
        semaphore,
        expand=True,
    )
    assert file_result == {
        "type": "file",
        "path": "weights/model.bin",
        "size": 32,
        "oid": "sha256-lfs",
        "lastModified": tree_api._format_last_modified(1713657600),
        "lfs": {
            "oid": "sha256-lfs",
            "size": 32,
            "pointerSize": 134,
        },
    }

    directory_result = await tree_api._process_single_path(
        "lake",
        "main",
        SimpleNamespace(id=1),
        "docs",
        {},
        semaphore,
        expand=True,
    )
    assert directory_result == {
        "type": "directory",
        "path": "docs",
        "oid": "tree-oid",
        "size": 15,
        "lastModified": tree_api._format_last_modified(1713657620),
    }

    assert (
        await tree_api._process_single_path(
            "lake",
            "main",
            SimpleNamespace(id=1),
            "ghost",
            {},
            semaphore,
            expand=False,
        )
        is None
    )
    assert (
        await tree_api._process_single_path(
            "lake",
            "main",
            SimpleNamespace(id=1),
            "broken-dir",
            {},
            semaphore,
            expand=False,
        )
        is None
    )
    assert await tree_api._process_single_path(
        "lake",
        "main",
        SimpleNamespace(id=1),
        "docs-error",
        {},
        semaphore,
        expand=True,
    ) == {
        "type": "directory",
        "path": "docs-error",
        "oid": "tree-oid-2",
        "size": 0,
        "lastModified": tree_api._format_last_modified(1713657615),
    }
    assert (
        await tree_api._process_single_path(
            "lake",
            "main",
            SimpleNamespace(id=1),
            "broken",
            {},
            semaphore,
            expand=False,
        )
        is None
    )


@pytest.mark.asyncio
async def test_list_repo_tree_covers_success_pagination_and_error_paths(monkeypatch):
    request = _request(
        "/api/models/owner/demo/tree/main/docs",
        query={"recursive": "false", "expand": "true", "limit": "200"},
    )
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")
    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)
    fetch_calls = []

    async def _fake_fetch(**kwargs):
        fetch_calls.append(kwargs)
        return {
            "results": [
                {
                    "path_type": "object",
                    "path": "docs/guide.md",
                    "size_bytes": 12,
                    "checksum": "lakefs-guide",
                    "mtime": 1713657600,
                },
                {
                    "path_type": "common_prefix",
                    "path": "docs/assets/",
                    "checksum": "tree-assets",
                    "mtime": 1713657605,
                },
            ],
            "pagination": {"has_more": True, "next_offset": "page-2"},
        }

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _fake_fetch)
    monkeypatch.setattr(
        tree_api,
        "_build_file_record_map",
        lambda repository, paths: {
            "docs/guide.md": SimpleNamespace(sha256="sha-db", lfs=False)
        },
    )
    async def _resolve_last_commits(lakefs_repo, revision, targets):
        return {
            "docs/guide.md": {"id": "commit-1", "title": "Update guide"},
            "docs/assets": {"id": "commit-2", "title": "Add assets"},
        }

    monkeypatch.setattr(tree_api, "resolve_last_commits_for_paths", _resolve_last_commits)
    monkeypatch.setattr(tree_api.cfg.app, "base_url", "https://hub.local")

    response = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        path="/docs/",
        expand=True,
        limit=200,
        cursor="page-1",
    )

    assert isinstance(response, JSONResponse)
    assert fetch_calls == [
        {
            "lakefs_repo": "lake-repo",
            "revision": "resolved-main",
            "prefix": "docs/",
            "recursive": False,
            "amount": tree_api.TREE_EXPAND_PAGE_SIZE,
            "after": "page-1",
        }
    ]
    assert response.headers["link"] == (
        '<https://hub.local/api/models/owner/demo/tree/main/docs?recursive=false&expand=true&limit=50&cursor=page-2>; rel="next"'
    )
    assert _json_body(response) == [
        {
            "type": "file",
            "oid": "sha-db",
            "size": 12,
            "path": "docs/guide.md",
            "lastModified": tree_api._format_last_modified(1713657600),
            "lastCommit": {"id": "commit-1", "title": "Update guide"},
            "securityFileStatus": None,
        },
        {
            "type": "directory",
            "oid": "tree-assets",
            "size": 0,
            "path": "docs/assets",
            "lastModified": tree_api._format_last_modified(1713657605),
            "lastCommit": {"id": "commit-2", "title": "Add assets"},
        },
    ]

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: None)
    monkeypatch.setattr(
        tree_api,
        "hf_repo_not_found",
        lambda repo_id, repo_type: {"missing": repo_id, "type": repo_type},
    )
    assert (
        await tree_api.list_repo_tree.__wrapped__(
            "model",
            "owner",
            "demo",
            request,
            limit=None,
        )
    ) == {"missing": "owner/demo", "type": "model"}

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    async def _raise_resolve_revision(client, lakefs_repo, revision):
        raise RuntimeError("bad revision")

    monkeypatch.setattr(tree_api, "resolve_revision", _raise_resolve_revision)
    monkeypatch.setattr(
        tree_api,
        "hf_revision_not_found",
        lambda repo_id, revision: {"revision": revision, "repo": repo_id},
    )
    assert (
        await tree_api.list_repo_tree.__wrapped__(
            "model",
            "owner",
            "demo",
            request,
            revision="bad-rev",
            limit=None,
        )
    ) == {"revision": "bad-rev", "repo": "owner/demo"}

    error = RuntimeError("missing path")

    async def _raise_missing(**kwargs):
        raise error

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)
    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _raise_missing)
    monkeypatch.setattr(tree_api, "is_lakefs_not_found_error", lambda exc: exc is error)
    monkeypatch.setattr(tree_api, "is_lakefs_revision_error", lambda exc: False)
    monkeypatch.setattr(
        tree_api,
        "hf_entry_not_found",
        lambda repo_id, path, revision: {"entry": path, "repo": repo_id, "revision": revision},
    )
    assert (
        await tree_api.list_repo_tree.__wrapped__(
            "model",
            "owner",
            "demo",
            request,
            path="/docs",
            limit=None,
        )
    ) == {"entry": "docs", "repo": "owner/demo", "revision": "main"}

    monkeypatch.setattr(tree_api, "is_lakefs_revision_error", lambda exc: True)
    assert (
        await tree_api.list_repo_tree.__wrapped__(
            "model",
            "owner",
            "demo",
            request,
            revision="bad-rev",
            limit=None,
        )
    ) == {"revision": "bad-rev", "repo": "owner/demo"}

    async def _empty_page(**kwargs):
        return {"results": [], "pagination": {"has_more": False}}

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _empty_page)
    assert (
        await tree_api.list_repo_tree.__wrapped__(
            "model",
            "owner",
            "demo",
            request,
            path="/docs",
            limit=None,
        )
    ) == {"entry": "docs", "repo": "owner/demo", "revision": "main"}

    generic_error = RuntimeError("server error")

    async def _raise_generic(**kwargs):
        raise generic_error

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _raise_generic)
    monkeypatch.setattr(tree_api, "is_lakefs_not_found_error", lambda exc: False)
    monkeypatch.setattr(tree_api, "hf_server_error", lambda message: {"error": message})
    assert "Failed to list objects" in (
        await tree_api.list_repo_tree.__wrapped__(
            "model",
            "owner",
            "demo",
            request,
            limit=None,
        )
    )["error"]


@pytest.mark.asyncio
async def test_list_repo_tree_handles_last_commit_lookup_failures(monkeypatch):
    request = _request("/api/models/owner/demo/tree/main")
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")
    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)

    async def _fetch_single_page(**kwargs):
        return {
            "results": [
                {
                    "path_type": "object",
                    "path": "README.md",
                    "size_bytes": 5,
                    "checksum": "sha-readme",
                }
            ],
            "pagination": {"has_more": False},
        }

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _fetch_single_page)
    monkeypatch.setattr(tree_api, "_build_file_record_map", lambda repository, paths: {})
    monkeypatch.setattr(tree_api, "should_use_lfs", lambda repository, path, size: False)

    revision_error = RuntimeError("bad commit history")
    async def _raise_revision_error(lakefs_repo, revision, targets):
        raise revision_error

    monkeypatch.setattr(tree_api, "resolve_last_commits_for_paths", _raise_revision_error)
    monkeypatch.setattr(tree_api, "is_lakefs_not_found_error", lambda exc: exc is revision_error)
    monkeypatch.setattr(tree_api, "is_lakefs_revision_error", lambda exc: True)
    monkeypatch.setattr(
        tree_api,
        "hf_revision_not_found",
        lambda repo_id, revision: {"revision": revision, "repo": repo_id},
    )
    assert (
        await tree_api.list_repo_tree.__wrapped__(
            "model",
            "owner",
            "demo",
            request,
            expand=True,
            limit=None,
        )
    ) == {"revision": "main", "repo": "owner/demo"}

    generic_error = RuntimeError("commit lookup failed")
    async def _raise_generic_commit_error(lakefs_repo, revision, targets):
        raise generic_error

    monkeypatch.setattr(
        tree_api,
        "resolve_last_commits_for_paths",
        _raise_generic_commit_error,
    )
    monkeypatch.setattr(tree_api, "is_lakefs_not_found_error", lambda exc: False)

    response = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        expand=True,
        limit=None,
    )
    assert _json_body(response) == [
        {
            "type": "file",
            "oid": "sha-readme",
            "size": 5,
            "path": "README.md",
            "lastCommit": None,
            "securityFileStatus": None,
        }
    ]


@pytest.mark.asyncio
async def test_get_paths_info_covers_limits_success_and_error_paths(monkeypatch):
    request = _request("/api/models/owner/demo/paths-info/main")
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: None)
    monkeypatch.setattr(
        tree_api,
        "hf_repo_not_found",
        lambda repo_id, repo_type: {"missing": repo_id},
    )
    assert (
        await tree_api.get_paths_info.__wrapped__(
            "model",
            "owner",
            "demo",
            "main",
            request,
            paths=["README.md"],
        )
    ) == {"missing": "owner/demo"}

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(
        tree_api,
        "hf_bad_request",
        lambda message: {"bad_request": message},
    )
    too_many_paths = ["file.txt"] * (tree_api.PATHS_INFO_MAX_PATHS + 1)
    assert "Maximum supported paths" in (
        await tree_api.get_paths_info.__wrapped__(
            "model",
            "owner",
            "demo",
            "main",
            request,
            paths=too_many_paths,
        )
    )["bad_request"]

    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")
    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)
    monkeypatch.setattr(
        tree_api,
        "_build_file_record_map",
        lambda repository, paths: {"README.md": SimpleNamespace(sha256="sha-readme", lfs=False)},
    )
    processed_paths = []

    async def _fake_process_path(**kwargs):
        processed_paths.append(kwargs["clean_path"])
        if kwargs["clean_path"] == "README.md":
            return {
                "type": "file",
                "path": "README.md",
                "size": 5,
                "oid": "sha-readme",
            }
        if kwargs["clean_path"] == "docs":
            return {
                "type": "directory",
                "path": "docs",
                "oid": "tree-docs",
                "size": 0,
            }
        return None

    monkeypatch.setattr(tree_api, "_process_single_path", _fake_process_path)

    async def _resolve_last_commits(lakefs_repo, revision, targets):
        return {
            "README.md": {"id": "commit-1", "title": "Update README"},
            "docs": {"id": "commit-2", "title": "Refresh docs"},
        }

    monkeypatch.setattr(tree_api, "resolve_last_commits_for_paths", _resolve_last_commits)

    results = await tree_api.get_paths_info.__wrapped__(
        "model",
        "owner",
        "demo",
        "main",
        request,
        paths=["/README.md/", "docs", "", None],
        expand=True,
    )
    assert processed_paths == ["README.md", "docs"]
    assert results == [
        {
            "type": "file",
            "path": "README.md",
            "size": 5,
            "oid": "sha-readme",
            "lastCommit": {"id": "commit-1", "title": "Update README"},
            "securityFileStatus": None,
        },
        {
            "type": "directory",
            "path": "docs",
            "oid": "tree-docs",
            "size": 0,
            "lastCommit": {"id": "commit-2", "title": "Refresh docs"},
        },
    ]

    async def _raise_resolve_revision(client, lakefs_repo, revision):
        raise RuntimeError("bad revision")

    monkeypatch.setattr(tree_api, "resolve_revision", _raise_resolve_revision)
    monkeypatch.setattr(
        tree_api,
        "hf_revision_not_found",
        lambda repo_id, revision: {"revision": revision, "repo": repo_id},
    )
    assert (
        await tree_api.get_paths_info.__wrapped__(
            "model",
            "owner",
            "demo",
            "bad-rev",
            request,
            paths=["README.md"],
        )
    ) == {"revision": "bad-rev", "repo": "owner/demo"}

    error = RuntimeError("missing revision")
    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)

    async def _raise_process_revision_error(**kwargs):
        raise error

    monkeypatch.setattr(tree_api, "_process_single_path", _raise_process_revision_error)
    monkeypatch.setattr(tree_api, "is_lakefs_not_found_error", lambda exc: exc is error)
    monkeypatch.setattr(tree_api, "is_lakefs_revision_error", lambda exc: True)
    assert (
        await tree_api.get_paths_info.__wrapped__(
            "model",
            "owner",
            "demo",
            "main",
            request,
            paths=["README.md"],
        )
    ) == {"revision": "main", "repo": "owner/demo"}

    monkeypatch.setattr(tree_api, "is_lakefs_revision_error", lambda exc: False)
    assert (
        await tree_api.get_paths_info.__wrapped__(
            "model",
            "owner",
            "demo",
            "main",
            request,
            paths=["README.md"],
        )
        == []
    )

    generic_error = RuntimeError("server error")
    async def _raise_process_generic_error(**kwargs):
        raise generic_error

    monkeypatch.setattr(tree_api, "_process_single_path", _raise_process_generic_error)
    monkeypatch.setattr(tree_api, "is_lakefs_not_found_error", lambda exc: False)
    monkeypatch.setattr(tree_api, "hf_server_error", lambda message: {"error": message})
    assert "Failed to fetch paths info" in (
        await tree_api.get_paths_info.__wrapped__(
            "model",
            "owner",
            "demo",
            "main",
            request,
            paths=["README.md"],
        )
    )["error"]


@pytest.mark.asyncio
async def test_get_paths_info_handles_last_commit_lookup_failures(monkeypatch):
    request = _request("/api/models/owner/demo/paths-info/main")
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")
    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)
    monkeypatch.setattr(tree_api, "_build_file_record_map", lambda repository, paths: {})
    async def _process_path(**kwargs):
        return {
            "type": "file",
            "path": kwargs["clean_path"],
            "size": 5,
            "oid": "sha-readme",
        }

    monkeypatch.setattr(tree_api, "_process_single_path", _process_path)

    revision_error = RuntimeError("bad commit history")
    async def _raise_revision_error(lakefs_repo, revision, targets):
        raise revision_error

    monkeypatch.setattr(tree_api, "resolve_last_commits_for_paths", _raise_revision_error)
    monkeypatch.setattr(tree_api, "is_lakefs_not_found_error", lambda exc: exc is revision_error)
    monkeypatch.setattr(tree_api, "is_lakefs_revision_error", lambda exc: True)
    monkeypatch.setattr(
        tree_api,
        "hf_revision_not_found",
        lambda repo_id, revision: {"revision": revision, "repo": repo_id},
    )
    assert (
        await tree_api.get_paths_info.__wrapped__(
            "model",
            "owner",
            "demo",
            "main",
            request,
            paths=["README.md"],
            expand=True,
        )
    ) == {"revision": "main", "repo": "owner/demo"}

    generic_error = RuntimeError("commit lookup failed")
    async def _raise_generic_commit_error(lakefs_repo, revision, targets):
        raise generic_error

    monkeypatch.setattr(
        tree_api,
        "resolve_last_commits_for_paths",
        _raise_generic_commit_error,
    )
    monkeypatch.setattr(tree_api, "is_lakefs_not_found_error", lambda exc: False)

    results = await tree_api.get_paths_info.__wrapped__(
        "model",
        "owner",
        "demo",
        "main",
        request,
        paths=["README.md"],
        expand=True,
    )
    assert results == [
        {
            "type": "file",
            "path": "README.md",
            "size": 5,
            "oid": "sha-readme",
            "lastCommit": None,
            "securityFileStatus": None,
        }
    ]


def test_normalize_name_prefix_treats_blank_as_omitted():
    # Issue #54 / §5.1: a whitespace-only `name_prefix` must be treated
    # as omitted so the response is byte-identical to the unfiltered
    # listing — anything else would silently change the LakeFS prefix
    # and the downstream cursor stack.
    assert tree_api._normalize_name_prefix(None) is None
    assert tree_api._normalize_name_prefix("") is None
    assert tree_api._normalize_name_prefix("   ") is None
    assert tree_api._normalize_name_prefix("conf") == "conf"
    assert tree_api._normalize_name_prefix("  conf  ") == "conf"


@pytest.mark.asyncio
async def test_list_repo_tree_name_prefix_pushes_lakefs_prefix(monkeypatch):
    request = _request("/api/models/owner/demo/tree/main/docs")
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")

    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)

    fetch_calls = []

    async def _fetch(**kwargs):
        fetch_calls.append(kwargs)
        return {
            "results": [
                {
                    "path_type": "object",
                    "path": "docs/config.json",
                    "size_bytes": 7,
                    "checksum": "sha-config",
                }
            ],
            "pagination": {"has_more": False},
        }

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _fetch)
    monkeypatch.setattr(tree_api, "_build_file_record_map", lambda repository, paths: {})
    monkeypatch.setattr(tree_api, "should_use_lfs", lambda repository, path, size: False)

    response = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        path="/docs/",
        name_prefix="conf",
        limit=None,
    )

    assert isinstance(response, JSONResponse)
    # The LakeFS-side prefix is `<base>/<typed>` — that's the whole
    # design (see issue #54 §"Approach"). The handler must not leak the
    # raw `prefix` arg under any other name.
    assert fetch_calls == [
        {
            "lakefs_repo": "lake-repo",
            "revision": "resolved-main",
            "prefix": "docs/conf",
            "recursive": False,
            "amount": tree_api.TREE_PAGE_SIZE,
            "after": None,
        }
    ]
    body = _json_body(response)
    assert [entry["path"] for entry in body] == ["docs/config.json"]


@pytest.mark.asyncio
async def test_list_repo_tree_name_prefix_root_path(monkeypatch):
    request = _request("/api/models/owner/demo/tree/main")
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")

    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)
    fetch_calls = []

    async def _fetch(**kwargs):
        fetch_calls.append(kwargs)
        return {"results": [], "pagination": {"has_more": False}}

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _fetch)
    monkeypatch.setattr(tree_api, "_build_file_record_map", lambda repository, paths: {})

    # Root path means base_prefix is "" — the LakeFS prefix is then the
    # raw user-typed prefix, with no leading "/".
    response = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        path="",
        name_prefix="READ",
        limit=None,
    )
    assert isinstance(response, JSONResponse)
    assert fetch_calls[0]["prefix"] == "READ"
    # Empty result at root with a prefix is 200 + [], not 404.
    assert _json_body(response) == []


@pytest.mark.asyncio
async def test_list_repo_tree_name_prefix_validation(monkeypatch):
    request = _request("/api/models/owner/demo/tree/main")
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")

    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)

    # If validation passes the prefix gate, the handler will try to
    # call fetch — wire a sentinel so an unwanted call is loud.
    async def _explode(**kwargs):
        raise AssertionError("LakeFS must not be called when validation fails")

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _explode)
    monkeypatch.setattr(
        tree_api,
        "hf_bad_request",
        lambda message: {"bad_request": message},
    )

    # `/` would silently turn the basename filter into a multi-segment
    # navigation — that's a UX trap, so the wire form must reject it.
    slash_result = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        name_prefix="docs/conf",
        limit=None,
    )
    assert slash_result == {"bad_request": "name_prefix must not contain '/'"}

    too_long = "a" * (tree_api.NAME_PREFIX_MAX_LENGTH + 1)
    long_result = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        name_prefix=too_long,
        limit=None,
    )
    assert "too long" in long_result["bad_request"]


@pytest.mark.asyncio
async def test_list_repo_tree_blank_name_prefix_is_byte_identical(monkeypatch):
    """§5.1 invariant — a whitespace-only `name_prefix` must produce the
    same LakeFS call as omitting the parameter entirely. Without this,
    HF clients that round-trip the query string (or proxies that
    normalize empty strings) could accidentally narrow the listing."""
    request = _request("/api/models/owner/demo/tree/main/docs")
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")

    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)
    fetch_calls = []

    async def _fetch(**kwargs):
        fetch_calls.append(kwargs)
        return {
            "results": [
                {
                    "path_type": "object",
                    "path": "docs/intro.md",
                    "size_bytes": 4,
                    "checksum": "sha-intro",
                }
            ],
            "pagination": {"has_more": False},
        }

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _fetch)
    monkeypatch.setattr(tree_api, "_build_file_record_map", lambda repository, paths: {})
    monkeypatch.setattr(tree_api, "should_use_lfs", lambda repository, path, size: False)

    await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        path="/docs",
        name_prefix="   ",
        limit=None,
    )
    assert fetch_calls[0]["prefix"] == "docs/"


@pytest.mark.asyncio
async def test_list_repo_tree_empty_with_name_prefix_returns_200(monkeypatch):
    """A valid directory whose name_prefix matched nothing should
    return 200 + [] (not 404). Without this, a one-character typo in
    the search box would render the whole UI as "directory not found"
    even though the directory clearly exists."""
    request = _request("/api/models/owner/demo/tree/main/docs")
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")

    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)

    async def _empty_fetch(**kwargs):
        return {"results": [], "pagination": {"has_more": False}}

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _empty_fetch)
    monkeypatch.setattr(tree_api, "_build_file_record_map", lambda repository, paths: {})

    # Sentinel — if the handler took the 404 path, this would clobber
    # the JSONResponse with our marker dict. It must NOT be called.
    monkeypatch.setattr(
        tree_api,
        "hf_entry_not_found",
        lambda repo_id, path, revision: {"unexpected_404": True},
    )

    response = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        path="/docs",
        name_prefix="zzz-no-such-thing",
        limit=None,
    )
    assert isinstance(response, JSONResponse)
    assert _json_body(response) == []
    assert "link" not in {key.lower() for key in response.headers.keys()}


@pytest.mark.asyncio
async def test_list_repo_tree_empty_path_with_cursor_no_404(monkeypatch):
    """Paginating into a non-empty directory and landing on an empty
    final page must not 404 — the original "entry not found" guard
    only fires on the *first* request to a path that doesn't exist."""
    request = _request("/api/models/owner/demo/tree/main/docs")
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")

    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)

    async def _empty_fetch(**kwargs):
        return {"results": [], "pagination": {"has_more": False}}

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _empty_fetch)
    monkeypatch.setattr(tree_api, "_build_file_record_map", lambda repository, paths: {})
    monkeypatch.setattr(
        tree_api,
        "hf_entry_not_found",
        lambda repo_id, path, revision: {"unexpected_404": True},
    )

    response = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        path="/docs",
        cursor="cursor-from-previous-page",
        limit=None,
    )
    assert isinstance(response, JSONResponse)
    assert _json_body(response) == []


@pytest.mark.asyncio
async def test_list_repo_tree_link_header_preserves_name_prefix(monkeypatch):
    """The Link: rel="next" cursor URL must keep `name_prefix` so
    follow-up pages see the same filter — `_build_public_link` already
    forwards `request.query_params`, but pin the behavior so a future
    refactor doesn't accidentally drop it."""
    request = _request(
        "/api/models/owner/demo/tree/main/docs",
        query={"name_prefix": "conf", "recursive": "false"},
    )
    repo = SimpleNamespace(full_id="owner/demo", private=False)

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")

    async def _resolve_revision(client, lakefs_repo, revision):
        return ("resolved-main", "branch")

    monkeypatch.setattr(tree_api, "resolve_revision", _resolve_revision)

    async def _fetch(**kwargs):
        return {
            "results": [
                {
                    "path_type": "object",
                    "path": "docs/config.json",
                    "size_bytes": 12,
                    "checksum": "sha-config",
                }
            ],
            "pagination": {"has_more": True, "next_offset": "page-2"},
        }

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects_page", _fetch)
    monkeypatch.setattr(tree_api, "_build_file_record_map", lambda repository, paths: {})
    monkeypatch.setattr(tree_api, "should_use_lfs", lambda repository, path, size: False)
    monkeypatch.setattr(tree_api.cfg.app, "base_url", "https://hub.local")

    response = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        path="/docs",
        name_prefix="conf",
        limit=None,
    )
    link_header = response.headers["link"]
    assert "name_prefix=conf" in link_header
    assert "cursor=page-2" in link_header
