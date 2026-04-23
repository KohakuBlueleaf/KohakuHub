"""Unit tests for file routes and helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.files as files_api


class _FakeClient:
    def __init__(self):
        self.stat_result = None
        self.stat_error = None
        self.object_result = None
        self.object_error = None
        self.branch_result = None
        self.branch_error = None

    async def stat_object(self, **kwargs):
        if self.stat_error:
            raise self.stat_error
        return self.stat_result

    async def get_object(self, **kwargs):
        if self.object_error:
            raise self.object_error
        return self.object_result

    async def get_branch(self, **kwargs):
        if self.branch_error:
            raise self.branch_error
        return self.branch_result


class _FakeRequest:
    def __init__(self, body=None, error=None, cookies=None):
        self._body = body
        self._error = error
        self.cookies = dict(cookies or {})

    async def json(self):
        if self._error:
            raise self._error
        return self._body


def _async_return(value=None):
    async def _inner(*args, **kwargs):
        return value

    return _inner


@pytest.mark.asyncio
async def test_hash_and_sample_helpers_cover_match_failures_and_decode_errors(monkeypatch):
    repo = SimpleNamespace()
    client = _FakeClient()

    monkeypatch.setattr(
        files_api,
        "get_file",
        lambda repo_row, path: SimpleNamespace(sha256="same", size=3),
    )
    assert await files_api.check_file_by_sha256(repo, "file.txt", "same", 3) is True
    assert await files_api.check_file_by_sha256(repo, "file.txt", "same", 4) is False

    monkeypatch.setattr(files_api, "get_lakefs_client", lambda: client)
    client.stat_result = {"size_bytes": 5}
    client.object_result = b"hello"
    assert (
        await files_api.check_file_by_sample(
            "alice/demo",
            "file.txt",
            "aGVsbG8=",
            5,
            "lakefs-repo",
            "main",
        )
        is True
    )

    client.object_error = RuntimeError("cannot read")
    assert (
        await files_api.check_file_by_sample(
            "alice/demo",
            "file.txt",
            "aGVsbG8=",
            5,
            "lakefs-repo",
            "main",
        )
        is False
    )

    client.object_error = None
    client.stat_error = RuntimeError("missing object")
    assert (
        await files_api.check_file_by_sample(
            "alice/demo",
            "file.txt",
            "aGVsbG8=",
            5,
            "lakefs-repo",
            "main",
        )
        is False
    )

    assert (
        await files_api.check_file_by_sample(
            "alice/demo",
            "file.txt",
            "%%%not-base64%%%",
            5,
            "lakefs-repo",
            "main",
        )
        is False
    )


@pytest.mark.asyncio
async def test_preupload_and_revision_cover_validation_quota_and_resolution_errors(
    monkeypatch,
):
    repo = SimpleNamespace(private=True, created_at=None, namespace="alice", downloads=1, likes_count=2)

    monkeypatch.setattr(files_api, "check_repo_write_permission", lambda repo_row, user: None)
    monkeypatch.setattr(files_api, "get_organization", lambda namespace: None)
    monkeypatch.setattr(files_api, "get_effective_lfs_threshold", lambda repo_row: 1024)
    monkeypatch.setattr(files_api, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(
        files_api,
        "process_preupload_file",
        _async_return({"path": "file.txt", "uploadMode": "regular", "shouldIgnore": False}),
    )
    monkeypatch.setattr(files_api.cfg.app, "debug_log_payloads", True)

    monkeypatch.setattr(files_api, "get_repository", lambda repo_type, namespace, name: None)
    with pytest.raises(HTTPException) as missing_repo:
        await files_api.preupload(
            files_api.RepoType.model,
            "alice",
            "demo",
            "main",
            _FakeRequest(body={"files": []}),
            user=SimpleNamespace(username="alice"),
        )
    assert missing_repo.value.status_code == 404

    monkeypatch.setattr(files_api, "get_repository", lambda repo_type, namespace, name: repo)
    with pytest.raises(HTTPException) as invalid_json:
        await files_api.preupload(
            files_api.RepoType.model,
            "alice",
            "demo",
            "main",
            _FakeRequest(error=RuntimeError("bad json")),
            user=SimpleNamespace(username="alice"),
        )
    assert invalid_json.value.status_code == 400

    with pytest.raises(HTTPException) as invalid_files:
        await files_api.preupload(
            files_api.RepoType.model,
            "alice",
            "demo",
            "main",
            _FakeRequest(body={"files": "bad"}),
            user=SimpleNamespace(username="alice"),
        )
    assert invalid_files.value.status_code == 400

    monkeypatch.setattr(
        files_api,
        "check_quota",
        lambda namespace, additional_bytes, is_private, is_org: (False, "too large"),
    )
    with pytest.raises(HTTPException) as quota_error:
        await files_api.preupload(
            files_api.RepoType.model,
            "alice",
            "demo",
            "main",
            _FakeRequest(body={"files": [{"path": "file.txt", "size": 5}]}),
            user=SimpleNamespace(username="alice"),
        )
    assert quota_error.value.status_code == 413

    monkeypatch.setattr(
        files_api,
        "check_quota",
        lambda namespace, additional_bytes, is_private, is_org: (True, None),
    )
    preupload = await files_api.preupload(
        files_api.RepoType.model,
        "alice",
        "demo",
        "main",
        _FakeRequest(body={"files": [{"path": "file.txt", "size": 5}]}),
        user=SimpleNamespace(username="alice"),
    )
    assert preupload["files"] == [
        {"path": "file.txt", "uploadMode": "regular", "shouldIgnore": False}
    ]

    monkeypatch.setattr(
        files_api,
        "hf_repo_not_found",
        lambda repo_id, repo_type: SimpleNamespace(status_code=404),
    )
    monkeypatch.setattr(
        files_api,
        "hf_revision_not_found",
        lambda repo_id, revision: SimpleNamespace(status_code=404, revision=revision),
    )
    monkeypatch.setattr(
        files_api,
        "hf_server_error",
        lambda message: SimpleNamespace(status_code=500, message=message),
    )
    monkeypatch.setattr(files_api, "check_repo_read_permission", lambda repo_row, user: None)
    monkeypatch.setattr(files_api, "get_repository", lambda repo_type, namespace, name: None)
    revision_not_found = await files_api.get_revision.__wrapped__(
        files_api.RepoType.model,
        "alice",
        "demo",
        "main",
        request=None,
        user=None,
    )
    assert revision_not_found.status_code == 404

    repo.created_at = None
    monkeypatch.setattr(files_api, "get_repository", lambda repo_type, namespace, name: repo)
    client = _FakeClient()
    monkeypatch.setattr(files_api, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(files_api, "safe_strftime", lambda value, fmt: "2024-01-01T00:00:00.000000Z")
    monkeypatch.setattr(files_api, "resolve_revision", _async_return(value=("commit-1", {"creation_date": 1})))
    success = await files_api.get_revision.__wrapped__(
        files_api.RepoType.model,
        "alice",
        "demo",
        "main",
        request=None,
        user=None,
    )
    assert success["sha"] == "commit-1"
    assert success["lastModified"] == files_api.datetime.fromtimestamp(1).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )

    async def _raise_value_error(*args, **kwargs):
        raise ValueError("missing revision")

    async def _raise_runtime_error(*args, **kwargs):
        raise RuntimeError("resolve failed")

    monkeypatch.setattr(files_api, "resolve_revision", _raise_value_error)
    missing_revision = await files_api.get_revision.__wrapped__(
        files_api.RepoType.model,
        "alice",
        "demo",
        "main",
        request=None,
        user=None,
    )
    assert missing_revision.revision == "main"

    monkeypatch.setattr(files_api, "resolve_revision", _raise_runtime_error)
    failed_revision = await files_api.get_revision.__wrapped__(
        files_api.RepoType.model,
        "alice",
        "demo",
        "main",
        request=None,
        user=None,
    )
    assert failed_revision.status_code == 500

    repo.private = True

    def _raise_unauthorized(repo_row, user):
        raise HTTPException(status_code=401, detail="auth required")

    monkeypatch.setattr(files_api, "check_repo_read_permission", _raise_unauthorized)
    hidden_private_revision = await files_api.get_revision.__wrapped__(
        files_api.RepoType.model,
        "alice",
        "demo",
        "main",
        request=None,
        user=None,
    )
    assert hidden_private_revision.status_code == 404

    def _raise_conflict(repo_row, user):
        raise HTTPException(status_code=409, detail="unexpected")

    monkeypatch.setattr(files_api, "check_repo_read_permission", _raise_conflict)
    with pytest.raises(HTTPException) as propagated_revision_error:
        await files_api.get_revision.__wrapped__(
            files_api.RepoType.model,
            "alice",
            "demo",
            "main",
            request=None,
            user=None,
        )
    assert propagated_revision_error.value.status_code == 409


@pytest.mark.asyncio
async def test_metadata_and_resolve_routes_cover_storage_backend_fallback_and_xet_headers(
    monkeypatch,
):
    repo = SimpleNamespace()
    client = _FakeClient()

    monkeypatch.setattr(files_api, "get_repository", lambda repo_type, namespace, name: repo)
    monkeypatch.setattr(files_api, "check_repo_read_permission", lambda repo_row, user: None)
    monkeypatch.setattr(files_api, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(files_api, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(files_api, "parse_s3_uri", lambda uri: ("bucket", "key/path.txt"))
    monkeypatch.setattr(
        files_api,
        "generate_download_presigned_url",
        _async_return("https://download.example.com/file"),
    )
    monkeypatch.setattr(
        files_api,
        "get_file",
        lambda repo_row, path: SimpleNamespace(sha256="sha256-value", lfs=True),
    )
    monkeypatch.setattr(files_api, "XET_ENABLE", True)
    monkeypatch.setattr(files_api.cfg.app, "base_url", "https://hub.example.com")

    monkeypatch.setattr(files_api, "get_repository", lambda repo_type, namespace, name: None)
    with pytest.raises(HTTPException) as missing_repo:
        await files_api._get_file_metadata("model", "alice", "demo", "main", "file.txt", None)
    assert missing_repo.value.status_code == 404
    assert missing_repo.value.headers["X-Error-Code"] == files_api.HFErrorCode.REPO_NOT_FOUND

    hidden_repo = SimpleNamespace(private=True)
    monkeypatch.setattr(files_api, "get_repository", lambda repo_type, namespace, name: hidden_repo)

    def _raise_forbidden(repo_row, user):
        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(files_api, "check_repo_read_permission", _raise_forbidden)
    with pytest.raises(HTTPException) as hidden_private_repo:
        await files_api._get_file_metadata("model", "alice", "demo", "main", "file.txt", None)
    assert hidden_private_repo.value.status_code == 404
    assert hidden_private_repo.value.headers["X-Error-Code"] == files_api.HFErrorCode.REPO_NOT_FOUND

    def _raise_conflict(repo_row, user):
        raise HTTPException(status_code=409, detail="unexpected")

    monkeypatch.setattr(files_api, "check_repo_read_permission", _raise_conflict)
    with pytest.raises(HTTPException) as propagated_permission_error:
        await files_api._get_file_metadata("model", "alice", "demo", "main", "file.txt", None)
    assert propagated_permission_error.value.status_code == 409

    repo = SimpleNamespace(private=False)
    monkeypatch.setattr(files_api, "get_repository", lambda repo_type, namespace, name: repo)
    monkeypatch.setattr(files_api, "check_repo_read_permission", lambda repo_row, user: None)

    client.stat_error = RuntimeError("missing file")
    with pytest.raises(HTTPException) as missing_file:
        await files_api._get_file_metadata("model", "alice", "demo", "main", "file.txt", None)
    assert missing_file.value.status_code == 404
    assert missing_file.value.headers["X-Error-Code"] == files_api.HFErrorCode.ENTRY_NOT_FOUND

    client.stat_error = None
    client.stat_result = {
        "physical_address": "memory://unsupported",
        "size_bytes": 5,
        "content_type": "text/plain",
        "mtime": 1,
    }
    client.branch_error = RuntimeError("not a branch")
    with pytest.raises(HTTPException) as unsupported_backend:
        await files_api._get_file_metadata("model", "alice", "demo", "commit-sha", "file.txt", None)
    assert unsupported_backend.value.status_code == 500

    client.branch_error = None
    client.branch_result = {"commit_id": "commit-sha"}
    client.stat_result = {
        "physical_address": "s3://bucket/key/path.txt",
        "size_bytes": 5,
        "content_type": "text/plain",
        "mtime": 1,
    }
    presigned_url, headers = await files_api._get_file_metadata(
        "model",
        "alice",
        "demo",
        "main",
        "folder/weights.bin",
        None,
    )
    assert presigned_url == "https://download.example.com/file"
    assert headers["X-Repo-Commit"] == "commit-sha"
    assert headers["X-Xet-hash"] == "sha256-value"
    assert headers["X-Xet-Refresh-Route"].endswith("/api/models/alice/demo/xet-read-token/main/weights.bin")

    monkeypatch.setattr(
        files_api,
        "_get_file_metadata",
        _async_return(
            (
                "https://download.example.com/file",
                {"X-Test": "value"},
            )
        ),
    )
    head_response = await files_api.resolve_file_head.__wrapped__(
        "model",
        "alice",
        "demo",
        "main",
        "file.txt",
        request=_FakeRequest(),
        user=None,
    )
    assert head_response.status_code == 200
    assert head_response.headers["x-test"] == "value"

    tracked_downloads = []
    monkeypatch.setattr(files_api, "get_repository", lambda repo_type, namespace, name: repo)
    monkeypatch.setattr(
        files_api,
        "get_or_create_tracking_cookie",
        lambda cookies, response_cookies: (
            response_cookies.setdefault(
                "hf_download_session",
                {
                    "value": "anon-session",
                    "max_age": 3600,
                    "httponly": True,
                    "samesite": "lax",
                },
            ),
            "anon-session",
        )[1],
    )

    async def _track_download_async(**kwargs):
        tracked_downloads.append((kwargs["session_id"], kwargs["file_path"]))

    monkeypatch.setattr(
        files_api,
        "track_download_async",
        _track_download_async,
    )
    redirect = await files_api.resolve_file_get.__wrapped__(
        "model",
        "alice",
        "demo",
        "main",
        "file.txt",
        request=_FakeRequest(cookies={}),
        user=None,
    )
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "https://download.example.com/file"
    assert "hf_download_session=anon-session" in redirect.headers["set-cookie"]
    await asyncio.sleep(0)
    assert tracked_downloads == [("anon-session", "file.txt")]

    auth_tracked_downloads = []
    async def _track_authenticated_download(**kwargs):
        auth_tracked_downloads.append(
            (kwargs["session_id"], kwargs["file_path"], kwargs["user"].username)
        )

    monkeypatch.setattr(files_api, "track_download_async", _track_authenticated_download)
    authenticated_redirect = await files_api.resolve_file_get.__wrapped__(
        "model",
        "alice",
        "demo",
        "main",
        "auth.txt",
        request=_FakeRequest(cookies={"session_id": "auth-session"}),
        user=SimpleNamespace(username="alice"),
    )
    assert authenticated_redirect.status_code == 302
    assert authenticated_redirect.headers["location"] == "https://download.example.com/file"
    assert "set-cookie" not in authenticated_redirect.headers
    await asyncio.sleep(0)
    assert auth_tracked_downloads == [("auth-session", "auth.txt", "alice")]

    monkeypatch.setattr(files_api, "get_repository", lambda repo_type, namespace, name: None)
    direct_redirect = await files_api.resolve_file_get.__wrapped__(
        "model",
        "alice",
        "demo",
        "main",
        "untracked.txt",
        request=_FakeRequest(cookies={}),
        user=None,
    )
    assert direct_redirect.status_code == 302
    assert direct_redirect.headers["location"] == "https://download.example.com/file"
