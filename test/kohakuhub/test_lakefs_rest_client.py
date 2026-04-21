"""Unit tests for the LakeFS REST client."""

from __future__ import annotations

from collections import deque

import httpx
import pytest

import kohakuhub.lakefs_rest_client as lakefs_rest


def _response(method: str, url: str, *, status: int = 200, json_data=None, text: str | None = None, content: bytes | None = None):
    request = httpx.Request(method, url)
    kwargs = {}
    if json_data is not None:
        kwargs["json"] = json_data
    if text is not None:
        kwargs["content"] = text.encode()
    if content is not None:
        kwargs["content"] = content
    return httpx.Response(status, request=request, **kwargs)


class _AsyncClientFactory:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    def __call__(self, *args, **kwargs):
        factory = self

        class _Client:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

            async def get(self_inner, url, **kwargs):
                factory.calls.append(("GET", url, kwargs))
                return factory.responses.popleft()

            async def post(self_inner, url, **kwargs):
                factory.calls.append(("POST", url, kwargs))
                return factory.responses.popleft()

            async def put(self_inner, url, **kwargs):
                factory.calls.append(("PUT", url, kwargs))
                return factory.responses.popleft()

            async def delete(self_inner, url, **kwargs):
                factory.calls.append(("DELETE", url, kwargs))
                return factory.responses.popleft()

        return _Client()


@pytest.mark.asyncio
async def test_check_response_and_core_object_commit_methods(monkeypatch):
    client = lakefs_rest.LakeFSRestClient("https://lakefs.example.com/", "ak", "sk")

    error_response = _response(
        "GET",
        "https://lakefs.example.com/api/v1/repositories/repo/refs/main/objects",
        status=500,
        text="boom",
    )
    with pytest.raises(httpx.HTTPStatusError) as response_error:
        client._check_response(error_response)
    assert "LakeFS API error 500" in str(response_error.value)

    factory = _AsyncClientFactory(
        [
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/objects",
                content=b"file-bytes",
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/objects/stat",
                json_data={"path": "file.txt", "size_bytes": 5},
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/objects",
                json_data={"path": "file.txt"},
            ),
            _response(
                "PUT",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/staging/backing",
                json_data={"path": "weights.bin"},
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/commits",
                json_data={"id": "commit-1"},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/commits/commit-1",
                json_data={"id": "commit-1"},
            ),
        ]
    )
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    assert await client.get_object("repo", "main", "file.txt", range_header="bytes=0-9") == b"file-bytes"
    assert await client.stat_object("repo", "main", "file.txt") == {
        "path": "file.txt",
        "size_bytes": 5,
    }
    assert await client.upload_object("repo", "main", "file.txt", b"hello", force=True) == {
        "path": "file.txt"
    }

    staging_metadata = lakefs_rest.StagingMetadata(
        staging=lakefs_rest.StagingLocation(physical_address="s3://bucket/path"),
        checksum="etag",
        size_bytes=5,
        content_type="application/octet-stream",
    )
    assert await client.link_physical_address("repo", "main", "weights.bin", staging_metadata) == {
        "path": "weights.bin"
    }
    assert await client.commit("repo", "main", "Add file", metadata={"email": "alice@example.com"}) == {
        "id": "commit-1"
    }
    assert await client.get_commit("repo", "commit-1") == {"id": "commit-1"}

    get_call = factory.calls[0]
    assert get_call[2]["headers"] == {"Range": "bytes=0-9"}
    assert factory.calls[2][2]["params"] == {"path": "file.txt", "force": True}
    assert factory.calls[3][2]["json"]["checksum"] == "etag"
    assert factory.calls[4][2]["json"] == {
        "message": "Add file",
        "metadata": {"email": "alice@example.com"},
    }


