"""Unit tests for branch and tag management helpers and routes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.branches as branches_api


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.branch_data = {"commit_id": "branch-head"}
        self.commit_data = {"id": "commit-1"}
        self.diff_result = {"results": []}
        self.merge_result = {"reference": "merge-commit"}
        self.list_branch_payloads = []
        self.list_tag_payloads = []
        self.raise_on = {}

    def _maybe_raise(self, name):
        error = self.raise_on.get(name)
        if error:
            raise error

    async def get_branch(self, **kwargs):
        self.calls.append(("get_branch", kwargs))
        self._maybe_raise("get_branch")
        return self.branch_data

    async def create_branch(self, **kwargs):
        self.calls.append(("create_branch", kwargs))
        self._maybe_raise("create_branch")
        return {"ok": True}

    async def delete_branch(self, **kwargs):
        self.calls.append(("delete_branch", kwargs))
        self._maybe_raise("delete_branch")
        return {"ok": True}

    async def create_tag(self, **kwargs):
        self.calls.append(("create_tag", kwargs))
        self._maybe_raise("create_tag")
        return {"ok": True}

    async def delete_tag(self, **kwargs):
        self.calls.append(("delete_tag", kwargs))
        self._maybe_raise("delete_tag")
        return {"ok": True}

    async def list_branches(self, **kwargs):
        self.calls.append(("list_branches", kwargs))
        self._maybe_raise("list_branches")
        return self.list_branch_payloads.pop(0)

    async def list_tags(self, **kwargs):
        self.calls.append(("list_tags", kwargs))
        self._maybe_raise("list_tags")
        return self.list_tag_payloads.pop(0)

    async def get_commit(self, **kwargs):
        self.calls.append(("get_commit", kwargs))
        self._maybe_raise("get_commit")
        return self.commit_data

    async def revert_branch(self, **kwargs):
        self.calls.append(("revert_branch", kwargs))
        self._maybe_raise("revert_branch")
        return {"ok": True}

    async def merge_into_branch(self, **kwargs):
        self.calls.append(("merge_into_branch", kwargs))
        self._maybe_raise("merge_into_branch")
        return self.merge_result

    async def diff_refs(self, **kwargs):
        self.calls.append(("diff_refs", kwargs))
        self._maybe_raise("diff_refs")
        return self.diff_result

    async def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))
        self._maybe_raise("delete_object")
        return {"ok": True}

    async def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        self._maybe_raise("get_object")
        return b"content"

    async def upload_object(self, **kwargs):
        self.calls.append(("upload_object", kwargs))
        self._maybe_raise("upload_object")
        return {"ok": True}

    async def commit(self, **kwargs):
        self.calls.append(("commit", kwargs))
        self._maybe_raise("commit")
        return {"id": "new-commit-id"}


def _response_error_message(response) -> str:
    return response.headers.get("x-error-message", "")


@pytest.mark.asyncio
async def test_create_branch_and_tag_routes_cover_success_and_error_paths(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    user = SimpleNamespace(username="owner")
    client = _FakeClient()

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(branches_api, "check_repo_delete_permission", lambda repo_arg, user_arg: None)
    monkeypatch.setattr(branches_api, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(branches_api, "get_lakefs_client", lambda: client)

    create_branch_response = await branches_api.create_branch(
        "model",
        "owner",
        "repo",
        branches_api.CreateBranchPayload(branch="feature", revision="dev"),
        user=user,
    )
    create_tag_response = await branches_api.create_tag(
        "model",
        "owner",
        "repo",
        branches_api.CreateTagPayload(tag="v1", revision="dev"),
        user=user,
    )
    compat_branch_response = await branches_api.create_branch_compat(
        "model",
        "owner",
        "repo",
        "feature-2",
        branches_api.CreateBranchCompatPayload(startingPoint="main"),
        user=user,
    )
    compat_tag_response = await branches_api.create_tag_compat(
        "model",
        "owner",
        "repo",
        "main",
        branches_api.CreateTagCompatPayload(tag="v2"),
        user=user,
    )

    assert create_branch_response["success"] is True
    assert create_tag_response["success"] is True
    assert compat_branch_response["success"] is True
    assert compat_tag_response["success"] is True
    assert ("create_branch", {"repository": "model:owner/repo", "name": "feature", "source": "branch-head"}) in client.calls
    assert ("create_tag", {"repository": "model:owner/repo", "id": "v1", "ref": "branch-head"}) in client.calls

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: None)
    not_found_response = await branches_api.create_branch(
        "model",
        "owner",
        "repo",
        branches_api.CreateBranchPayload(branch="feature"),
        user=user,
    )
    assert not_found_response.status_code == 404

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: repo)
    client.raise_on["create_branch"] = RuntimeError("409 conflict")
    conflict_response = await branches_api.create_branch(
        "model",
        "owner",
        "repo",
        branches_api.CreateBranchPayload(branch="feature"),
        user=user,
    )
    assert conflict_response.status_code == 409
    assert "already exists" in _response_error_message(conflict_response)

    client.raise_on["create_branch"] = RuntimeError("boom")
    generic_branch_error = await branches_api.create_branch(
        "model",
        "owner",
        "repo",
        branches_api.CreateBranchPayload(branch="feature"),
        user=user,
    )
    assert generic_branch_error.status_code == 500

    client.raise_on.pop("create_branch", None)
    client.raise_on["create_tag"] = RuntimeError("tag failed")
    generic_tag_error = await branches_api.create_tag(
        "model",
        "owner",
        "repo",
        branches_api.CreateTagPayload(tag="v3"),
        user=user,
    )
    assert generic_tag_error.status_code == 500


@pytest.mark.asyncio
async def test_delete_branch_and_tag_cover_success_not_found_and_guardrails(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    user = SimpleNamespace(username="owner")
    client = _FakeClient()

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(branches_api, "check_repo_delete_permission", lambda repo_arg, user_arg: None)
    monkeypatch.setattr(branches_api, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(branches_api, "get_lakefs_client", lambda: client)

    main_response = await branches_api.delete_branch("model", "owner", "repo", "main", user=user)
    assert main_response.status_code == 400
    assert "Cannot delete main branch" in _response_error_message(main_response)

    branch_response = await branches_api.delete_branch("model", "owner", "repo", "feature", user=user)
    tag_response = await branches_api.delete_tag("model", "owner", "repo", "v1", user=user)
    assert branch_response["success"] is True
    assert tag_response["success"] is True

    client.raise_on["delete_branch"] = RuntimeError("cannot delete branch")
    branch_error = await branches_api.delete_branch("model", "owner", "repo", "feature", user=user)
    assert branch_error.status_code == 500

    client.raise_on["delete_tag"] = RuntimeError("cannot delete tag")
    tag_error = await branches_api.delete_tag("model", "owner", "repo", "v1", user=user)
    assert tag_error.status_code == 500

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: None)
    not_found = await branches_api.delete_tag("model", "owner", "repo", "v1", user=user)
    assert not_found.status_code == 404


@pytest.mark.asyncio
async def test_reference_helpers_and_list_repo_refs_cover_pagination_and_fallback(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    client = _FakeClient()
    warnings = []

    client.list_branch_payloads = [
        {
            "results": [{"id": "dev", "commit_id": "c2"}],
            "pagination": {"has_more": True, "next_offset": "page-2"},
        },
        {"results": [{"name": "main", "commit": {"id": "c1"}}], "pagination": {"has_more": False}},
    ]
    client.list_tag_payloads = [
        [
            {"id": "v2", "hash": "c3"},
            {"name": "v1", "commit": {"commitId": "c0"}},
            {"name": None},
        ]
    ]

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(branches_api, "check_repo_read_permission", lambda repo_arg, user: None)
    monkeypatch.setattr(branches_api, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(branches_api, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(branches_api.logger, "warning", lambda message: warnings.append(message))

    assert branches_api._resolve_ref_name({"id": "main"}) == "main"
    assert branches_api._resolve_ref_name({"name": "dev"}) == "dev"
    assert branches_api._resolve_target_commit({"commit": {"commit_id": "abc"}}) == "abc"
    assert branches_api._resolve_target_commit({"commitId": "def"}) == "def"

    refs_response = await branches_api.list_repo_refs("model", "owner", "repo", include_prs=True, user=None)
    assert [item["name"] for item in refs_response["branches"]] == ["dev", "main"]
    assert [item["name"] for item in refs_response["tags"]] == ["v1", "v2"]
    assert refs_response["pullRequests"] == []

    failing_client = _FakeClient()
    failing_client.raise_on["list_branches"] = RuntimeError("no branch listing")
    failing_client.raise_on["get_branch"] = RuntimeError("no main")
    failing_client.raise_on["list_tags"] = RuntimeError("no tag listing")
    monkeypatch.setattr(branches_api, "get_lakefs_client", lambda: failing_client)
    fallback_response = await branches_api.list_repo_refs("model", "owner", "repo", user=None)
    assert fallback_response == {"branches": [], "converts": [], "tags": []}
    assert warnings


@pytest.mark.asyncio
async def test_revert_branch_covers_not_found_conflict_success_and_tracking_failure(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    user = SimpleNamespace(username="owner")
    client = _FakeClient()
    created_commits = []

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(branches_api, "check_repo_write_permission", lambda repo_arg, user_arg: None)
    monkeypatch.setattr(branches_api, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(branches_api, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(branches_api, "track_commit_lfs_objects", lambda **kwargs: _async_return(2))
    monkeypatch.setattr(branches_api, "create_commit", lambda **kwargs: created_commits.append(kwargs))

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: None)
    not_found = await branches_api.revert_branch(
        "model",
        "owner",
        "repo",
        "main",
        branches_api.RevertPayload(ref="abc"),
        user=user,
    )
    assert not_found.status_code == 404

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: repo)
    client.raise_on["get_commit"] = RuntimeError("missing")
    with pytest.raises(HTTPException) as missing_commit:
        await branches_api.revert_branch(
            "model",
            "owner",
            "repo",
            "main",
            branches_api.RevertPayload(ref="abc"),
            user=user,
        )
    assert missing_commit.value.status_code == 404

    client.raise_on.pop("get_commit", None)
    client.raise_on["revert_branch"] = RuntimeError("409 conflict")
    with pytest.raises(HTTPException) as conflict_error:
        await branches_api.revert_branch(
            "model",
            "owner",
            "repo",
            "main",
            branches_api.RevertPayload(ref="abc"),
            user=user,
        )
    assert conflict_error.value.status_code == 409

    client.raise_on["revert_branch"] = RuntimeError("boom")
    with pytest.raises(HTTPException) as generic_error:
        await branches_api.revert_branch(
            "model",
            "owner",
            "repo",
            "main",
            branches_api.RevertPayload(ref="abc"),
            user=user,
        )
    assert generic_error.value.status_code == 500

    client.raise_on.pop("revert_branch", None)
    client.branch_data = {"commit_id": "revert-commit"}
    result = await branches_api.revert_branch(
        "model",
        "owner",
        "repo",
        "main",
        branches_api.RevertPayload(ref="abc", message="revert it"),
        user=user,
    )
    assert result["success"] is True
    assert result["new_commit_id"] == "revert-commit"
    assert created_commits[-1]["commit_id"] == "revert-commit"

    async def broken_track(**kwargs):
        raise RuntimeError("tracking broke")

    monkeypatch.setattr(branches_api, "track_commit_lfs_objects", broken_track)
    result = await branches_api.revert_branch(
        "model",
        "owner",
        "repo",
        "main",
        branches_api.RevertPayload(ref="abc"),
        user=user,
    )
    assert result["success"] is True


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner()


@pytest.mark.asyncio
async def test_merge_branches_covers_not_found_conflict_success_and_tracking_paths(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    user = SimpleNamespace(username="owner")
    client = _FakeClient()
    created_commits = []

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(branches_api, "check_repo_write_permission", lambda repo_arg, user_arg: None)
    monkeypatch.setattr(branches_api, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(branches_api, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(branches_api, "track_commit_lfs_objects", lambda **kwargs: _async_return(1))
    monkeypatch.setattr(branches_api, "create_commit", lambda **kwargs: created_commits.append(kwargs))

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: None)
    not_found = await branches_api.merge_branches(
        "model", "owner", "repo", "feature", "main", branches_api.MergePayload(), user=user
    )
    assert not_found.status_code == 404

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: repo)
    client.raise_on["merge_into_branch"] = RuntimeError("merge conflict happened")
    with pytest.raises(HTTPException) as conflict_error:
        await branches_api.merge_branches(
            "model", "owner", "repo", "feature", "main", branches_api.MergePayload(), user=user
        )
    assert conflict_error.value.status_code == 409

    client.raise_on["merge_into_branch"] = RuntimeError("merge broke")
    with pytest.raises(HTTPException) as generic_error:
        await branches_api.merge_branches(
            "model", "owner", "repo", "feature", "main", branches_api.MergePayload(), user=user
        )
    assert generic_error.value.status_code == 500

    client.raise_on.pop("merge_into_branch", None)
    client.merge_result = {"reference": "merge-commit"}
    result = await branches_api.merge_branches(
        "model", "owner", "repo", "feature", "main", branches_api.MergePayload(message="merge it"), user=user
    )
    assert result["result"]["reference"] == "merge-commit"
    assert created_commits[-1]["commit_id"] == "merge-commit"

    client.merge_result = {"status": "ok"}
    result = await branches_api.merge_branches(
        "model", "owner", "repo", "feature", "main", branches_api.MergePayload(), user=user
    )
    assert result["success"] is True

    async def broken_track(**kwargs):
        raise RuntimeError("tracking broke")

    monkeypatch.setattr(branches_api, "track_commit_lfs_objects", broken_track)
    client.merge_result = {"reference": "merge-commit-2"}
    result = await branches_api.merge_branches(
        "model", "owner", "repo", "feature", "main", branches_api.MergePayload(), user=user
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_reset_branch_covers_guardrails_recoverability_success_and_failures(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    user = SimpleNamespace(username="owner")
    client = _FakeClient()
    created_commits = []
    synced_refs = []

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(branches_api, "check_repo_write_permission", lambda repo_arg, user_arg: None)
    monkeypatch.setattr(branches_api, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(branches_api, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(branches_api, "check_commit_range_recoverability", lambda **kwargs: _async_return((True, [], [])))
    monkeypatch.setattr(branches_api, "sync_file_table_with_commit", lambda **kwargs: synced_refs.append(kwargs) or _async_return(3))
    monkeypatch.setattr(branches_api, "create_commit", lambda **kwargs: created_commits.append(kwargs))

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: None)
    not_found = await branches_api.reset_branch(
        "model", "owner", "repo", "main", branches_api.ResetPayload(ref="abc"), user=user
    )
    assert not_found.status_code == 404

    monkeypatch.setattr(branches_api, "get_repository", lambda *_args: repo)
    with pytest.raises(HTTPException) as main_guard:
        await branches_api.reset_branch(
            "model", "owner", "repo", "main", branches_api.ResetPayload(ref="abc"), user=user
        )
    assert main_guard.value.status_code == 400

    client.raise_on["get_commit"] = RuntimeError("missing")
    with pytest.raises(HTTPException) as missing_commit:
        await branches_api.reset_branch(
            "model", "owner", "repo", "feature", branches_api.ResetPayload(ref="abc", force=True), user=user
        )
    assert missing_commit.value.status_code == 404

    client.raise_on.pop("get_commit", None)
    monkeypatch.setattr(
        branches_api,
        "check_commit_range_recoverability",
        lambda **kwargs: _async_return((False, ["missing.bin"] * 2, ["c1"])),
    )
    with pytest.raises(HTTPException) as unrecoverable:
        await branches_api.reset_branch(
            "model", "owner", "repo", "feature", branches_api.ResetPayload(ref="abc"), user=user
        )
    assert unrecoverable.value.status_code == 400
    assert unrecoverable.value.detail["recoverable"] is False

    monkeypatch.setattr(branches_api, "check_commit_range_recoverability", lambda **kwargs: _async_return((True, [], [])))
    client.branch_data = {"commit_id": "current-commit"}
    client.diff_result = {"results": []}
    with pytest.raises(HTTPException) as already_target:
        await branches_api.reset_branch(
            "model", "owner", "repo", "feature", branches_api.ResetPayload(ref="abc"), user=user
        )
    assert already_target.value.status_code == 400

    client.diff_result = {
        "results": [
            {"path": "added.txt", "path_type": "object", "type": "added"},
            {"path": "removed.txt", "path_type": "object", "type": "removed"},
            {"path": "changed.txt", "path_type": "object", "type": "changed"},
            {"path": "folder/", "path_type": "common_prefix", "type": "changed"},
        ]
    }
    result = await branches_api.reset_branch(
        "model", "owner", "repo", "feature", branches_api.ResetPayload(ref="abc", message="reset it", force=True), user=user
    )
    assert result["success"] is True
    assert result["commit_id"] == "new-commit-id"
    assert created_commits[-1]["commit_id"] == "new-commit-id"
    assert synced_refs

    async def broken_sync(**kwargs):
        raise RuntimeError("sync broke")

    monkeypatch.setattr(branches_api, "sync_file_table_with_commit", broken_sync)
    result = await branches_api.reset_branch(
        "model", "owner", "repo", "feature", branches_api.ResetPayload(ref="abc", force=True), user=user
    )
    assert result["success"] is True

    client.raise_on["commit"] = RuntimeError("commit broke")
    with pytest.raises(HTTPException) as generic_error:
        await branches_api.reset_branch(
            "model", "owner", "repo", "feature", branches_api.ResetPayload(ref="abc", force=True), user=user
        )
    assert generic_error.value.status_code == 500
