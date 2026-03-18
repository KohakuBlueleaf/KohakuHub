from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import Request

from kohakuhub.api import files
from kohakuhub.api.repo.routers import info
from kohakuhub.api.repo.utils.hf import format_hf_commit_hash


COMMIT_64 = "fced95226914d30386e0cb01fe4251d2b9c530079474f4815fc3e2fd709ef1c0"
COMMIT_40 = COMMIT_64[:40]


def make_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


class FakeLakeFSClient:
    async def get_branch(self, repository: str, branch: str) -> dict:
        return {"commit_id": COMMIT_64}

    async def get_commit(self, repository: str, commit_id: str) -> dict:
        return {"creation_date": 1_773_807_276}

    async def list_objects(
        self,
        repository: str,
        ref: str,
        prefix: str = "",
        delimiter: str = "",
        amount: int = 1000,
        after: str = "",
    ) -> dict:
        return {"results": [], "pagination": {"has_more": False}}

    async def stat_object(self, repository: str, ref: str, path: str) -> dict:
        return {
            "physical_address": "s3://bucket/path/config.json",
            "size_bytes": 453,
            "content_type": "application/json",
            "mtime": 1_773_807_276,
        }


class FakeField:
    def __eq__(self, other):
        return ("eq", other)

    def desc(self):
        return ("desc", self)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def where(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, value):
        return self.rows[:value]


@pytest.fixture
def repo_row():
    return SimpleNamespace(
        id=23,
        full_id="migo/mnist-lenet-arch",
        namespace="migo",
        private=False,
        downloads=2,
        likes_count=0,
        created_at=datetime(2026, 3, 18, 4, 14, 36),
    )


def test_format_hf_commit_hash():
    assert format_hf_commit_hash(COMMIT_64) == COMMIT_40
    assert format_hf_commit_hash(COMMIT_40) == COMMIT_40
    assert format_hf_commit_hash(None) is None


@pytest.mark.asyncio
async def test_get_revision_normalizes_commit_hash(monkeypatch, repo_row):
    monkeypatch.setattr(files, "get_repository", lambda *args, **kwargs: repo_row)
    monkeypatch.setattr(
        files, "check_repo_read_permission", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(files, "get_lakefs_client", lambda: FakeLakeFSClient())

    async def fake_resolve_revision(client, lakefs_repo, revision):
        return COMMIT_64, {"creation_date": 1_773_807_276}

    async def fake_build_revision_siblings(repo, lakefs_repo, commit_id):
        return [{"rfilename": "config.json", "size": 453}]

    monkeypatch.setattr(files, "resolve_revision", fake_resolve_revision)
    monkeypatch.setattr(files, "build_revision_siblings", fake_build_revision_siblings)

    response = await files.get_revision.__wrapped__(
        repo_type=files.RepoType.model,
        namespace="migo",
        name="mnist-lenet-arch",
        revision="main",
        request=make_request("/api/models/migo/mnist-lenet-arch/revision/main"),
        expand=None,
        fallback=False,
        user=None,
    )

    assert response["sha"] == COMMIT_40
    assert response["commit"]["oid"] == COMMIT_40


@pytest.mark.asyncio
async def test_get_file_metadata_normalizes_x_repo_commit(monkeypatch, repo_row):
    monkeypatch.setattr(files, "get_repository", lambda *args, **kwargs: repo_row)
    monkeypatch.setattr(
        files, "check_repo_read_permission", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(files, "get_lakefs_client", lambda: FakeLakeFSClient())
    monkeypatch.setattr(
        files,
        "get_file",
        lambda repo, path: SimpleNamespace(sha256="32257be9f767053b692b8f339c170196a695969b", lfs=False),
    )

    async def fake_generate_download_presigned_url(**kwargs):
        return "http://example.invalid/config.json"

    monkeypatch.setattr(
        files, "generate_download_presigned_url", fake_generate_download_presigned_url
    )

    _, headers = await files._get_file_metadata(
        repo_type="model",
        namespace="migo",
        name="mnist-lenet-arch",
        revision="main",
        path="config.json",
        user=None,
    )

    assert headers["X-Repo-Commit"] == COMMIT_40


@pytest.mark.asyncio
async def test_get_file_metadata_accepts_40_char_revision(monkeypatch, repo_row):
    class PrefixRevisionClient(FakeLakeFSClient):
        async def get_branch(self, repository: str, branch: str) -> dict:
            raise RuntimeError("branch lookup not available for direct commit ref")

    monkeypatch.setattr(files, "get_repository", lambda *args, **kwargs: repo_row)
    monkeypatch.setattr(
        files, "check_repo_read_permission", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(files, "get_lakefs_client", lambda: PrefixRevisionClient())
    monkeypatch.setattr(files, "get_file", lambda repo, path: None)

    async def fake_generate_download_presigned_url(**kwargs):
        return "http://example.invalid/config.json"

    monkeypatch.setattr(
        files, "generate_download_presigned_url", fake_generate_download_presigned_url
    )

    _, headers = await files._get_file_metadata(
        repo_type="model",
        namespace="migo",
        name="mnist-lenet-arch",
        revision=COMMIT_40,
        path="config.json",
        user=None,
    )

    assert headers["X-Repo-Commit"] == COMMIT_40


@pytest.mark.asyncio
async def test_get_repo_info_normalizes_commit_hash(monkeypatch, repo_row):
    monkeypatch.setattr(info, "get_repository", lambda *args, **kwargs: repo_row)
    monkeypatch.setattr(
        info, "check_repo_read_permission", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(info, "get_lakefs_client", lambda: FakeLakeFSClient())
    monkeypatch.setattr(info, "should_use_lfs", lambda *args, **kwargs: False)

    response = await info.get_repo_info.__wrapped__(
        namespace="migo",
        repo_name="mnist-lenet-arch",
        request=make_request("/api/models/migo/mnist-lenet-arch"),
        fallback=False,
        user=None,
    )

    assert response["sha"] == COMMIT_40


@pytest.mark.asyncio
async def test_list_repos_internal_normalizes_commit_hash(monkeypatch, repo_row):
    fake_repository_model = SimpleNamespace(
        select=lambda: FakeQuery([repo_row]),
        repo_type=FakeField(),
        namespace=FakeField(),
        likes_count=FakeField(),
        downloads=FakeField(),
        created_at=FakeField(),
        private=FakeField(),
    )

    monkeypatch.setattr(info, "Repository", fake_repository_model)
    monkeypatch.setattr(info, "_filter_repos_by_privacy", lambda q, user, author=None: q)
    monkeypatch.setattr(info, "get_lakefs_client", lambda: FakeLakeFSClient())

    response = await info._list_repos_internal("model", limit=10, user=None)

    assert response[0]["sha"] == COMMIT_40
