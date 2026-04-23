"""Tests for download tracking helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import kohakuhub.api.utils.downloads as download_utils


class _Expr:
    def __init__(self, value):
        self.value = value

    def __and__(self, other):
        return _Expr(("and", self.value, getattr(other, "value", other)))

    def __eq__(self, other):
        return self.value == getattr(other, "value", other)

    def __hash__(self):
        return hash(self.value)

    def __repr__(self):
        return repr(self.value)


class _Field:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return _Expr((self.name, "==", other))

    def __ge__(self, other):
        return _Expr((self.name, ">=", other))

    def __le__(self, other):
        return _Expr((self.name, "<=", other))

    def __lt__(self, other):
        return _Expr((self.name, "<", other))

    def __add__(self, other):
        return _Expr((self.name, "+", other))

    def desc(self):
        return _Expr((self.name, "desc"))

    def asc(self):
        return _Expr((self.name, "asc"))

    def __hash__(self):
        return hash(self.name)


class _Query:
    def __init__(self, items=None, first_result=None, execute_result=1):
        self.items = list(items or [])
        self.first_result = first_result
        self.execute_result = execute_result
        self.where_calls = []
        self.order_by_calls = []
        self.on_conflict_calls = []

    def where(self, *args):
        self.where_calls.append(args)
        return self

    def order_by(self, *args):
        self.order_by_calls.append(args)
        return self

    def on_conflict(self, **kwargs):
        self.on_conflict_calls.append(kwargs)
        return self

    def execute(self):
        return self.execute_result

    def first(self):
        return self.first_result

    def __iter__(self):
        return iter(self.items)


class _AtomicContext:
    def __init__(self, seen: dict):
        self.seen = seen

    def __enter__(self):
        self.seen["entered"] = self.seen.get("entered", 0) + 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.seen["exited"] = self.seen.get("exited", 0) + 1
        return False


def test_get_or_create_tracking_cookie_reuses_existing_and_sets_new_cookie(monkeypatch):
    response_cookies = {}

    assert (
        download_utils.get_or_create_tracking_cookie(
            {"hf_download_session": "existing"}, response_cookies
        )
        == "existing"
    )
    assert response_cookies == {}

    monkeypatch.setattr(download_utils.uuid, "uuid4", lambda: SimpleNamespace(hex="newsession"))
    created = download_utils.get_or_create_tracking_cookie({}, response_cookies)

    assert created == "newsession"
    assert response_cookies["hf_download_session"]["httponly"] is True


@pytest.mark.asyncio
async def test_track_download_async_updates_existing_session(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    existing = SimpleNamespace(id=7, file_count=2)
    seen = {}
    update_query = _Query()

    class FakeDailyRepoStats:
        total_files = _Field("total_files")
        repository = _Field("repository")
        date = _Field("date")

        @staticmethod
        def update(**kwargs):
            seen["update_kwargs"] = kwargs
            return update_query

    monkeypatch.setattr(download_utils, "DailyRepoStats", FakeDailyRepoStats)
    monkeypatch.setattr(download_utils.time, "time", lambda: 120)
    monkeypatch.setattr(download_utils.cfg.app, "download_time_bucket_seconds", 10)
    monkeypatch.setattr(download_utils, "get_download_session", lambda repo_arg, session_id, time_bucket: existing)
    monkeypatch.setattr(download_utils, "increment_download_session_files", lambda session_id: seen.setdefault("incremented", []).append(session_id))

    await download_utils.track_download_async(repo, "README.md", "session-1", user=None)

    assert seen["incremented"] == [7]
    assert seen["update_kwargs"] == {"total_files": _Expr(("total_files", "+", 1))}
    assert update_query.where_calls


@pytest.mark.asyncio
async def test_track_download_async_creates_new_session_and_schedules_cleanup(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo", downloads=4)
    user = SimpleNamespace(username="owner")
    seen = {}
    insert_query = _Query()
    db_state = {}

    class FakeDailyRepoStats:
        repository = _Field("repository")
        date = _Field("date")
        download_sessions = _Field("download_sessions")
        authenticated_downloads = _Field("authenticated_downloads")
        anonymous_downloads = _Field("anonymous_downloads")
        total_files = _Field("total_files")

        @staticmethod
        def insert(**kwargs):
            seen["insert_kwargs"] = kwargs
            return insert_query

    monkeypatch.setattr(download_utils, "DailyRepoStats", FakeDailyRepoStats)
    monkeypatch.setattr(download_utils, "get_download_session", lambda *args: None)
    monkeypatch.setattr(download_utils.time, "time", lambda: 250)
    monkeypatch.setattr(download_utils.cfg.app, "download_time_bucket_seconds", 5)
    monkeypatch.setattr(download_utils.cfg.app, "download_session_cleanup_threshold", 3)
    monkeypatch.setattr(download_utils, "count_repository_sessions", lambda repo_arg: 9)
    monkeypatch.setattr(download_utils, "create_download_session", lambda **kwargs: seen.setdefault("created", []).append(kwargs))
    monkeypatch.setattr(download_utils, "update_repository", lambda repo_arg, **kwargs: seen.setdefault("updated_repo", []).append((repo_arg, kwargs)))
    async def fake_cleanup(repo_arg):
        return "cleanup-coro"

    def fake_create_task(coro):
        seen["task"] = coro
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(download_utils, "aggregate_old_sessions", fake_cleanup)
    monkeypatch.setattr(download_utils.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(download_utils, "db", SimpleNamespace(atomic=lambda: _AtomicContext(db_state)))

    await download_utils.track_download_async(repo, "weights.bin", "session-2", user=user)

    assert db_state == {"entered": 1, "exited": 1}
    assert seen["created"][0]["first_file"] == "weights.bin"
    assert seen["updated_repo"] == [(repo, {"downloads": 5})]
    assert seen["insert_kwargs"]["authenticated_downloads"] == 1
    assert insert_query.on_conflict_calls
    assert seen["task"].cr_code.co_name == "fake_cleanup"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("latest_stat", "expected_start_delta"),
    [
        (None, None),
        (SimpleNamespace(date=datetime.now(timezone.utc).date() - timedelta(days=3)), 2),
        (SimpleNamespace(date=datetime.now(timezone.utc).date() - timedelta(days=1)), "skip"),
    ],
)
async def test_ensure_stats_up_to_date_dispatches_aggregation_ranges(
    monkeypatch, latest_stat, expected_start_delta
):
    seen = {}

    class FakeDailyRepoStats:
        repository = _Field("repository")
        date = _Field("date")

        @staticmethod
        def select():
            return _Query(first_result=latest_stat)

    async def fake_aggregate(repo, start_date, end_date):
        seen["call"] = (repo, start_date, end_date)

    monkeypatch.setattr(download_utils, "DailyRepoStats", FakeDailyRepoStats)
    monkeypatch.setattr(download_utils, "aggregate_sessions_to_daily", fake_aggregate)

    repo = SimpleNamespace(full_id="owner/repo")
    await download_utils.ensure_stats_up_to_date(repo)

    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    if expected_start_delta == "skip":
        assert seen == {}
    elif expected_start_delta is None:
        assert seen["call"] == (repo, None, yesterday)
    else:
        assert seen["call"] == (
            repo,
            today - timedelta(days=expected_start_delta),
            yesterday,
        )


@pytest.mark.asyncio
async def test_aggregate_sessions_to_daily_groups_sessions_and_upserts(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    today = datetime.now(timezone.utc).date()
    sessions = [
        SimpleNamespace(
            first_download_at=datetime.combine(today - timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc),
            file_count=2,
            user=SimpleNamespace(username="owner"),
        ),
        SimpleNamespace(
            first_download_at=datetime.combine(today - timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc),
            file_count=1,
            user=None,
        ),
        SimpleNamespace(
            first_download_at=datetime.combine(today - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc),
            file_count=4,
            user=None,
        ),
    ]
    db_state = {}
    insert_calls = []

    class FakeDownloadSession:
        repository = _Field("repository")
        first_download_at = _Field("first_download_at")

        @staticmethod
        def select():
            return _Query(items=sessions)

    class FakeDailyRepoStats:
        repository = _Field("repository")
        date = _Field("date")
        download_sessions = _Field("download_sessions")
        authenticated_downloads = _Field("authenticated_downloads")
        anonymous_downloads = _Field("anonymous_downloads")
        total_files = _Field("total_files")

        @staticmethod
        def insert(**kwargs):
            insert_calls.append(kwargs)
            return _Query()

    monkeypatch.setattr(download_utils, "DownloadSession", FakeDownloadSession)
    monkeypatch.setattr(download_utils, "DailyRepoStats", FakeDailyRepoStats)
    monkeypatch.setattr(download_utils, "db", SimpleNamespace(atomic=lambda: _AtomicContext(db_state)))

    await download_utils.aggregate_sessions_to_daily(
        repo,
        start_date=today - timedelta(days=3),
        end_date=today - timedelta(days=1),
    )

    assert db_state == {"entered": 1, "exited": 1}
    assert len(insert_calls) == 2
    older_day = min(call["date"] for call in insert_calls)
    older_stats = next(call for call in insert_calls if call["date"] == older_day)
    assert older_stats["download_sessions"] == 2
    assert older_stats["authenticated_downloads"] == 1
    assert older_stats["anonymous_downloads"] == 1
    assert older_stats["total_files"] == 3


@pytest.mark.asyncio
async def test_aggregate_old_sessions_cleans_up_and_handles_errors(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    seen = {}
    delete_query = _Query(execute_result=3)

    class FakeDownloadSession:
        repository = _Field("repository")
        first_download_at = _Field("first_download_at")

        @staticmethod
        def delete():
            return delete_query

    async def fake_ensure(repo_arg):
        seen["ensured"] = repo_arg

    monkeypatch.setattr(download_utils, "DownloadSession", FakeDownloadSession)
    monkeypatch.setattr(download_utils, "ensure_stats_up_to_date", fake_ensure)
    monkeypatch.setattr(download_utils.cfg.app, "download_keep_sessions_days", 7)

    await download_utils.aggregate_old_sessions(repo)

    assert seen["ensured"] is repo
    assert delete_query.where_calls

    async def broken_ensure(repo_arg):
        raise RuntimeError("boom")

    errors = []
    monkeypatch.setattr(download_utils, "ensure_stats_up_to_date", broken_ensure)
    monkeypatch.setattr(download_utils.logger, "exception", lambda message, error: errors.append((message, str(error))))

    await download_utils.aggregate_old_sessions(repo)

    assert errors == [("Failed to aggregate old sessions for owner/repo", "boom")]
