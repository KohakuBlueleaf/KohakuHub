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


class _FakeLakeFSRestClient:
    def __init__(self, *, log_responses=None, diff_responses=None):
        self.log_responses = list(log_responses or [])
        self.diff_responses = list(diff_responses or [])
        self.log_calls = []
        self.diff_calls = []

    async def log_commits(self, **kwargs):
        self.log_calls.append(kwargs)
        return self.log_responses.pop(0)

    async def diff_refs(self, **kwargs):
        self.diff_calls.append(kwargs)
        return self.diff_responses.pop(0)


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


def test_make_tree_item_and_apply_changed_path_cover_file_directory_and_ancestors(monkeypatch):
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

    unresolved_files = {"docs/guide.md"}
    unresolved_directories = {"docs", "weights"}
    resolved = {}
    commit_info = {"id": "commit-3", "title": "Update nested paths"}

    tree_api._apply_changed_path(
        "docs/guide.md",
        unresolved_files,
        unresolved_directories,
        resolved,
        commit_info,
    )

    assert unresolved_files == set()
    assert unresolved_directories == {"weights"}
    assert resolved == {
        "docs/guide.md": commit_info,
        "docs": commit_info,
    }

    direct_directory_targets = {"weights"}
    tree_api._apply_changed_path(
        "/",
        set(),
        direct_directory_targets,
        resolved,
        commit_info,
    )
    assert direct_directory_targets == {"weights"}

    tree_api._apply_changed_path(
        "weights",
        set(),
        direct_directory_targets,
        resolved,
        commit_info,
    )
    assert direct_directory_targets == set()
    assert resolved["weights"] == commit_info


@pytest.mark.asyncio
async def test_resolve_last_commits_for_paths_covers_diff_pagination_and_root_commit(monkeypatch):
    rest_client = _FakeLakeFSRestClient(
        log_responses=[
            {
                "results": [
                    {
                        "id": "commit-2",
                        "message": "Refresh tree rows",
                        "creation_date": 1713657600,
                        "parents": ["commit-1"],
                    }
                ],
                "pagination": {"has_more": False},
            }
        ],
        diff_responses=[
            {
                "results": [{"path": "docs/guide.md"}],
                "pagination": {"has_more": True, "next_offset": "diff-2"},
            },
            {
                "results": [{"path": "weights/model.bin"}],
                "pagination": {"has_more": False},
            },
        ],
    )
    monkeypatch.setattr(tree_api, "get_lakefs_rest_client", lambda: rest_client)

    resolved = await tree_api.resolve_last_commits_for_paths(
        "lake",
        "main",
        [
            {"path": "docs", "type": "directory"},
            {"path": "weights/model.bin", "type": "file"},
        ],
    )

    assert resolved["docs"]["id"] == "commit-2"
    assert resolved["weights/model.bin"]["title"] == "Refresh tree rows"
    assert rest_client.log_calls == [
        {
            "repository": "lake",
            "ref": "main",
            "after": None,
            "amount": tree_api.TREE_COMMIT_SCAN_PAGE_SIZE,
        }
    ]
    assert rest_client.diff_calls == [
        {
            "repository": "lake",
            "left_ref": "commit-1",
            "right_ref": "commit-2",
            "after": None,
            "amount": tree_api.TREE_DIFF_PAGE_SIZE,
        },
        {
            "repository": "lake",
            "left_ref": "commit-1",
            "right_ref": "commit-2",
            "after": "diff-2",
            "amount": tree_api.TREE_DIFF_PAGE_SIZE,
        },
    ]

    root_client = _FakeLakeFSRestClient(
        log_responses=[
            {
                "results": [
                    {
                        "id": "root-commit",
                        "message": "Initial import",
                        "creation_date": "2026-04-21T00:00:00.000000Z",
                        "parents": [],
                    }
                ],
                "pagination": {"has_more": False},
            }
        ]
    )
    monkeypatch.setattr(tree_api, "get_lakefs_rest_client", lambda: root_client)

    root_resolved = await tree_api.resolve_last_commits_for_paths(
        "lake",
        "main",
        [
            {"path": "README.md", "type": "file"},
            {"path": "docs", "type": "directory"},
        ],
    )
    assert root_resolved == {
        "README.md": {
            "id": "root-commit",
            "title": "Initial import",
            "date": "2026-04-21T00:00:00.000000Z",
        },
        "docs": {
            "id": "root-commit",
            "title": "Initial import",
            "date": "2026-04-21T00:00:00.000000Z",
        },
    }
    assert await tree_api.resolve_last_commits_for_paths("lake", "main", []) == {}

    empty_client = _FakeLakeFSRestClient(
        log_responses=[{"results": [], "pagination": {"has_more": False}}]
    )
    monkeypatch.setattr(tree_api, "get_lakefs_rest_client", lambda: empty_client)
    assert await tree_api.resolve_last_commits_for_paths(
        "lake",
        "main",
        [{"path": "missing.txt", "type": "file"}],
    ) == {}

    paginated_client = _FakeLakeFSRestClient(
        log_responses=[
            {
                "results": [
                    {
                        "id": "commit-2",
                        "message": "Unrelated change",
                        "creation_date": 1713657600,
                        "parents": ["commit-1"],
                    }
                ],
                "pagination": {"has_more": True, "next_offset": "page-2"},
            },
            {
                "results": [
                    {
                        "id": "root-commit",
                        "message": "Initial import",
                        "creation_date": 1713657610,
                        "parents": [],
                    }
                ],
                "pagination": {"has_more": False},
            },
        ],
        diff_responses=[
            {
                "results": [{"path": "docs/other.md"}],
                "pagination": {"has_more": False},
            }
        ],
    )
    monkeypatch.setattr(tree_api, "get_lakefs_rest_client", lambda: paginated_client)

    paginated_result = await tree_api.resolve_last_commits_for_paths(
        "lake",
        "main",
        [{"path": "README.md", "type": "file"}],
    )
    assert paginated_result["README.md"]["id"] == "root-commit"
    assert paginated_client.log_calls[1]["after"] == "page-2"


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
