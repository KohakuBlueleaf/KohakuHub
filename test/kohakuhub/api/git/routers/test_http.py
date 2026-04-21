"""Tests for Git Smart HTTP routes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.git.routers.http as git_http


class _FakeRequest:
    def __init__(self, body: bytes):
        self._body = body

    async def body(self) -> bytes:
        return self._body


def test_get_user_from_git_auth_handles_missing_invalid_and_active_users(monkeypatch):
    user = SimpleNamespace(is_active=True, username="owner")
    token = SimpleNamespace(id=3, user=user)
    seen = {}

    class FakeUpdateQuery:
        def __init__(self):
            self.where_args = None

        def where(self, *args):
            self.where_args = args
            seen["where_args"] = args
            return self

        def execute(self):
            seen["executed"] = True
            return 1

    class FakeField:
        def __eq__(self, other):
            return ("eq", other)

    class FakeTokenModel:
        token_hash = FakeField()
        id = FakeField()

        @staticmethod
        def get_or_none(*args):
            seen["lookup"] = args
            return token

        @staticmethod
        def update(**kwargs):
            seen["update_kwargs"] = kwargs
            return FakeUpdateQuery()

    monkeypatch.setattr(git_http, "parse_git_credentials", lambda header: ("owner", "secret") if header else (None, None))
    monkeypatch.setattr(git_http, "hash_token", lambda token_str: f"hash:{token_str}")
    monkeypatch.setattr(git_http, "Token", FakeTokenModel)

    assert git_http.get_user_from_git_auth(None) is None
    assert git_http.get_user_from_git_auth("Basic xxx") is user
    assert seen["lookup"] == (("eq", "hash:secret"),)
    assert seen["executed"] is True

    inactive_user = SimpleNamespace(is_active=False, username="owner")
    token.user = inactive_user
    assert git_http.get_user_from_git_auth("Basic xxx") is None


@pytest.mark.asyncio
async def test_git_info_refs_handles_upload_and_receive_services(monkeypatch):
    repo = SimpleNamespace(repo_type="dataset", private=False)
    user = SimpleNamespace(username="owner")
    seen = {"handlers": []}

    monkeypatch.setattr(
        git_http,
        "get_repository",
        lambda repo_type, namespace, name: repo if repo_type == "dataset" else None,
    )
    monkeypatch.setattr(git_http, "get_user_from_git_auth", lambda authorization: user)
    monkeypatch.setattr(git_http, "check_repo_read_permission", lambda repo_arg, user_arg: seen.setdefault("read", []).append((repo_arg, user_arg)))
    monkeypatch.setattr(git_http, "check_repo_write_permission", lambda repo_arg, user_arg: seen.setdefault("write", []).append((repo_arg, user_arg)))

    class FakeBridge:
        def __init__(self, repo_type, namespace, name):
            seen["bridge_args"] = (repo_type, namespace, name)

        async def get_refs(self, branch="main"):
            seen["branch"] = branch
            return {"HEAD": "1" * 40}

    class FakeUploadHandler:
        def __init__(self, repo_id):
            seen["handlers"].append(("upload", repo_id))

        def get_service_info(self, refs):
            seen["upload_refs"] = refs
            return b"upload-info"

    class FakeReceiveHandler:
        def __init__(self, repo_id):
            seen["handlers"].append(("receive", repo_id))

        def get_service_info(self, refs):
            seen["receive_refs"] = refs
            return b"receive-info"

    monkeypatch.setattr(git_http, "GitLakeFSBridge", FakeBridge)
    monkeypatch.setattr(git_http, "GitUploadPackHandler", FakeUploadHandler)
    monkeypatch.setattr(git_http, "GitReceivePackHandler", FakeReceiveHandler)

    upload_response = await git_http.git_info_refs("owner", "repo", "git-upload-pack", authorization="Basic x")
    receive_response = await git_http.git_info_refs("owner", "repo", "git-receive-pack", authorization="Basic x")

    assert upload_response.body == b"upload-info"
    assert upload_response.media_type == "application/x-git-upload-pack-advertisement"
    assert receive_response.body == b"receive-info"
    assert seen["bridge_args"] == ("dataset", "owner", "repo")
    assert seen["read"] == [(repo, user)]
    assert seen["write"] == [(repo, user)]


@pytest.mark.asyncio
async def test_git_info_refs_rejects_missing_repo_unknown_service_and_unauthenticated_push(monkeypatch):
    monkeypatch.setattr(git_http, "get_repository", lambda *_args: None)

    with pytest.raises(HTTPException) as not_found:
        await git_http.git_info_refs("owner", "repo", "git-upload-pack")

    assert not_found.value.status_code == 404

    repo = SimpleNamespace(repo_type="model", private=False)
    monkeypatch.setattr(git_http, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(git_http, "get_user_from_git_auth", lambda authorization: None)
    monkeypatch.setattr(git_http, "check_repo_read_permission", lambda repo_arg, user_arg: None)

    with pytest.raises(HTTPException) as unknown_service:
        await git_http.git_info_refs("owner", "repo", "git-bad-service")

    assert unknown_service.value.status_code == 400

    with pytest.raises(HTTPException) as missing_auth:
        await git_http.git_info_refs("owner", "repo", "git-receive-pack")

    assert missing_auth.value.status_code == 401


@pytest.mark.asyncio
async def test_git_upload_pack_receive_pack_and_head_use_expected_handlers(monkeypatch):
    repo = SimpleNamespace(repo_type="space", private=False)
    user = SimpleNamespace(username="owner")
    seen = {}

    monkeypatch.setattr(
        git_http,
        "get_repository",
        lambda repo_type, namespace, name: repo if repo_type == "space" else None,
    )
    monkeypatch.setattr(git_http, "get_user_from_git_auth", lambda authorization: user)
    monkeypatch.setattr(git_http, "check_repo_read_permission", lambda repo_arg, user_arg: seen.setdefault("read", []).append((repo_arg, user_arg)))
    monkeypatch.setattr(git_http, "check_repo_write_permission", lambda repo_arg, user_arg: seen.setdefault("write", []).append((repo_arg, user_arg)))

    class FakeBridge:
        def __init__(self, repo_type, namespace, name):
            seen["bridge_args"] = (repo_type, namespace, name)

    class FakeUploadHandler:
        def __init__(self, repo_id, bridge):
            seen["upload_handler"] = (repo_id, bridge.__class__.__name__)

        async def handle_upload_pack(self, request_body):
            seen["upload_body"] = request_body
            return b"upload-pack-result"

    class FakeReceiveHandler:
        def __init__(self, repo_id):
            seen["receive_handler"] = repo_id

        async def handle_receive_pack(self, request_body):
            seen["receive_body"] = request_body
            return b"receive-pack-result"

    monkeypatch.setattr(git_http, "GitLakeFSBridge", FakeBridge)
    monkeypatch.setattr(git_http, "GitUploadPackHandler", FakeUploadHandler)
    monkeypatch.setattr(git_http, "GitReceivePackHandler", FakeReceiveHandler)

    upload_response = await git_http.git_upload_pack(
        "owner",
        "repo",
        request=_FakeRequest(b"want main"),
        authorization="Basic x",
    )
    receive_response = await git_http.git_receive_pack(
        "owner",
        "repo",
        request=_FakeRequest(b"push refs"),
        authorization="Basic x",
    )
    head_response = await git_http.git_head("owner", "repo", authorization="Basic x")

    assert upload_response.body == b"upload-pack-result"
    assert receive_response.body == b"receive-pack-result"
    assert head_response.body == b"ref: refs/heads/main\n"
    assert seen["upload_body"] == b"want main"
    assert seen["receive_body"] == b"push refs"
    assert seen["bridge_args"] == ("space", "owner", "repo")


@pytest.mark.asyncio
async def test_git_receive_pack_requires_authentication(monkeypatch):
    repo = SimpleNamespace(repo_type="model", private=False)
    monkeypatch.setattr(git_http, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(git_http, "get_user_from_git_auth", lambda authorization: None)

    with pytest.raises(HTTPException) as exc_info:
        await git_http.git_receive_pack("owner", "repo", request=_FakeRequest(b"data"))

    assert exc_info.value.status_code == 401
