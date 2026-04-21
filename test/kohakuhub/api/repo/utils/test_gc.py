"""Tests for repository garbage-collection helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import kohakuhub.api.repo.utils.gc as gc_utils


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

    def desc(self):
        return _Expr((self.name, "desc"))

    def in_(self, values):
        return _Expr((self.name, "in", tuple(values)))

    def not_in(self, values):
        return _Expr((self.name, "not_in", tuple(values)))

    def __hash__(self):
        return hash(self.name)


class _Query:
    def __init__(self, items=None, count_result=None, execute_result=0):
        self.items = list(items or [])
        self.count_result = len(self.items) if count_result is None else count_result
        self.execute_result = execute_result
        self.where_calls = []
        self.order_by_calls = []
        self.select_calls = []

    def where(self, *args):
        self.where_calls.append(args)
        return self

    def order_by(self, *args):
        self.order_by_calls.append(args)
        return self

    def count(self):
        return self.count_result

    def execute(self):
        return self.execute_result

    def distinct(self):
        return self

    def select(self, *args):
        self.select_calls.append(args)
        return self

    def __iter__(self):
        return iter(self.items)


class _InsertQuery:
    def __init__(self):
        self.on_conflict_calls = []
        self.execute_calls = 0

    def on_conflict(self, **kwargs):
        self.on_conflict_calls.append(kwargs)
        return self

    def execute(self):
        self.execute_calls += 1
        return 1


class _FakeFileModel:
    repository = _Field("repository")
    path_in_repo = _Field("path_in_repo")
    sha256 = _Field("sha256")
    lfs = _Field("lfs")
    is_deleted = _Field("is_deleted")
    updated_at = _Field("updated_at")
    size = _Field("size")
    owner = _Field("owner")

    select_query = _Query()
    delete_query = _Query()
    get_or_none_result = None
    get_or_none_side_effect = None
    insert_calls = []

    @classmethod
    def reset(cls):
        cls.select_query = _Query()
        cls.delete_query = _Query()
        cls.get_or_none_result = None
        cls.get_or_none_side_effect = None
        cls.insert_calls = []

    @classmethod
    def select(cls, *args):
        return cls.select_query

    @classmethod
    def get_or_none(cls, *args):
        if cls.get_or_none_side_effect is not None:
            return cls.get_or_none_side_effect(*args)
        return cls.get_or_none_result

    @classmethod
    def insert(cls, **kwargs):
        cls.insert_calls.append(kwargs)
        return _InsertQuery()

    @classmethod
    def delete(cls):
        return cls.delete_query


class _FakeHistoryModel:
    repository = _Field("repository")
    path_in_repo = _Field("path_in_repo")
    created_at = _Field("created_at")
    sha256 = _Field("sha256")
    commit_id = _Field("commit_id")

    select_query = _Query()
    delete_query = _Query()

    @classmethod
    def reset(cls):
        cls.select_query = _Query()
        cls.delete_query = _Query()

    @classmethod
    def select(cls, *args):
        return cls.select_query

    @classmethod
    def delete(cls):
        return cls.delete_query


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


@pytest.fixture(autouse=True)
def _reset_fake_models():
    _FakeFileModel.reset()
    _FakeHistoryModel.reset()


def test_track_lfs_object_and_get_old_versions_cover_repo_lookup_and_dedup(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    file_fk = SimpleNamespace(id=1)
    created = []

    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(gc_utils, "File", _FakeFileModel)
    monkeypatch.setattr(gc_utils, "create_lfs_history", lambda **kwargs: created.append(kwargs))
    _FakeFileModel.get_or_none_result = file_fk

    gc_utils.track_lfs_object(
        "model",
        "owner",
        "repo",
        "weights/model.bin",
        "a" * 64,
        123,
        "commit-1",
    )

    assert created == [
        {
            "repository": repo,
            "path_in_repo": "weights/model.bin",
            "sha256": "a" * 64,
            "size": 123,
            "commit_id": "commit-1",
            "file": file_fk,
        }
    ]

    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: None)
    gc_utils.track_lfs_object("model", "owner", "repo", "x", "b" * 64, 1, "commit-2")
    assert len(created) == 1

    repo = SimpleNamespace(full_id="owner/repo")
    _FakeHistoryModel.select_query = _Query(
        items=[
            SimpleNamespace(sha256="n1", created_at=3),
            SimpleNamespace(sha256="n1", created_at=2),
            SimpleNamespace(sha256="n2", created_at=1),
            SimpleNamespace(sha256="n3", created_at=0),
        ]
    )
    monkeypatch.setattr(gc_utils, "LFSObjectHistory", _FakeHistoryModel)

    assert gc_utils.get_old_lfs_versions(repo, "weights/model.bin", keep_count=2) == ["n3"]

    _FakeHistoryModel.select_query = _Query(items=[])
    assert gc_utils.get_old_lfs_versions(repo, "weights/model.bin", keep_count=2) == []


def test_cleanup_lfs_object_respects_current_use_history_and_deletion(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    deleted = []
    monkeypatch.setattr(gc_utils, "File", _FakeFileModel)
    monkeypatch.setattr(gc_utils, "LFSObjectHistory", _FakeHistoryModel)
    monkeypatch.setattr(gc_utils.cfg.s3, "bucket", "hub-storage")
    monkeypatch.setattr(
        gc_utils,
        "get_s3_client",
        lambda: SimpleNamespace(delete_object=lambda **kwargs: deleted.append(kwargs)),
    )

    _FakeFileModel.select_query = _Query(count_result=1)
    assert gc_utils.cleanup_lfs_object("a" * 64) is False

    _FakeFileModel.select_query = _Query(count_result=0)
    _FakeHistoryModel.select_query = _Query(count_result=2)
    assert gc_utils.cleanup_lfs_object("b" * 64) is False

    _FakeHistoryModel.select_query = _Query(count_result=0)
    _FakeHistoryModel.delete_query = _Query(execute_result=3)
    assert gc_utils.cleanup_lfs_object("c" * 64) is True
    assert deleted[-1]["Bucket"] == "hub-storage"

    _FakeHistoryModel.delete_query = _Query(execute_result=1)
    assert gc_utils.cleanup_lfs_object("d" * 64, repo=repo) is True

    monkeypatch.setattr(
        gc_utils,
        "get_s3_client",
        lambda: SimpleNamespace(delete_object=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))),
    )
    assert gc_utils.cleanup_lfs_object("e" * 64) is False


def test_run_gc_for_file_handles_disabled_missing_repo_and_deleted_counts(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    monkeypatch.setattr(gc_utils.cfg.app, "lfs_auto_gc", False)
    assert gc_utils.run_gc_for_file("model", "owner", "repo", "weights.bin", "commit-1") == 0

    monkeypatch.setattr(gc_utils.cfg.app, "lfs_auto_gc", True)
    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: None)
    assert gc_utils.run_gc_for_file("model", "owner", "repo", "weights.bin", "commit-1") == 0

    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(gc_utils, "get_effective_lfs_keep_versions", lambda repo_arg: 2)
    monkeypatch.setattr(gc_utils, "get_old_lfs_versions", lambda repo_arg, path, keep: ["a", "b"])
    monkeypatch.setattr(gc_utils, "cleanup_lfs_object", lambda sha256, repo_arg: sha256 == "a")

    assert gc_utils.run_gc_for_file("model", "owner", "repo", "weights.bin", "commit-1") == 1


@pytest.mark.asyncio
async def test_check_lfs_recoverability_covers_empty_and_missing_objects(monkeypatch):
    empty_repo = SimpleNamespace(lfs_history=SimpleNamespace(select=lambda: _Query(items=[])))
    monkeypatch.setattr(gc_utils, "LFSObjectHistory", _FakeHistoryModel)

    assert await gc_utils.check_lfs_recoverability(empty_repo, "commit-1") == (True, [])

    lfs_objects = [
        SimpleNamespace(path_in_repo="weights.bin", sha256="a" * 64),
        SimpleNamespace(path_in_repo="config.json", sha256="b" * 64),
    ]
    repo = SimpleNamespace(lfs_history=SimpleNamespace(select=lambda: _Query(items=lfs_objects)))
    monkeypatch.setattr(
        gc_utils,
        "object_exists",
        lambda bucket, key: _async_return(not key.endswith("b" * 64))(),
    )
    monkeypatch.setattr(gc_utils.cfg.s3, "bucket", "hub-storage")

    recoverable, missing_files = await gc_utils.check_lfs_recoverability(repo, "commit-2")

    assert recoverable is False
    assert missing_files == ["config.json"]


@pytest.mark.asyncio
async def test_check_commit_range_recoverability_covers_missing_repo_target_and_results(monkeypatch):
    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: None)
    assert await gc_utils.check_commit_range_recoverability(
        "lakefs-repo", "model", "owner", "repo", "target", "main"
    ) == (False, [], [])

    repo = SimpleNamespace(full_id="owner/repo")
    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: repo)

    class MissingTargetClient:
        async def log_commits(self, **kwargs):
            return {"results": [{"id": "head"}], "pagination": {"has_more": False}}

    monkeypatch.setattr(gc_utils, "get_lakefs_client", lambda: MissingTargetClient())
    assert await gc_utils.check_commit_range_recoverability(
        "lakefs-repo", "model", "owner", "repo", "target", "main"
    ) == (False, [], [])

    class WorkingClient:
        async def log_commits(self, **kwargs):
            return {
                "results": [{"id": "head"}, {"id": "target"}, {"id": "older"}],
                "pagination": {"has_more": False},
            }

    async def fake_check_lfs_recoverability(repo_arg, commit_id):
        if commit_id == "head":
            return True, []
        return False, [f"{commit_id}.bin"]

    monkeypatch.setattr(gc_utils, "get_lakefs_client", lambda: WorkingClient())
    monkeypatch.setattr(gc_utils, "check_lfs_recoverability", fake_check_lfs_recoverability)

    recoverable, missing_files, affected_commits = await gc_utils.check_commit_range_recoverability(
        "lakefs-repo", "model", "owner", "repo", "target", "main"
    )

    assert recoverable is False
    assert missing_files == ["target.bin"]
    assert affected_commits == ["target"]


@pytest.mark.asyncio
async def test_sync_file_table_with_commit_syncs_objects_and_removes_stale_entries(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo", owner=SimpleNamespace(username="owner"))
    file_placeholder = SimpleNamespace(id=7)
    create_history_calls = []

    class FakeClient:
        async def get_branch(self, repository, branch):
            return {"commit_id": "commit-1"}

        async def list_objects(self, repository, ref, amount, after):
            if after == "":
                return {
                    "results": [
                        {
                            "path_type": "object",
                            "path": "README.md",
                            "size_bytes": 3,
                            "checksum": "sha256:readme",
                        },
                        {"path_type": "common_prefix", "path": "folder/"},
                    ],
                    "pagination": {"has_more": True, "next_offset": "page-2"},
                }
            return {
                "results": [
                    {
                        "path_type": "object",
                        "path": "weights/model.safetensors",
                        "size_bytes": 12,
                        "checksum": "sha256:weights",
                    }
                ],
                "pagination": {"has_more": False},
            }

    calls = {"get_or_none": 0}

    def fake_get_or_none(*args):
        calls["get_or_none"] += 1
        if calls["get_or_none"] >= 2:
            return file_placeholder
        return None

    monkeypatch.setattr(gc_utils, "get_lakefs_client", lambda: FakeClient())
    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(gc_utils, "File", _FakeFileModel)
    monkeypatch.setattr(gc_utils, "create_lfs_history", lambda **kwargs: create_history_calls.append(kwargs))
    monkeypatch.setattr(gc_utils, "should_use_lfs", lambda repo_arg, path, size: path.endswith(".safetensors"))
    _FakeFileModel.get_or_none_side_effect = fake_get_or_none
    _FakeFileModel.delete_query = _Query(execute_result=1)

    synced = await gc_utils.sync_file_table_with_commit(
        "lakefs-repo", "main", "model", "owner", "repo"
    )

    assert synced == 2
    assert len(_FakeFileModel.insert_calls) == 2
    assert create_history_calls[0]["sha256"] == "weights"
    assert _FakeFileModel.delete_query.where_calls


@pytest.mark.asyncio
async def test_cleanup_repository_storage_cleans_repo_prefix_and_unreferenced_lfs(monkeypatch):
    repo = SimpleNamespace(
        full_id="owner/repo",
        lfs_history=SimpleNamespace(
            select=lambda *args: _Query(
                items=[
                    SimpleNamespace(sha256="a" * 64),
                    SimpleNamespace(sha256="b" * 64),
                ]
            )
        ),
    )
    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(gc_utils, "LFSObjectHistory", _FakeHistoryModel)
    monkeypatch.setattr(gc_utils, "delete_objects_with_prefix", _async_return(3))
    monkeypatch.setattr(gc_utils, "cleanup_lfs_object", lambda sha256, repo=None: sha256.startswith("a"))
    _FakeHistoryModel.delete_query = _Query(execute_result=4)

    result = await gc_utils.cleanup_repository_storage(
        "model", "owner", "repo", "lakefs-repo"
    )

    assert result == {
        "repo_objects_deleted": 3,
        "lfs_objects_deleted": 1,
        "lfs_history_deleted": 4,
    }

    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: None)
    assert await gc_utils.cleanup_repository_storage(
        "model", "owner", "repo", "lakefs-repo"
    ) == {
        "repo_objects_deleted": 0,
        "lfs_objects_deleted": 0,
        "lfs_history_deleted": 0,
    }


@pytest.mark.asyncio
async def test_track_commit_lfs_objects_handles_missing_repo_no_parents_and_success(monkeypatch):
    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: None)
    assert await gc_utils.track_commit_lfs_objects(
        "lakefs-repo", "commit-1", "model", "owner", "repo"
    ) == 0

    repo = SimpleNamespace(full_id="owner/repo", owner=SimpleNamespace(username="owner"))
    monkeypatch.setattr(gc_utils, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(gc_utils, "File", _FakeFileModel)
    create_history_calls = []
    _FakeFileModel.delete_query = _Query(execute_result=1)
    _FakeFileModel.get_or_none_result = SimpleNamespace(id=9)

    class NoParentClient:
        async def get_commit(self, repository, commit_id):
            return {"parents": []}

    monkeypatch.setattr(gc_utils, "get_lakefs_client", lambda: NoParentClient())
    assert await gc_utils.track_commit_lfs_objects(
        "lakefs-repo", "commit-1", "model", "owner", "repo"
    ) == 0

    class WorkingClient:
        async def get_commit(self, repository, commit_id):
            return {"parents": ["parent-1"]}

        async def diff_refs(self, repository, left_ref, right_ref):
            return {
                "results": [
                    {"path": "old.bin", "path_type": "object", "type": "removed"},
                    {"path": "weights/model.safetensors", "path_type": "object", "type": "changed"},
                    {"path": "README.md", "path_type": "object", "type": "changed"},
                    {"path": "folder/", "path_type": "common_prefix", "type": "changed"},
                ]
            }

        async def stat_object(self, repository, ref, path):
            if path == "README.md":
                return {"size_bytes": 3, "checksum": "sha256:readme"}
            return {"size_bytes": 12, "checksum": "sha256:weights"}

    monkeypatch.setattr(gc_utils, "get_lakefs_client", lambda: WorkingClient())
    monkeypatch.setattr(gc_utils, "should_use_lfs", lambda repo_arg, path, size: path.endswith(".safetensors"))
    monkeypatch.setattr(gc_utils, "create_lfs_history", lambda **kwargs: create_history_calls.append(kwargs))

    tracked = await gc_utils.track_commit_lfs_objects(
        "lakefs-repo", "commit-2", "model", "owner", "repo"
    )

    assert tracked == 1
    assert create_history_calls[0]["path_in_repo"] == "weights/model.safetensors"
    assert len(_FakeFileModel.insert_calls) == 2
    assert _FakeFileModel.delete_query.where_calls
