"""Unit tests for commit history routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import kohakuhub.api.commit.routers.history as commit_history


class _Expr:
    def __init__(self, value):
        self.value = value

    def in_(self, other):
        return _Expr(("in", self.value, tuple(other)))


class _Field:
    def __init__(self, name: str):
        self.name = name

    def in_(self, other):
        return _Expr((self.name, "in", tuple(other)))


class _Query:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.where_calls = []

    def where(self, *args):
        self.where_calls.append(args)
        return self

    def __iter__(self):
        return iter(self.items)


class _FakeClient:
    def __init__(self):
        self.log_result = None
        self.log_error = None
        self.commit_result = None
        self.commit_error = None
        self.diff_result = None
        self.diff_error = None
        self.stat_results = {}
        self.stat_errors = {}
        self.object_results = {}
        self.object_errors = {}

    async def log_commits(self, **kwargs):
        if self.log_error:
            raise self.log_error
        return self.log_result

    async def get_commit(self, **kwargs):
        if self.commit_error:
            raise self.commit_error
        return self.commit_result

    async def diff_refs(self, **kwargs):
        if self.diff_error:
            raise self.diff_error
        return self.diff_result

    async def stat_object(self, **kwargs):
        key = (kwargs["ref"], kwargs["path"])
        if key in self.stat_errors:
            raise self.stat_errors[key]
        return self.stat_results[key]

    async def get_object(self, **kwargs):
        key = (kwargs["ref"], kwargs["path"])
        if key in self.object_errors:
            raise self.object_errors[key]
        return self.object_results[key]


def _repo_with_backrefs(commits=None, files=None):
    commit_model = SimpleNamespace(commit_id=_Field("commit_id"))
    file_model = SimpleNamespace(path_in_repo=_Field("path_in_repo"))
    return SimpleNamespace(
        commits=SimpleNamespace(select=lambda: _Query(items=commits), model=commit_model),
        files=SimpleNamespace(select=lambda: _Query(items=files), model=file_model),
    )


@pytest.mark.asyncio
async def test_list_commits_covers_not_found_empty_parse_failure_and_server_error(
    monkeypatch,
):
    client = _FakeClient()
    repo_row = _repo_with_backrefs(
        commits=[
            SimpleNamespace(
                commit_id="good-1",
                author=SimpleNamespace(username="alice"),
            ),
            SimpleNamespace(
                commit_id="broken-2",
                author=None,
            ),
        ]
    )

    monkeypatch.setattr(
        commit_history,
        "hf_repo_not_found",
        lambda repo_id, repo_type: SimpleNamespace(status_code=404, repo_id=repo_id),
    )
    monkeypatch.setattr(
        commit_history,
        "hf_server_error",
        lambda message: SimpleNamespace(status_code=500, message=message),
    )
    monkeypatch.setattr(commit_history, "check_repo_read_permission", lambda repo, user: None)
    monkeypatch.setattr(commit_history, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(commit_history, "get_lakefs_rest_client", lambda: client)

    monkeypatch.setattr(commit_history, "get_repository", lambda repo_type, namespace, name: None)
    not_found = await commit_history.list_commits("model", "alice", "demo")
    assert not_found.status_code == 404

    monkeypatch.setattr(commit_history, "get_repository", lambda repo_type, namespace, name: repo_row)
    client.log_result = None
    empty = await commit_history.list_commits("model", "alice", "demo", branch="main")
    assert empty == {"commits": [], "hasMore": False, "nextCursor": None}

    client.log_result = {
        "results": [
            {
                "id": "good-1",
                "message": "good commit",
                "creation_date": 123,
                "metadata": {"email": "alice@example.com"},
                "parents": ["parent"],
            },
            {
                "id": "broken-2",
                "message": "broken commit",
            },
        ],
        "pagination": {"has_more": True, "next_offset": "cursor-2"},
    }
    parsed = await commit_history.list_commits("model", "alice", "demo", after="cursor-1")
    assert parsed == {
        "commits": [
            {
                "id": "good-1",
                "oid": "good-1",
                "title": "good commit",
                "message": "good commit",
                "date": 123,
                "author": "alice",
                "email": "alice@example.com",
                "parents": ["parent"],
            }
        ],
        "hasMore": True,
        "nextCursor": "cursor-2",
    }

    client.log_error = RuntimeError("lakefs boom")
    failure = await commit_history.list_commits("model", "alice", "demo")
    assert failure.status_code == 500


@pytest.mark.asyncio
async def test_get_commit_detail_covers_not_found_fallback_author_and_server_error(
    monkeypatch,
):
    client = _FakeClient()
    repo_row = _repo_with_backrefs()
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(
        commit_history,
        "hf_repo_not_found",
        lambda repo_id, repo_type: SimpleNamespace(status_code=404),
    )
    monkeypatch.setattr(
        commit_history,
        "hf_server_error",
        lambda message: SimpleNamespace(status_code=500, message=message),
    )
    monkeypatch.setattr(commit_history, "check_repo_read_permission", lambda repo, user: None)
    monkeypatch.setattr(commit_history, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(commit_history, "get_lakefs_rest_client", lambda: client)
    monkeypatch.setattr(commit_history, "get_repository", lambda repo_type, namespace, name: None)

    not_found = await commit_history.get_commit_detail("model", "alice", "demo", "abc")
    assert not_found.status_code == 404

    monkeypatch.setattr(commit_history, "get_repository", lambda repo_type, namespace, name: repo_row)
    client.commit_result = {
        "id": "abc",
        "message": "commit message",
        "creation_date": 321,
        "parents": ["base"],
        "metadata": {"email": "alice@example.com"},
        "committer": "fallback-user",
    }
    monkeypatch.setattr(commit_history, "get_commit", lambda commit_id, repo: None)
    detail = await commit_history.get_commit_detail("model", "alice", "demo", "abc")
    assert detail["author"] == "fallback-user"
    assert detail["metadata"] == {"email": "alice@example.com"}

    monkeypatch.setattr(
        commit_history,
        "get_commit",
        lambda commit_id, repo: SimpleNamespace(
            author=SimpleNamespace(username="alice", id=1),
            description="desc",
            created_at=now,
        ),
    )
    detail_with_author = await commit_history.get_commit_detail("model", "alice", "demo", "abc")
    assert detail_with_author["author"] == "alice"
    assert detail_with_author["user_id"] == 1

    client.commit_error = RuntimeError("commit boom")
    failure = await commit_history.get_commit_detail("model", "alice", "demo", "abc")
    assert failure.status_code == 500


@pytest.mark.asyncio
async def test_get_commit_diff_covers_parentless_diff_generation_skips_and_errors(
    monkeypatch,
):
    client = _FakeClient()
    repo_row = _repo_with_backrefs(
        files=[SimpleNamespace(path_in_repo="weights.bin", lfs=True)]
    )

    monkeypatch.setattr(
        commit_history,
        "hf_repo_not_found",
        lambda repo_id, repo_type: SimpleNamespace(status_code=404),
    )
    monkeypatch.setattr(
        commit_history,
        "hf_server_error",
        lambda message: SimpleNamespace(status_code=500, message=message),
    )
    monkeypatch.setattr(commit_history, "check_repo_read_permission", lambda repo, user: None)
    monkeypatch.setattr(commit_history, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(commit_history, "get_lakefs_rest_client", lambda: client)
    monkeypatch.setattr(
        commit_history,
        "should_use_lfs",
        lambda repo, path, size: path.endswith(".bin"),
    )
    monkeypatch.setattr(commit_history, "get_repository", lambda repo_type, namespace, name: None)

    not_found = await commit_history.get_commit_diff("model", "alice", "demo", "abc")
    assert not_found.status_code == 404

    monkeypatch.setattr(commit_history, "get_repository", lambda repo_type, namespace, name: repo_row)
    client.commit_result = {"id": "abc", "parents": [], "message": "initial", "creation_date": 1}
    parentless = await commit_history.get_commit_diff("model", "alice", "demo", "abc")
    assert parentless == {"files": [], "parent_commit": None}

    client.commit_result = {
        "id": "abc",
        "parents": ["parent-1"],
        "message": "update files",
        "creation_date": 9,
        "committer": "lakefs-user",
    }
    client.diff_result = {
        "results": [
            {"path": "added.txt", "type": "added", "path_type": "object", "size_bytes": 5},
            {"path": "weights.bin", "type": "changed", "path_type": "object", "size_bytes": 9},
            {"path": "large.txt", "type": "removed", "path_type": "object", "size_bytes": None},
            {"path": "broken.txt", "type": "changed", "path_type": "object", "size_bytes": 3},
        ]
    }
    client.stat_results = {
        ("abc", "added.txt"): {"size_bytes": 5, "checksum": "sha256:added"},
        ("abc", "weights.bin"): {"size_bytes": 9, "checksum": "plain-sha"},
        ("parent-1", "weights.bin"): {"size_bytes": 4, "checksum": "sha256:previous"},
        ("parent-1", "large.txt"): {"size_bytes": 1000000, "checksum": "sha256:large"},
    }
    client.stat_errors = {
        ("abc", "broken.txt"): RuntimeError("no current stat"),
        ("parent-1", "broken.txt"): RuntimeError("no previous stat"),
    }
    client.object_results = {
        ("abc", "added.txt"): b"hello\n",
        ("abc", "weights.bin"): b"new-binary",
        ("parent-1", "weights.bin"): b"old-binary",
        ("parent-1", "large.txt"): b"very large",
    }
    client.object_errors = {
        ("abc", "broken.txt"): RuntimeError("object broken"),
    }
    monkeypatch.setattr(commit_history, "get_commit", lambda commit_id, repo: None)

    diff = await commit_history.get_commit_diff("model", "alice", "demo", "abc")
    assert diff["parent_commit"] == "parent-1"
    assert diff["author"] == "lakefs-user"
    assert diff["files"][0]["sha256"] == "added"
    assert diff["files"][0]["diff"].startswith("--- a/added.txt")
    assert diff["files"][1]["is_lfs"] is True
    assert diff["files"][1]["sha256"] == "plain-sha"
    assert diff["files"][1]["previous_sha256"] == "previous"
    assert diff["files"][2]["diff"] is None
    assert diff["files"][3]["diff"] is None

    client.commit_error = RuntimeError("diff commit boom")
    failure = await commit_history.get_commit_diff("model", "alice", "demo", "abc")
    assert failure.status_code == 500
