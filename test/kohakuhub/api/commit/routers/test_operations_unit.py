"""Unit tests for commit operation helpers and router flow."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.commit.routers.operations as commit_ops


class _Expr:
    def __init__(self, value):
        self.value = value

    def __and__(self, other):
        return _Expr(("and", self.value, getattr(other, "value", other)))

    def __repr__(self):
        return repr(self.value)


class _Field:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return _Expr((self.name, "==", other))

    def startswith(self, other):
        return _Expr((self.name, "startswith", other))

    def __hash__(self):
        return hash(self.name)


class _Query:
    def __init__(self, execute_result=1):
        self.execute_result = execute_result
        self.where_calls = []
        self.on_conflict_calls = []

    def where(self, *args):
        self.where_calls.append(args)
        return self

    def on_conflict(self, **kwargs):
        self.on_conflict_calls.append(kwargs)
        return self

    def execute(self):
        return self.execute_result


class _FakeFileModel:
    repository = _Field("repository")
    path_in_repo = _Field("path_in_repo")
    sha256 = _Field("sha256")
    size = _Field("size")
    lfs = _Field("lfs")
    is_deleted = _Field("is_deleted")
    updated_at = _Field("updated_at")
    owner = _Field("owner")
    id = _Field("id")

    insert_calls = []
    update_query = _Query()
    get_or_none_result = None

    @classmethod
    def reset(cls):
        cls.insert_calls = []
        cls.update_query = _Query()
        cls.get_or_none_result = None

    @classmethod
    def insert(cls, **kwargs):
        cls.insert_calls.append(kwargs)
        return _Query()

    @classmethod
    def update(cls, **kwargs):
        cls.update_kwargs = kwargs
        return cls.update_query

    @classmethod
    def get_or_none(cls, *args):
        return cls.get_or_none_result


class _FakeRequest:
    def __init__(self, body: bytes, query_params: dict | None = None):
        self._body = body
        # Real ``starlette.Request.query_params`` is a QueryParams object,
        # but everything the commit handler does with it goes through
        # ``.get(...)`` — a plain dict suffices for unit tests.
        self.query_params = query_params or {}

    async def body(self):
        return self._body


class _FakeLakeFSClient:
    def __init__(self):
        self.calls = []
        self.branch_data = {"commit_id": "head-commit"}
        self.commit_data = {"id": "commit-created"}
        self.raise_on = {}
        self.list_payload = {"results": []}

    def _maybe_raise(self, name):
        error = self.raise_on.get(name)
        if error:
            raise error

    async def upload_object(self, **kwargs):
        self.calls.append(("upload_object", kwargs))
        self._maybe_raise("upload_object")
        return {"ok": True}

    async def link_physical_address(self, **kwargs):
        self.calls.append(("link_physical_address", kwargs))
        self._maybe_raise("link_physical_address")
        return {"ok": True}

    async def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))
        self._maybe_raise("delete_object")
        return {"ok": True}

    async def list_objects(self, **kwargs):
        self.calls.append(("list_objects", kwargs))
        self._maybe_raise("list_objects")
        return self.list_payload

    async def stat_object(self, **kwargs):
        self.calls.append(("stat_object", kwargs))
        self._maybe_raise("stat_object")
        return {
            "physical_address": "s3://bucket/shared/path",
            "checksum": "sha256:abc",
            "size_bytes": 12,
        }

    async def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        self._maybe_raise("get_object")
        return b"copied-content"

    async def get_branch(self, **kwargs):
        self.calls.append(("get_branch", kwargs))
        self._maybe_raise("get_branch")
        return self.branch_data

    async def commit(self, **kwargs):
        self.calls.append(("commit", kwargs))
        self._maybe_raise("commit")
        return self.commit_data

    async def get_commit(self, **kwargs):
        self.calls.append(("get_commit", kwargs))
        self._maybe_raise("get_commit")
        return {"id": kwargs["commit_id"]}


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner()


@pytest.fixture(autouse=True)
def _reset_fake_file():
    _FakeFileModel.reset()


def test_calculate_git_blob_sha1_matches_git_blob_format():
    content = b"hello world"

    digest = commit_ops.calculate_git_blob_sha1(content)

    assert digest == "95d09f2b10159347eece71399a7e2e907ea3df4f"


@pytest.mark.asyncio
async def test_process_regular_file_covers_validation_skip_restore_and_success(monkeypatch):
    repo = SimpleNamespace(owner=SimpleNamespace(username="owner"))
    client = _FakeLakeFSClient()
    monkeypatch.setattr(commit_ops, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(commit_ops, "get_effective_lfs_threshold", lambda repo_arg: 10)
    monkeypatch.setattr(commit_ops, "should_use_lfs", lambda repo_arg, path, size: size >= 10)
    monkeypatch.setattr(commit_ops, "get_file", lambda repo_arg, path: None)
    monkeypatch.setattr(commit_ops, "File", _FakeFileModel)

    with pytest.raises(HTTPException) as invalid_encoding:
        await commit_ops.process_regular_file("README.md", "aGVsbG8=", "utf8", repo, "lakefs", "main")
    assert invalid_encoding.value.status_code == 400

    monkeypatch.setattr(
        commit_ops.base64,
        "b64decode",
        lambda value: (_ for _ in ()).throw(ValueError("bad base64")),
    )
    with pytest.raises(HTTPException) as bad_base64:
        await commit_ops.process_regular_file("README.md", "!!", "base64", repo, "lakefs", "main")
    assert bad_base64.value.status_code == 400
    monkeypatch.undo()
    monkeypatch.setattr(commit_ops, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(commit_ops, "get_effective_lfs_threshold", lambda repo_arg: 10)
    monkeypatch.setattr(commit_ops, "should_use_lfs", lambda repo_arg, path, size: size >= 10)
    monkeypatch.setattr(commit_ops, "get_file", lambda repo_arg, path: None)
    monkeypatch.setattr(commit_ops, "File", _FakeFileModel)

    large_content = base64.b64encode(b"0123456789").decode("ascii")
    with pytest.raises(HTTPException) as lfs_required:
        await commit_ops.process_regular_file("README.md", large_content, "base64", repo, "lakefs", "main")
    assert lfs_required.value.status_code == 400
    assert lfs_required.value.detail["suggested_operation"] == "lfsFile"

    monkeypatch.setattr(commit_ops, "should_use_lfs", lambda repo_arg, path, size: False)
    monkeypatch.setattr(
        commit_ops,
        "get_file",
        lambda repo_arg, path: SimpleNamespace(
            sha256=commit_ops.calculate_git_blob_sha1(b"hello"),
            size=5,
            is_deleted=False,
        ),
    )
    skipped = await commit_ops.process_regular_file(
        "README.md",
        base64.b64encode(b"hello").decode("ascii"),
        "base64",
        repo,
        "lakefs",
        "main",
    )
    assert skipped is False

    monkeypatch.setattr(
        commit_ops,
        "get_file",
        lambda repo_arg, path: SimpleNamespace(
            sha256="old",
            size=1,
            is_deleted=True,
        ),
    )
    changed = await commit_ops.process_regular_file(
        "README.md",
        base64.b64encode(b"hello").decode("ascii"),
        "base64",
        repo,
        "lakefs",
        "main",
    )
    assert changed is True
    assert client.calls[-1][0] == "upload_object"
    assert _FakeFileModel.insert_calls

    client.raise_on["upload_object"] = RuntimeError("upload failed")
    with pytest.raises(HTTPException) as upload_error:
        await commit_ops.process_regular_file(
            "README.md",
            base64.b64encode(b"hello").decode("ascii"),
            "base64",
            repo,
            "lakefs",
            "main",
        )
    assert upload_error.value.status_code == 500


@pytest.mark.asyncio
async def test_process_lfs_file_covers_same_content_new_content_and_failures(monkeypatch):
    repo = SimpleNamespace(owner=SimpleNamespace(username="owner"))
    client = _FakeLakeFSClient()
    monkeypatch.setattr(commit_ops, "File", _FakeFileModel)
    monkeypatch.setattr(commit_ops, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(commit_ops.cfg.s3, "bucket", "hub-storage")

    with pytest.raises(HTTPException) as missing_oid:
        await commit_ops.process_lfs_file("weights.bin", None, 10, "sha256", repo, "lakefs", "main")
    assert missing_oid.value.status_code == 400

    _FakeFileModel.get_or_none_result = SimpleNamespace(
        id=3,
        repository=repo,
        path_in_repo="weights.bin",
        sha256="sameoid",
        size=10,
        lfs=True,
        is_deleted=True,
    )
    restored = await commit_ops.process_lfs_file(
        "weights.bin", "sameoid", 10, "sha256", repo, "lakefs", "main"
    )
    assert restored[0] is True
    assert restored[1]["sha256"] == "sameoid"
    assert _FakeFileModel.update_query.where_calls

    _FakeFileModel.get_or_none_result = SimpleNamespace(
        id=4,
        repository=repo,
        path_in_repo="weights.bin",
        sha256="sameoid",
        size=10,
        lfs=True,
        is_deleted=False,
    )
    unchanged = await commit_ops.process_lfs_file(
        "weights.bin", "sameoid", 10, "sha256", repo, "lakefs", "main"
    )
    assert unchanged == (
        False,
        {"path": "weights.bin", "sha256": "sameoid", "size": 10, "old_sha256": None},
    )

    _FakeFileModel.get_or_none_result = SimpleNamespace(
        repository=repo,
        path_in_repo="weights.bin",
        sha256="oldoid",
        size=8,
        lfs=True,
        is_deleted=False,
    )
    monkeypatch.setattr(commit_ops, "object_exists", lambda bucket, key: _async_return(False))
    with pytest.raises(HTTPException) as missing_object:
        await commit_ops.process_lfs_file("weights.bin", "newoid", 11, "sha256", repo, "lakefs", "main")
    assert missing_object.value.status_code == 400

    monkeypatch.setattr(commit_ops, "object_exists", lambda bucket, key: _async_return(True))
    monkeypatch.setattr(commit_ops, "get_object_metadata", lambda bucket, key: _async_return({"size": 12}))
    changed, tracking = await commit_ops.process_lfs_file(
        "weights.bin", "newoid", 11, "sha256", repo, "lakefs", "main"
    )
    assert changed is True
    assert tracking["old_sha256"] == "oldoid"
    assert _FakeFileModel.insert_calls

    monkeypatch.setattr(commit_ops, "get_object_metadata", lambda bucket, key: (_ for _ in ()).throw(RuntimeError("meta fail")))
    client.raise_on["link_physical_address"] = RuntimeError("link failed")
    with pytest.raises(HTTPException) as link_error:
        await commit_ops.process_lfs_file("weights.bin", "newoid", 11, "sha256", repo, "lakefs", "main")
    assert link_error.value.status_code == 500


@pytest.mark.asyncio
async def test_process_deleted_file_and_folder_cover_success_partial_failures_and_exceptions(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    client = _FakeLakeFSClient()
    monkeypatch.setattr(commit_ops, "File", _FakeFileModel)
    monkeypatch.setattr(commit_ops, "get_lakefs_client", lambda: client)

    deleted = await commit_ops.process_deleted_file("README.md", repo, "lakefs", "main")
    assert deleted is True
    assert _FakeFileModel.update_query.where_calls

    client.raise_on["delete_object"] = RuntimeError("delete failed")
    deleted = await commit_ops.process_deleted_file("README.md", repo, "lakefs", "main")
    assert deleted is True
    client.raise_on.pop("delete_object", None)

    client.list_payload = {
        "results": [
            {"path_type": "object", "path": "folder/a.txt"},
            {"path_type": "object", "path": "folder/b.txt"},
            {"path_type": "common_prefix", "path": "folder/sub/"},
        ]
    }
    folder_deleted = await commit_ops.process_deleted_folder("folder", repo, "lakefs", "main")
    assert folder_deleted is True

    client.raise_on["list_objects"] = RuntimeError("list failed")
    folder_deleted = await commit_ops.process_deleted_folder("folder", repo, "lakefs", "main")
    assert folder_deleted is True


@pytest.mark.asyncio
async def test_process_copy_file_covers_validation_success_and_error(monkeypatch):
    repo = SimpleNamespace(owner=SimpleNamespace(username="owner"))
    client = _FakeLakeFSClient()
    monkeypatch.setattr(commit_ops, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(commit_ops, "get_file", lambda repo_arg, path: SimpleNamespace(size=12, sha256="abc", lfs=True))
    monkeypatch.setattr(commit_ops, "should_use_lfs", lambda repo_arg, path, size: True)
    monkeypatch.setattr(commit_ops, "File", _FakeFileModel)

    with pytest.raises(HTTPException) as missing_src:
        await commit_ops.process_copy_file("dest.txt", None, "main", repo, "lakefs", "main")
    assert missing_src.value.status_code == 400

    copied = await commit_ops.process_copy_file("dest.txt", "src.txt", "main", repo, "lakefs", "main")
    assert copied is True
    assert _FakeFileModel.insert_calls[-1]["lfs"] is True

    monkeypatch.setattr(commit_ops, "get_file", lambda repo_arg, path: None)
    _FakeFileModel.insert_calls.clear()
    copied = await commit_ops.process_copy_file("dest.txt", "src.txt", "main", repo, "lakefs", "main")
    assert copied is True
    assert _FakeFileModel.insert_calls[-1]["sha256"] == "sha256:abc"

    client.raise_on["stat_object"] = RuntimeError("copy failed")
    with pytest.raises(HTTPException) as copy_error:
        await commit_ops.process_copy_file("dest.txt", "src.txt", "main", repo, "lakefs", "main")
    assert copy_error.value.status_code == 500


@pytest.mark.asyncio
async def test_commit_route_covers_parse_dispatch_noop_and_success_paths(monkeypatch):
    user = SimpleNamespace(username="owner")
    repo = SimpleNamespace(owner=SimpleNamespace(username="owner"), used_bytes=0)
    client = _FakeLakeFSClient()
    warnings = []
    tracked = []
    gc_calls = []

    monkeypatch.setattr(commit_ops.Repository, "get_or_none", lambda *args: repo)
    monkeypatch.setattr(commit_ops, "check_repo_write_permission", lambda repo_arg, user_arg: None)
    monkeypatch.setattr(commit_ops, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(commit_ops, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(commit_ops.cfg.app, "base_url", "https://hub.example.com")
    monkeypatch.setattr(commit_ops.cfg.app, "debug_log_payloads", False)
    monkeypatch.setattr(commit_ops.cfg.app, "lfs_auto_gc", True)
    monkeypatch.setattr(commit_ops, "process_regular_file", lambda **kwargs: _async_return(False))
    monkeypatch.setattr(commit_ops, "process_lfs_file", lambda **kwargs: _async_return((False, None)))
    monkeypatch.setattr(commit_ops, "process_deleted_file", lambda **kwargs: _async_return(True))
    monkeypatch.setattr(commit_ops, "process_deleted_folder", lambda **kwargs: _async_return(True))
    monkeypatch.setattr(commit_ops, "process_copy_file", lambda **kwargs: _async_return(True))
    monkeypatch.setattr(commit_ops, "track_lfs_object", lambda **kwargs: tracked.append(kwargs))
    monkeypatch.setattr(commit_ops, "run_gc_for_file", lambda **kwargs: gc_calls.append(kwargs) or 1)
    monkeypatch.setattr(commit_ops, "create_commit", lambda **kwargs: tracked.append({"commit": kwargs["commit_id"]}))
    monkeypatch.setattr(commit_ops, "update_repository_storage", lambda repo_arg: _async_return(None))
    monkeypatch.setattr(commit_ops, "get_organization", lambda namespace: None)
    monkeypatch.setattr(commit_ops, "update_namespace_storage", lambda namespace, is_org: _async_return(None))
    monkeypatch.setattr(commit_ops.logger, "warning", lambda message: warnings.append(message))

    monkeypatch.setattr(commit_ops.Repository, "get_or_none", lambda *args: None)
    with pytest.raises(HTTPException) as missing_repo:
            await commit_ops.commit(commit_ops.RepoType.model, "owner", "repo", "main", _FakeRequest(b""), user=user)
    assert missing_repo.value.status_code == 404

    monkeypatch.setattr(commit_ops.Repository, "get_or_none", lambda *args: repo)
    with pytest.raises(HTTPException) as invalid_json:
        await commit_ops.commit(commit_ops.RepoType.model, "owner", "repo", "main", _FakeRequest(b"{bad"), user=user)
    assert invalid_json.value.status_code == 400

    payload_without_header = json.dumps({"key": "file", "value": {"path": "README.md"}}).encode("utf-8")
    with pytest.raises(HTTPException) as missing_header:
        await commit_ops.commit(commit_ops.RepoType.model, "owner", "repo", "main", _FakeRequest(payload_without_header), user=user)
    assert missing_header.value.status_code == 400

    noop_payload = b"\n".join(
        [
            json.dumps({"key": "header", "value": {"summary": "noop"}}).encode("utf-8"),
            json.dumps({"key": "file", "value": {"path": "README.md", "content": "aGVsbG8=", "encoding": "base64"}}).encode("utf-8"),
        ]
    )
    noop_response = await commit_ops.commit(commit_ops.RepoType.model, "owner", "repo", "main", _FakeRequest(noop_payload), user=user)
    assert noop_response["commitOid"] == "head-commit"
    assert noop_response["commitUrl"] == "models/owner/repo/commit/head-commit"

    monkeypatch.setattr(commit_ops, "process_regular_file", lambda **kwargs: _async_return(True))
    monkeypatch.setattr(
        commit_ops,
        "process_lfs_file",
        lambda **kwargs: _async_return((True, {"path": "weights.bin", "sha256": "oid", "size": 12, "old_sha256": "old"})),
    )
    success_payload = b"\n".join(
        [
            json.dumps({"key": "header", "value": {"summary": "commit", "description": "desc"}}).encode("utf-8"),
            json.dumps({"key": "file", "value": {"path": "README.md", "content": "aGVsbG8=", "encoding": "base64"}}).encode("utf-8"),
            json.dumps({"key": "lfsFile", "value": {"path": "weights.bin", "oid": "oid", "size": 12}}).encode("utf-8"),
            json.dumps({"key": "deletedFile", "value": {"path": "old.txt"}}).encode("utf-8"),
            json.dumps({"key": "deletedFolder", "value": {"path": "folder"}}).encode("utf-8"),
            json.dumps({"key": "copyFile", "value": {"path": "copied.txt", "srcPath": "README.md"}}).encode("utf-8"),
        ]
    )
    success_response = await commit_ops.commit(commit_ops.RepoType.model, "owner", "repo", "main", _FakeRequest(success_payload), user=user)
    assert success_response["commitOid"] == "commit-created"
    assert success_response["commitUrl"] == "models/owner/repo/commit/commit-created"
    assert tracked
    assert gc_calls

    client.raise_on["commit"] = RuntimeError("commit failed")
    with pytest.raises(HTTPException) as commit_failed:
        await commit_ops.commit(commit_ops.RepoType.model, "owner", "repo", "main", _FakeRequest(success_payload), user=user)
    assert commit_failed.value.status_code == 500

    client.raise_on.pop("commit", None)
    monkeypatch.setattr(commit_ops, "process_lfs_file", lambda **kwargs: _async_return((False, None)))
    monkeypatch.setattr(commit_ops, "update_repository_storage", lambda repo_arg: (_ for _ in ()).throw(RuntimeError("storage failed")))
    success_without_lfs = await commit_ops.commit(commit_ops.RepoType.model, "owner", "repo", "main", _FakeRequest(success_payload), user=user)
    assert success_without_lfs["commitOid"] == "commit-created"
    assert any("No LFS files to track" in message for message in warnings)
