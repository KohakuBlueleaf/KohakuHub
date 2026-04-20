"""Tests for HuggingFace compatibility helpers."""

from __future__ import annotations

from datetime import datetime

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
