"""Tests for HuggingFace compatibility helpers."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

import kohakuhub.api.repo.utils.hf as hf_utils


def test_hf_error_helpers_return_header_only_responses():
    response = hf_utils.hf_error_response(
        418,
        hf_utils.HFErrorCode.BAD_REQUEST,
        "bad tea",
        headers={"X-Trace-Id": "abc"},
    )

    assert response.status_code == 418
    assert response.body == b""
    assert response.headers["x-error-code"] == hf_utils.HFErrorCode.BAD_REQUEST
    assert response.headers["x-error-message"] == "bad tea"
    assert response.headers["x-trace-id"] == "abc"


def test_hf_shortcuts_cover_repo_revision_entry_and_server_errors():
    repo_response = hf_utils.hf_repo_not_found("owner/repo", "dataset")
    gated_response = hf_utils.hf_gated_repo("owner/repo")
    revision_response = hf_utils.hf_revision_not_found("owner/repo", "dev")
    entry_response = hf_utils.hf_entry_not_found("owner/repo", "README.md", "dev")
    bad_request = hf_utils.hf_bad_request("bad input")
    server_error = hf_utils.hf_server_error("boom", error_code="CustomError")

    assert repo_response.headers["x-error-code"] == hf_utils.HFErrorCode.REPO_NOT_FOUND
    assert "dataset" in repo_response.headers["x-error-message"]
    assert gated_response.headers["x-error-code"] == hf_utils.HFErrorCode.GATED_REPO
    assert "accept the terms" in gated_response.headers["x-error-message"]
    assert revision_response.headers["x-error-code"] == hf_utils.HFErrorCode.REVISION_NOT_FOUND
    assert "dev" in revision_response.headers["x-error-message"]
    assert entry_response.headers["x-error-code"] == hf_utils.HFErrorCode.ENTRY_NOT_FOUND
    assert "README.md" in entry_response.headers["x-error-message"]
    assert bad_request.headers["x-error-code"] == hf_utils.HFErrorCode.BAD_REQUEST
    assert server_error.headers["x-error-code"] == "CustomError"


def test_hf_error_response_sanitizes_header_values_for_http_transport():
    response = hf_utils.hf_error_response(
        500,
        hf_utils.HFErrorCode.SERVER_ERROR,
        "line 1\nline 2\twith\tspacing",
        headers={"X-Debug": " debug\nvalue "},
    )

    assert response.headers["x-error-message"] == "line 1 line 2 with spacing"
    assert response.headers["x-debug"] == "debug value"


def test_format_hf_datetime_and_lakefs_error_classifiers(monkeypatch):
    seen = {}

    def fake_safe_strftime(value, fmt):
        seen["value"] = value
        seen["fmt"] = fmt
        return "2025-01-15T10:30:45.000000Z"

    monkeypatch.setattr("kohakuhub.utils.datetime_utils.safe_strftime", fake_safe_strftime)

    dt = datetime(2025, 1, 15, 10, 30, 45)

    assert hf_utils.format_hf_datetime(None) is None
    assert hf_utils.format_hf_datetime(dt) == "2025-01-15T10:30:45.000000Z"
    assert seen == {"value": dt, "fmt": "%Y-%m-%dT%H:%M:%S.%fZ"}
    assert hf_utils.is_lakefs_not_found_error(RuntimeError("404 missing")) is True
    assert hf_utils.is_lakefs_not_found_error(RuntimeError("permission denied")) is False
    assert hf_utils.is_lakefs_revision_error(RuntimeError("Unknown branch ref")) is True
    assert hf_utils.is_lakefs_revision_error(RuntimeError("totally different")) is False


@pytest.mark.asyncio
async def test_collect_hf_siblings_handles_pagination_and_lfs_metadata(monkeypatch):
    repo_row = SimpleNamespace()
    calls = []

    class _FakeClient:
        async def list_objects(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {
                    "results": [
                        {
                            "path_type": "object",
                            "path": "README.md",
                            "size_bytes": 4,
                            "checksum": "sha256:readme",
                        },
                        {
                            "path_type": "object",
                            "path": "weights.bin",
                            "size_bytes": 8,
                            "checksum": "sha256:weights",
                        },
                    ],
                    "pagination": {"has_more": True, "next_offset": "cursor-2"},
                }

            return {
                "results": [
                    {
                        "path_type": "object",
                        "path": "broken.bin",
                        "size_bytes": 9,
                        "checksum": "sha256:broken",
                    },
                    {
                        "path_type": "common_prefix",
                        "path": "subdir/",
                    },
                ],
                "pagination": {"has_more": False},
            }

    monkeypatch.setattr("kohakuhub.utils.lakefs.get_lakefs_client", lambda: _FakeClient())
    monkeypatch.setattr(
        "kohakuhub.utils.lakefs.lakefs_repo_name",
        lambda repo_type, repo_id: f"{repo_type}:{repo_id}",
    )
    monkeypatch.setattr(
        "kohakuhub.db_operations.should_use_lfs",
        lambda repo, path, size: path.endswith(".bin"),
    )
    monkeypatch.setattr(
        "kohakuhub.db_operations.get_file",
        lambda repo, path: (_ for _ in ()).throw(RuntimeError("db fail"))
        if path == "broken.bin"
        else SimpleNamespace(sha256="db-sha"),
    )

    siblings = await hf_utils.collect_hf_siblings(
        repo_row,
        "model",
        "alice/demo",
        "main",
    )

    assert calls == [
        {
            "repository": "model:alice/demo",
            "ref": "main",
            "prefix": "",
            "delimiter": "",
            "amount": 1000,
            "after": "",
        },
        {
            "repository": "model:alice/demo",
            "ref": "main",
            "prefix": "",
            "delimiter": "",
            "amount": 1000,
            "after": "cursor-2",
        },
    ]
    assert siblings == [
        {"rfilename": "README.md", "size": 4},
        {
            "rfilename": "weights.bin",
            "size": 8,
            "lfs": {"sha256": "db-sha", "size": 8, "pointerSize": 134},
        },
        {
            "rfilename": "broken.bin",
            "size": 9,
            "lfs": {"sha256": "sha256:broken", "size": 9, "pointerSize": 134},
        },
    ]


@pytest.mark.asyncio
async def test_collect_hf_siblings_accepts_list_payload_without_pagination(monkeypatch):
    class _FakeClient:
        async def list_objects(self, **kwargs):
            return [
                {
                    "path_type": "object",
                    "path": "config.json",
                    "size_bytes": 12,
                    "checksum": "sha256:config",
                }
            ]

    monkeypatch.setattr("kohakuhub.utils.lakefs.get_lakefs_client", lambda: _FakeClient())
    monkeypatch.setattr(
        "kohakuhub.utils.lakefs.lakefs_repo_name",
        lambda repo_type, repo_id: f"{repo_type}:{repo_id}",
    )
    monkeypatch.setattr("kohakuhub.db_operations.should_use_lfs", lambda repo, path, size: False)

    siblings = await hf_utils.collect_hf_siblings(
        SimpleNamespace(),
        "dataset",
        "alice/data",
        "dev",
    )

    assert siblings == [{"rfilename": "config.json", "size": 12}]


@pytest.mark.asyncio
async def test_collect_hf_siblings_stops_when_pagination_cursor_is_missing(monkeypatch):
    calls = []

    class _FakeClient:
        async def list_objects(self, **kwargs):
            calls.append(kwargs)
            return {
                "results": [
                    {
                        "path_type": "object",
                        "path": "weights.bin",
                        "size_bytes": 7,
                        "checksum": "sha256:weights",
                    }
                ],
                "pagination": {"has_more": True, "next_offset": None},
            }

    monkeypatch.setattr("kohakuhub.utils.lakefs.get_lakefs_client", lambda: _FakeClient())
    monkeypatch.setattr(
        "kohakuhub.utils.lakefs.lakefs_repo_name",
        lambda repo_type, repo_id: f"{repo_type}:{repo_id}",
    )
    monkeypatch.setattr("kohakuhub.db_operations.should_use_lfs", lambda repo, path, size: False)

    siblings = await hf_utils.collect_hf_siblings(
        SimpleNamespace(),
        "model",
        "alice/demo",
        "main",
    )

    assert len(calls) == 1
    assert siblings == [{"rfilename": "weights.bin", "size": 7}]
