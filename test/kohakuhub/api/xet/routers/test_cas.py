"""Tests for XET CAS reconstruction routes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.xet.routers.cas as cas_router


def test_generate_chunked_reconstruction_handles_empty_single_and_multi_chunk_files():
    empty = cas_router._generate_chunked_reconstruction("a" * 64, 0, "https://example.com")
    single = cas_router._generate_chunked_reconstruction("b" * 64, 128, "https://example.com")
    multi = cas_router._generate_chunked_reconstruction(
        "c" * 64,
        cas_router.CHUNK_SIZE_BYTES * 2 + 7,
        "https://example.com",
    )

    assert empty["terms"][0]["unpacked_length"] == 0
    assert single["terms"][0]["hash"] == "b" * 64
    assert len(multi["terms"]) == 3
    assert multi["fetch_info"][multi["terms"][0]["hash"]][0]["url_range"]["start"] == 0
    assert multi["fetch_info"][multi["terms"][-1]["hash"]][0]["url_range"]["end"] == cas_router.CHUNK_SIZE_BYTES * 2 + 6


@pytest.mark.asyncio
async def test_get_reconstruction_returns_chunked_response(monkeypatch):
    repo = SimpleNamespace(repo_type="model", full_id="owner/repo")
    file_record = SimpleNamespace(path_in_repo="weights/model.bin", size=42, sha256="f" * 64)
    seen = {}

    class FakeClient:
        async def stat_object(self, repository, ref, path):
            seen["stat_object"] = {"repository": repository, "ref": ref, "path": path}
            return {"physical_address": "s3://bucket/path/to/object"}

    monkeypatch.setattr(cas_router, "lookup_file_by_sha256", lambda file_id: (repo, file_record))
    monkeypatch.setattr(cas_router, "check_file_read_permission", lambda repo_arg, user: seen.setdefault("permission", (repo_arg, user)))
    monkeypatch.setattr(cas_router, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(cas_router, "get_lakefs_client", lambda: FakeClient())
    monkeypatch.setattr(cas_router, "parse_s3_uri", lambda uri: ("bucket", "path/to/object"))

    async def fake_presigned(bucket, key, expires_in, filename):
        seen["presigned"] = {
            "bucket": bucket,
            "key": key,
            "expires_in": expires_in,
            "filename": filename,
        }
        return "https://download.example.com/object"

    monkeypatch.setattr(cas_router, "generate_download_presigned_url", fake_presigned)

    response = await cas_router.get_reconstruction("f" * 64, user=SimpleNamespace(username="owner"))
    payload = json.loads(response.body.decode("utf-8"))

    assert response.media_type == "application/json"
    assert seen["stat_object"] == {
        "repository": "model:owner/repo",
        "ref": "main",
        "path": "weights/model.bin",
    }
    assert seen["presigned"]["filename"] == "model.bin"
    assert payload["terms"][0]["unpacked_length"] == 42


@pytest.mark.asyncio
async def test_get_reconstruction_raises_not_found_when_lakefs_lookup_fails(monkeypatch):
    repo = SimpleNamespace(repo_type="model", full_id="owner/repo")
    file_record = SimpleNamespace(path_in_repo="weights/model.bin", size=42, sha256="f" * 64)

    class FakeClient:
        async def stat_object(self, **_kwargs):
            raise RuntimeError("missing")

    monkeypatch.setattr(cas_router, "lookup_file_by_sha256", lambda file_id: (repo, file_record))
    monkeypatch.setattr(cas_router, "check_file_read_permission", lambda repo_arg, user: None)
    monkeypatch.setattr(cas_router, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(cas_router, "get_lakefs_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await cas_router.get_reconstruction("f" * 64, user=None)

    assert exc_info.value.status_code == 404
    assert "File not found in repository" in exc_info.value.detail["error"]