@pytest.mark.asyncio
async def test_log_diff_list_repository_and_branch_methods(monkeypatch):
    client = lakefs_rest.LakeFSRestClient("https://lakefs.example.com", "ak", "sk")
    factory = _AsyncClientFactory(
        [
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/commits",
                json_data={"results": []},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/base/diff/head",
                json_data={"results": []},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/main/objects/ls",
                json_data={"results": []},
            ),
            _response(
                "DELETE",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/objects",
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories",
                json_data={"id": "repo"},
            ),
            _response(
                "DELETE",
                "https://lakefs.example.com/api/v1/repositories/repo",
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo",
                json_data={"id": "repo"},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo",
                status=404,
                text="missing",
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main",
                json_data={"id": "main", "commit_id": "commit-1"},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/branches",
                json_data={"results": []},
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/branches",
            ),
            _response(
                "DELETE",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/dev",
            ),
        ]
    )
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)

    assert await client.log_commits("repo", "main", after="cursor-1", amount=5) == {"results": []}
    assert await client.diff_refs("repo", "base", "head", after="cursor-2", amount=10) == {"results": []}
    assert await client.list_objects("repo", "main", prefix="data/", after="cursor-3", delimiter="/") == {
        "results": []
    }
    await client.delete_object("repo", "main", "file.txt", force=True)
    assert await client.create_repository("repo", "s3://bucket/repo", default_branch="main") == {"id": "repo"}
    await client.delete_repository("repo", force=True)
    assert await client.get_repository("repo") == {"id": "repo"}
    assert await client.repository_exists("repo") is False
    assert await client.get_branch("repo", "main") == {"id": "main", "commit_id": "commit-1"}
    assert await client.list_branches("repo", after="cursor-4", amount=20) == {"results": []}
    await client.create_branch("repo", "dev", "main")
    await client.delete_branch("repo", "dev", force=True)

    assert factory.calls[0][2]["params"] == {"after": "cursor-1", "amount": 5}
    assert factory.calls[1][2]["params"] == {"after": "cursor-2", "amount": 10}
    assert factory.calls[2][2]["params"] == {
        "amount": 1000,
        "prefix": "data/",
        "after": "cursor-3",
        "delimiter": "/",
    }
    assert factory.calls[3][2]["params"] == {"path": "file.txt", "force": True}
    assert factory.calls[4][2]["json"]["storage_namespace"] == "s3://bucket/repo"
    assert factory.calls[5][2]["params"] == {"force": True}
    assert factory.calls[9][2]["params"] == {"after": "cursor-4", "amount": 20}
    assert factory.calls[10][2]["json"] == {"name": "dev", "source": "main"}
    assert factory.calls[11][2]["params"] == {"force": True}


@pytest.mark.asyncio
async def test_tag_revert_merge_reset_and_factory_cover_optional_payloads(monkeypatch):
    client = lakefs_rest.LakeFSRestClient("https://lakefs.example.com", "ak", "sk")
    factory = _AsyncClientFactory(
        [
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/tags",
                json_data={"id": "v1"},
            ),
            _response(
                "GET",
                "https://lakefs.example.com/api/v1/repositories/repo/tags",
                json_data={"results": []},
            ),
            _response(
                "DELETE",
                "https://lakefs.example.com/api/v1/repositories/repo/tags/v1",
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/revert",
            ),
            _response(
                "POST",
                "https://lakefs.example.com/api/v1/repositories/repo/refs/feature/merge/main",
                json_data={"reference": "main"},
            ),
            _response(
                "PUT",
                "https://lakefs.example.com/api/v1/repositories/repo/branches/main/hard_reset",
            ),
        ]
    )
    monkeypatch.setattr(lakefs_rest.httpx, "AsyncClient", factory)
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "endpoint", "https://cfg-lakefs")
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "access_key", "cfg-ak")
    monkeypatch.setattr(lakefs_rest.cfg.lakefs, "secret_key", "cfg-sk")

    assert await client.create_tag("repo", "v1", "main", force=True) == {"id": "v1"}
    assert await client.list_tags("repo", after="cursor-5", amount=50) == {"results": []}
    await client.delete_tag("repo", "v1", force=True)
    await client.revert_branch(
        "repo",
        "main",
        "commit-1",
        parent_number=2,
        message="Revert commit",
        metadata={"email": "alice@example.com"},
        force=True,
        allow_empty=True,
    )
    assert await client.merge_into_branch(
        "repo",
        "feature",
        "main",
        message="Merge feature",
        metadata={"email": "alice@example.com"},
        strategy="source-wins",
        force=True,
        allow_empty=True,
        squash_merge=True,
    ) == {"reference": "main"}
    await client.hard_reset_branch("repo", "main", "commit-2", force=True)

    assert factory.calls[0][2]["json"] == {"id": "v1", "ref": "main", "force": True}
    assert factory.calls[1][2]["params"] == {"after": "cursor-5", "amount": 50}
    assert factory.calls[2][2]["params"] == {"force": True}
    assert factory.calls[3][2]["json"] == {
        "ref": "commit-1",
        "parent_number": 2,
        "force": True,
        "allow_empty": True,
        "commit_overrides": {
            "message": "Revert commit",
            "metadata": {"email": "alice@example.com"},
        },
    }
    assert factory.calls[4][2]["json"] == {
        "force": True,
        "allow_empty": True,
        "squash_merge": True,
        "message": "Merge feature",
        "metadata": {"email": "alice@example.com"},
        "strategy": "source-wins",
    }
    assert factory.calls[5][2]["params"] == {"ref": "commit-2", "force": True}

    configured_client = lakefs_rest.get_lakefs_rest_client()
    assert configured_client.endpoint == "https://cfg-lakefs"
    assert configured_client.auth == ("cfg-ak", "cfg-sk")
