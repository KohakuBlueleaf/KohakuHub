"""Unit tests for repository tree routes."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

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


@pytest.mark.asyncio
async def test_fetch_lakefs_objects_and_calculate_folder_stats_cover_pagination(monkeypatch):
    fetch_client = _FakeLakeFSClient(
        list_responses=[
            {
                "results": [{"path": "a.txt", "path_type": "object"}],
                "pagination": {"has_more": True, "next_offset": "page-2"},
            },
            {
                "results": [{"path": "b.txt", "path_type": "object"}],
                "pagination": {"has_more": False},
            },
        ]
    )
    monkeypatch.setattr(tree_api, "get_lakefs_client", lambda: fetch_client)

    results = await tree_api.fetch_lakefs_objects("lake", "main", "docs/", recursive=False)
    assert [item["path"] for item in results] == ["a.txt", "b.txt"]
    assert fetch_client.list_calls[0]["delimiter"] == "/"
    assert fetch_client.list_calls[1]["after"] == "page-2"

    folder_client = _FakeLakeFSClient(
        list_responses=[
            {
                "results": [
                    {"path_type": "object", "size_bytes": 4, "mtime": 10},
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
    monkeypatch.setattr(tree_api, "get_lakefs_client", lambda: folder_client)

    folder_size, latest_mtime = await tree_api.calculate_folder_stats("lake", "main", "docs/")
    assert folder_size == 10
    assert latest_mtime == 20

    failing_folder_client = _FakeLakeFSClient(list_responses=[RuntimeError("folder failed")])
    monkeypatch.setattr(tree_api, "get_lakefs_client", lambda: failing_folder_client)
    failed_size, failed_mtime = await tree_api.calculate_folder_stats("lake", "main", "docs/")
    assert failed_size == 0
    assert failed_mtime is None


@pytest.mark.asyncio
async def test_convert_file_object_adds_lfs_and_last_modified_metadata(monkeypatch):
    monkeypatch.setattr(tree_api, "should_use_lfs", lambda repository, path, size: True)
    monkeypatch.setattr(
        tree_api,
        "get_file",
        lambda repository, path: SimpleNamespace(sha256="sha256-lfs"),
    )

    result = await tree_api.convert_file_object(
        {
            "path": "weights.bin",
            "size_bytes": 32,
            "checksum": "lakefs-sha",
            "mtime": 123,
        },
        SimpleNamespace(full_id="owner/demo"),
    )

    assert result["lfs"] == {"oid": "sha256-lfs", "size": 32, "pointerSize": 134}
    assert "lastModified" in result


@pytest.mark.asyncio
async def test_convert_directory_object_uses_object_mtime_when_folder_stats_have_no_latest_time(
    monkeypatch,
):
    async def _fake_calculate_folder_stats(*args):
        return (12, None)

    monkeypatch.setattr(tree_api, "calculate_folder_stats", _fake_calculate_folder_stats)

    result = await tree_api.convert_directory_object(
        {"path": "docs/", "checksum": "tree-sha", "mtime": 123},
        "lake",
        "main",
    )

    assert result["path"] == "docs"
    assert result["size"] == 12
    assert "lastModified" in result


@pytest.mark.asyncio
async def test_list_repo_tree_covers_missing_repo_success_and_error_paths(monkeypatch):
    request = SimpleNamespace()
    monkeypatch.setattr(tree_api, "get_repository", lambda *args: None)
    monkeypatch.setattr(
        tree_api,
        "hf_repo_not_found",
        lambda repo_id, repo_type: {"missing": repo_id, "type": str(repo_type)},
    )

    missing = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
    )
    assert missing["missing"] == "owner/demo"

    repo = SimpleNamespace(full_id="owner/demo", private=False)
    captured = {}

    async def _fake_fetch(lakefs_repo, revision, prefix, recursive):
        captured.update(
            {
                "lakefs_repo": lakefs_repo,
                "revision": revision,
                "prefix": prefix,
                "recursive": recursive,
            }
        )
        return [
            {"path_type": "object", "path": "folder/file.txt"},
            {"path_type": "common_prefix", "path": "folder/"},
        ]

    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")
    monkeypatch.setattr(tree_api, "fetch_lakefs_objects", _fake_fetch)

    async def _fake_convert_file_object(obj, repository):
        return {"type": "file", "path": obj["path"]}

    async def _fake_convert_directory_object(obj, lakefs_repo, revision):
        return {"type": "directory", "path": obj["path"]}

    monkeypatch.setattr(tree_api, "convert_file_object", _fake_convert_file_object)
    monkeypatch.setattr(tree_api, "convert_directory_object", _fake_convert_directory_object)

    success = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        path="folder",
    )
    assert captured["prefix"] == "folder/"
    assert success == [
        {"type": "file", "path": "folder/file.txt"},
        {"type": "directory", "path": "folder/"},
    ]

    error = RuntimeError("missing")
    async def _raise_missing(*args, **kwargs):
        raise error

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects", _raise_missing)
    monkeypatch.setattr(tree_api, "is_lakefs_not_found_error", lambda exc: exc is error)
    monkeypatch.setattr(tree_api, "is_lakefs_revision_error", lambda exc: True)
    monkeypatch.setattr(
        tree_api,
        "hf_revision_not_found",
        lambda repo_id, revision: {"revision": revision, "repo": repo_id},
    )
    revision_missing = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
        revision="bad-rev",
    )
    assert revision_missing == {"revision": "bad-rev", "repo": "owner/demo"}

    monkeypatch.setattr(tree_api, "is_lakefs_revision_error", lambda exc: False)
    assert (
        await tree_api.list_repo_tree.__wrapped__(
            "model",
            "owner",
            "demo",
            request,
            revision="missing-path",
        )
        == []
    )

    generic_error = RuntimeError("server")
    async def _raise_generic(*args, **kwargs):
        raise generic_error

    monkeypatch.setattr(tree_api, "fetch_lakefs_objects", _raise_generic)
    monkeypatch.setattr(tree_api, "is_lakefs_not_found_error", lambda exc: False)
    monkeypatch.setattr(tree_api, "hf_server_error", lambda message: {"error": message})
    server_error = await tree_api.list_repo_tree.__wrapped__(
        "model",
        "owner",
        "demo",
        request,
    )
    assert "Failed to list objects" in server_error["error"]


@pytest.mark.asyncio
async def test_get_paths_info_covers_missing_repo_lfs_directory_and_missing_paths(monkeypatch):
    request = SimpleNamespace()
    monkeypatch.setattr(tree_api, "get_repository", lambda *args: None)
    monkeypatch.setattr(
        tree_api,
        "hf_repo_not_found",
        lambda repo_id, repo_type: {"missing": repo_id},
    )

    missing = await tree_api.get_paths_info.__wrapped__(
        "model",
        "owner",
        "demo",
        "main",
        request,
        paths=["README.md"],
    )
    assert missing == {"missing": "owner/demo"}

    repo = SimpleNamespace(full_id="owner/demo", private=False)

    class _NotFoundError(Exception):
        pass

    client = _FakeLakeFSClient(
        stat_map={
            "weights.bin": {"size_bytes": 32, "checksum": "lakefs-sha"},
            "folder": _NotFoundError("folder"),
            "ghost": _NotFoundError("ghost"),
            "broken": _NotFoundError("broken"),
            "server-error": RuntimeError("server error"),
        },
        list_map={
            "folder/": {"results": [{"checksum": "tree-oid"}]},
            "ghost/": {"results": []},
            "broken/": RuntimeError("broken directory"),
        },
    )
    monkeypatch.setattr(tree_api, "get_repository", lambda *args: repo)
    monkeypatch.setattr(tree_api, "check_repo_read_permission", lambda repo_arg, user: True)
    monkeypatch.setattr(tree_api, "lakefs_repo_name", lambda repo_type, repo_id: "lake-repo")
    monkeypatch.setattr(tree_api, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(
        tree_api,
        "should_use_lfs",
        lambda repo_arg, path, size: path == "weights.bin",
    )
    monkeypatch.setattr(
        tree_api,
        "get_file",
        lambda repo_arg, path: (
            SimpleNamespace(sha256="sha256-lfs") if path == "weights.bin" else None
        ),
    )
    monkeypatch.setattr(
        tree_api,
        "is_lakefs_not_found_error",
        lambda exc: isinstance(exc, _NotFoundError),
    )

    results = await tree_api.get_paths_info.__wrapped__(
        "model",
        "owner",
        "demo",
        "main",
        request,
        paths=["weights.bin", "folder", "ghost", "broken", "server-error"],
    )

    assert results == [
        {
            "type": "file",
            "path": "weights.bin",
            "size": 32,
            "oid": "sha256-lfs",
            "lfs": {"oid": "sha256-lfs", "size": 32, "pointerSize": 134},
            "last_commit": None,
            "security": None,
        },
        {
            "type": "directory",
            "path": "folder",
            "oid": "tree-oid",
            "tree_id": "tree-oid",
            "last_commit": None,
        },
    ]
