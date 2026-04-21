"""Tests for XET file lookup helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.xet.utils.file_lookup as file_lookup


class _Field:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return (self.name, "==", other)


def test_lookup_file_by_sha256_returns_repo_and_file(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    file_record = SimpleNamespace(repository=repo)
    seen = {}

    class FakeFile:
        sha256 = _Field("sha256")
        is_deleted = _Field("is_deleted")

        @staticmethod
        def get_or_none(*args):
            seen["args"] = args
            return file_record

    monkeypatch.setattr(file_lookup, "File", FakeFile)

    actual_repo, actual_file = file_lookup.lookup_file_by_sha256("abc123")

    assert actual_repo is repo
    assert actual_file is file_record
    assert seen["args"] == (("sha256", "==", "abc123"), ("is_deleted", "==", False))


def test_lookup_file_by_sha256_raises_for_missing_file(monkeypatch):
    class FakeFile:
        sha256 = _Field("sha256")
        is_deleted = _Field("is_deleted")

        @staticmethod
        def get_or_none(*_args):
            return None

    monkeypatch.setattr(file_lookup, "File", FakeFile)

    with pytest.raises(HTTPException) as exc_info:
        file_lookup.lookup_file_by_sha256("deadbeef")

    assert exc_info.value.status_code == 404
    assert "deadbeef" in exc_info.value.detail["error"]


def test_check_file_read_permission_delegates_to_repo_permission(monkeypatch):
    seen = {}

    def fake_check_repo_read_permission(repo, user):
        seen["repo"] = repo
        seen["user"] = user

    monkeypatch.setattr(file_lookup, "check_repo_read_permission", fake_check_repo_read_permission)

    repo = SimpleNamespace(full_id="owner/repo")
    user = SimpleNamespace(username="owner")

    file_lookup.check_file_read_permission(repo, user)

    assert seen == {"repo": repo, "user": user}
