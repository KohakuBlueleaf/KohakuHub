"""Unit tests for statistics routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.stats as stats_api


class _Expr:
    def __and__(self, other):
        return self


class _Field:
    def __eq__(self, other):
        return _Expr()

    def __ge__(self, other):
        return _Expr()

    def __le__(self, other):
        return _Expr()


class _Query:
    def __init__(self, items):
        self.items = list(items)

    def where(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self.items)


class _FrozenDatetime:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 4, 20, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_get_recent_stats_returns_hf_not_found_when_repository_is_missing(monkeypatch):
    monkeypatch.setattr(stats_api, "get_repository", lambda *args: None)
    monkeypatch.setattr(
        stats_api,
        "hf_repo_not_found",
        lambda repo_id, repo_type: {"error": "missing", "repo_id": repo_id, "repo_type": repo_type},
    )

    response = await stats_api.get_recent_stats("model", "owner", "missing", days=14)

    assert response == {
        "error": "missing",
        "repo_id": "owner/missing",
        "repo_type": "model",
    }


@pytest.mark.asyncio
async def test_get_trending_repositories_filters_missing_mismatched_and_inaccessible_repos(
    monkeypatch,
):
    repo_public = SimpleNamespace(
        full_id="owner/public",
        repo_type="model",
        downloads=10,
        likes_count=2,
        private=False,
    )
    repo_private = SimpleNamespace(
        full_id="owner/private",
        repo_type="model",
        downloads=8,
        likes_count=1,
        private=True,
    )
    repo_results = [repo_public, repo_private, None]
    stats_rows = [
        SimpleNamespace(repository=SimpleNamespace(id=1), download_sessions=7),
        SimpleNamespace(repository=SimpleNamespace(id=1), download_sessions=3),
        SimpleNamespace(repository=SimpleNamespace(id=2), download_sessions=5),
        SimpleNamespace(repository=SimpleNamespace(id=3), download_sessions=4),
    ]

    class _FakeDailyRepoStats:
        repository = _Field()
        download_sessions = _Field()
        date = _Field()

        @staticmethod
        def select(*args):
            return _Query(stats_rows)

    class _FakeRepository:
        id = _Field()
        repo_type = _Field()

        @staticmethod
        def get_or_none(expr):
            return repo_results.pop(0)

    def _check_repo_read_permission(repo, user):
        if repo.private:
            raise HTTPException(status_code=403, detail="forbidden")
        return True

    monkeypatch.setattr(stats_api, "datetime", _FrozenDatetime)
    monkeypatch.setattr(stats_api, "DailyRepoStats", _FakeDailyRepoStats)
    monkeypatch.setattr(stats_api, "Repository", _FakeRepository)
    monkeypatch.setattr(stats_api, "check_repo_read_permission", _check_repo_read_permission)

    response = await stats_api.get_trending_repositories(
        repo_type="model",
        days=7,
        limit=10,
        user=SimpleNamespace(username="owner"),
    )

    assert response["trending"] == [
        {
            "id": "owner/public",
            "type": "model",
            "downloads": 10,
            "likes": 2,
            "recent_downloads": 10,
            "private": False,
        }
    ]
    assert response["period"]["days"] == 7
