"""Tests for Git LFS routes."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.git.routers.lfs as lfs_router


class _FakeRequest:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error

    async def json(self):
        if self.error:
            raise self.error
        return self.payload


def test_get_lfs_key_uses_balanced_directory_layout():
    oid = "0123456789abcdef" * 4

    assert lfs_router.get_lfs_key(oid) == f"lfs/{oid[:2]}/{oid[2:4]}/{oid}"


@pytest.mark.asyncio
async def test_process_upload_object_skips_existing_content(monkeypatch):
    monkeypatch.setattr(lfs_router, "object_exists", lambda bucket, key: _async_return(True))
    monkeypatch.setattr(lfs_router, "get_file_by_sha256", lambda oid: None)

    response = await lfs_router.process_upload_object("a" * 64, 123, "owner/repo")

    assert response.actions is None
    assert response.error is None


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_process_upload_object_supports_multipart_and_single_part(monkeypatch):
    multipart_seen = {}
    single_seen = {}
    monkeypatch.setattr(lfs_router.cfg.s3, "bucket", "hub-storage")
    monkeypatch.setattr(lfs_router.cfg.app, "base_url", "https://hub.example.com")
    monkeypatch.setattr(lfs_router, "object_exists", lambda bucket, key: _async_return(False))
    monkeypatch.setattr(lfs_router, "get_file_by_sha256", lambda oid: None)
    monkeypatch.setattr(lfs_router, "get_multipart_threshold", lambda: 10)
    monkeypatch.setattr(lfs_router, "get_multipart_chunk_size", lambda: 4)

    async def fake_generate_multipart_upload_urls(bucket, key, part_count, expires_in):
        multipart_seen.update(
            {
                "bucket": bucket,
                "key": key,
                "part_count": part_count,
                "expires_in": expires_in,
            }
        )
        return {
            "upload_id": "upload-1",
            "expires_at": "2025-01-01T00:00:00.000000Z",
            "part_urls": [
                {"part_number": 1, "url": "https://upload/1"},
                {"part_number": 2, "url": "https://upload/2"},
                {"part_number": 3, "url": "https://upload/3"},
            ],
        }

    async def fake_generate_upload_presigned_url(
        bucket, key, expires_in, content_type=None, checksum_sha256=None
    ):
        single_seen.update(
            {
                "bucket": bucket,
                "key": key,
                "expires_in": expires_in,
                "content_type": content_type,
                "checksum": checksum_sha256,
            }
        )
        return {
            "url": "https://upload/single",
            "expires_at": "2025-01-01T00:00:00.000000Z",
            "headers": {"x-amz-checksum-sha256": checksum_sha256},
        }

    monkeypatch.setattr(
        lfs_router, "generate_multipart_upload_urls", fake_generate_multipart_upload_urls
    )
    monkeypatch.setattr(
        lfs_router, "generate_upload_presigned_url", fake_generate_upload_presigned_url
    )

    multipart_response = await lfs_router.process_upload_object("b" * 64, 11, "owner/repo")
    single_response = await lfs_router.process_upload_object(
        "c" * 64, 9, "owner/repo", is_browser=True
    )

    assert multipart_seen["part_count"] == 3
    assert multipart_response.actions["upload"]["header"]["chunk_size"] == "4"
    assert multipart_response.actions["upload"]["header"]["1"] == "https://upload/1"
    assert multipart_response.actions["verify"]["href"].endswith("/api/owner/repo.git/info/lfs/verify")
    assert single_seen["content_type"] == "application/octet-stream"
    assert single_seen["checksum"] == base64.b64encode(bytes.fromhex("c" * 64)).decode("utf-8")
    assert single_response.actions["upload"]["href"] == "https://upload/single"


@pytest.mark.asyncio
async def test_process_upload_object_returns_error_when_presign_fails(monkeypatch):
    monkeypatch.setattr(lfs_router, "object_exists", lambda bucket, key: _async_return(False))
    monkeypatch.setattr(lfs_router, "get_file_by_sha256", lambda oid: None)
    monkeypatch.setattr(lfs_router, "get_multipart_threshold", lambda: 100)

    async def broken_upload(**_kwargs):
        raise RuntimeError("presign failed")

    monkeypatch.setattr(lfs_router, "generate_upload_presigned_url", broken_upload)

    response = await lfs_router.process_upload_object("d" * 64, 50, "owner/repo")

    assert response.error.code == 500
    assert "presign failed" in response.error.message


@pytest.mark.asyncio
async def test_process_download_object_covers_missing_success_and_failure(monkeypatch):
    monkeypatch.setattr(lfs_router.cfg.s3, "bucket", "hub-storage")
    monkeypatch.setattr(lfs_router, "get_file_by_sha256", lambda oid: None)

    missing = await lfs_router.process_download_object("e" * 64, 123)
    assert missing.error.code == 404

    monkeypatch.setattr(lfs_router, "get_file_by_sha256", lambda oid: SimpleNamespace(size=123))
    monkeypatch.setattr(
        lfs_router,
        "generate_download_presigned_url",
        lambda bucket, key, expires_in: _async_return("https://download/object"),
    )
    success = await lfs_router.process_download_object("e" * 64, 123)
    assert success.actions["download"]["href"] == "https://download/object"

    async def broken_download(**_kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr(lfs_router, "generate_download_presigned_url", broken_download)
    failure = await lfs_router.process_download_object("e" * 64, 123)
    assert failure.error.code == 500


@pytest.mark.asyncio
async def test_lfs_batch_validates_payload_auth_quota_and_operations(monkeypatch):
    repo = SimpleNamespace(private=True)
    writer = SimpleNamespace(username="owner")
    permission_calls = []
    upload_calls = []
    download_calls = []

    monkeypatch.setattr(lfs_router, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(lfs_router, "get_organization", lambda namespace: SimpleNamespace() if namespace == "org" else None)
    monkeypatch.setattr(lfs_router, "check_repo_write_permission", lambda repo_arg, user_arg: permission_calls.append(("write", repo_arg, user_arg)))
    monkeypatch.setattr(lfs_router, "check_repo_read_permission", lambda repo_arg, user_arg: permission_calls.append(("read", repo_arg, user_arg)))
    monkeypatch.setattr(lfs_router, "check_quota", lambda namespace, total_bytes, is_private, is_org: (True, None))

    async def fake_process_upload_object(oid, size, repo_id, is_browser):
        upload_calls.append((oid, size, repo_id, is_browser))
        return lfs_router.LFSObjectResponse(oid=oid, size=size, authenticated=True)

    async def fake_process_download_object(oid, size):
        download_calls.append((oid, size))
        return lfs_router.LFSObjectResponse(oid=oid, size=size, authenticated=True)

    monkeypatch.setattr(lfs_router, "process_upload_object", fake_process_upload_object)
    monkeypatch.setattr(lfs_router, "process_download_object", fake_process_download_object)

    with pytest.raises(HTTPException) as invalid_body:
        await lfs_router.lfs_batch("owner", "repo", request=_FakeRequest(error=ValueError("bad json")))

    assert invalid_body.value.status_code == 400

    with pytest.raises(HTTPException) as missing_auth:
        await lfs_router.lfs_batch(
            "owner",
            "repo",
            request=_FakeRequest({"operation": "upload", "objects": [{"oid": "a" * 64, "size": 3}]}),
            user=None,
        )

    assert missing_auth.value.status_code == 401

    monkeypatch.setattr(lfs_router, "check_quota", lambda namespace, total_bytes, is_private, is_org: (False, "too big"))
    with pytest.raises(HTTPException) as quota_error:
        await lfs_router.lfs_batch(
            "org",
            "repo",
            request=_FakeRequest({"operation": "upload", "objects": [{"oid": "a" * 64, "size": 3}]}),
            user=writer,
        )

    assert quota_error.value.status_code == 413

    monkeypatch.setattr(lfs_router, "check_quota", lambda namespace, total_bytes, is_private, is_org: (True, None))
    upload_response = await lfs_router.lfs_batch(
        "owner",
        "repo",
        request=_FakeRequest(
            {
                "operation": "upload",
                "objects": [{"oid": "a" * 64, "size": 3}],
                "is_browser": True,
            }
        ),
        user=writer,
    )
    download_response = await lfs_router.lfs_batch(
        "owner",
        "repo",
        request=_FakeRequest({"operation": "download", "objects": [{"oid": "b" * 64, "size": 4}]}),
        user=None,
    )
    unknown_response = await lfs_router.lfs_batch(
        "owner",
        "repo",
        request=_FakeRequest({"operation": "unknown", "objects": [{"oid": "c" * 64, "size": 5}]}),
        user=writer,
    )

    assert upload_response.status_code == 200
    assert upload_calls == [("a" * 64, 3, "owner/repo", True)]
    assert download_calls == [("b" * 64, 4)]
    assert permission_calls[0][0] == "write"
    assert permission_calls[-1][0] == "read"
    assert unknown_response.body


@pytest.mark.asyncio
async def test_lfs_complete_multipart_validates_and_completes_upload(monkeypatch):
    monkeypatch.setattr(lfs_router.cfg.s3, "bucket", "hub-storage")
    seen = {}

    async def fake_complete_multipart_upload(bucket, key, upload_id, parts):
        seen["complete"] = {
            "bucket": bucket,
            "key": key,
            "upload_id": upload_id,
            "parts": parts,
        }
        return {"etag": "abc"}

    async def fake_get_object_metadata(bucket, key):
        return {"size": 12, "etag": "etag-1"}

    monkeypatch.setattr(lfs_router, "complete_multipart_upload", fake_complete_multipart_upload)
    monkeypatch.setattr(lfs_router, "get_object_metadata", fake_get_object_metadata)

    with pytest.raises(HTTPException) as invalid_json:
        await lfs_router.lfs_complete_multipart("owner", "repo", _FakeRequest(error=ValueError("boom")))
    assert invalid_json.value.status_code == 400

    with pytest.raises(HTTPException) as missing_fields:
        await lfs_router.lfs_complete_multipart("owner", "repo", _FakeRequest({"oid": "a" * 64}))
    assert missing_fields.value.status_code == 400

    with pytest.raises(HTTPException) as invalid_part:
        await lfs_router.lfs_complete_multipart(
            "owner",
            "repo",
            _FakeRequest({"oid": "a" * 64, "upload_id": "u1", "parts": [{"bad": 1}]}),
        )
    assert invalid_part.value.status_code == 400

    response = await lfs_router.lfs_complete_multipart(
        "owner",
        "repo",
        _FakeRequest(
            {
                "oid": "a" * 64,
                "size": 12,
                "upload_id": "u1",
                "parts": [{"partNumber": 1, "etag": "etag-1"}],
            }
        ),
    )

    assert response["etag"] == "etag-1"
    assert seen["complete"]["parts"] == [{"PartNumber": 1, "ETag": "etag-1"}]

    async def mismatched_metadata(bucket, key):
        return {"size": 99, "etag": "etag-1"}

    monkeypatch.setattr(lfs_router, "get_object_metadata", mismatched_metadata)
    with pytest.raises(HTTPException) as size_mismatch:
        await lfs_router.lfs_complete_multipart(
            "owner",
            "repo",
            _FakeRequest(
                {
                    "oid": "a" * 64,
                    "size": 12,
                    "upload_id": "u1",
                    "parts": [{"PartNumber": 1, "ETag": "etag-1"}],
                }
            ),
        )

    assert size_mismatch.value.status_code == 500


@pytest.mark.asyncio
async def test_lfs_verify_covers_validation_completion_and_size_checks(monkeypatch):
    monkeypatch.setattr(lfs_router.cfg.s3, "bucket", "hub-storage")
    warnings = []

    async def fake_complete_multipart_upload(bucket, key, upload_id, parts):
        return {"ok": True}

    async def fake_object_exists(bucket, key):
        return True

    async def fake_get_object_metadata(bucket, key):
        return {"size": 12}

    monkeypatch.setattr(lfs_router, "complete_multipart_upload", fake_complete_multipart_upload)
    monkeypatch.setattr(lfs_router, "object_exists", fake_object_exists)
    monkeypatch.setattr(lfs_router, "get_object_metadata", fake_get_object_metadata)
    monkeypatch.setattr(lfs_router.logger, "warning", lambda message: warnings.append(message))

    with pytest.raises(HTTPException) as invalid_json:
        await lfs_router.lfs_verify("owner", "repo", _FakeRequest(error=ValueError("bad")))
    assert invalid_json.value.status_code == 400

    with pytest.raises(HTTPException) as missing_oid:
        await lfs_router.lfs_verify("owner", "repo", _FakeRequest({}))
    assert missing_oid.value.status_code == 400

    result = await lfs_router.lfs_verify(
        "owner",
        "repo",
        _FakeRequest(
            {
                "oid": "a" * 64,
                "size": 12,
                "upload_id": "u1",
                "parts": [{"PartNumber": 1, "ETag": "etag-1"}],
            }
        ),
    )
    assert result["message"] == "Object verified successfully"

    monkeypatch.setattr(lfs_router, "object_exists", lambda bucket, key: _async_return(False))
    with pytest.raises(HTTPException) as missing_object:
        await lfs_router.lfs_verify("owner", "repo", _FakeRequest({"oid": "a" * 64}))
    assert missing_object.value.status_code == 404

    monkeypatch.setattr(lfs_router, "object_exists", fake_object_exists)
    monkeypatch.setattr(lfs_router, "get_object_metadata", lambda bucket, key: _async_return({"size": 99}))
    with pytest.raises(HTTPException) as size_mismatch:
        await lfs_router.lfs_verify(
            "owner",
            "repo",
            _FakeRequest({"oid": "a" * 64, "size": 12}),
        )
    assert size_mismatch.value.status_code == 400

    async def broken_metadata(bucket, key):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(lfs_router, "get_object_metadata", broken_metadata)
    success = await lfs_router.lfs_verify(
        "owner",
        "repo",
        _FakeRequest({"oid": "a" * 64, "size": 12}),
    )
    assert success["message"] == "Object verified successfully"
    assert any("Failed to verify size" in warning for warning in warnings)
