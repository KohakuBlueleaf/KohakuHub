"""Unit tests for repository info routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import kohakuhub.api.repo.routers.info as repo_info


class _Expr:
    def __init__(self, value):
        self.value = value

    def __and__(self, other):
        return _Expr(("and", self.value, getattr(other, "value", other)))

    def __or__(self, other):
        return _Expr(("or", self.value, getattr(other, "value", other)))

    def alias(self, name):
        return _Expr(("alias", self.value, name))

    def desc(self):
        return _Expr(("desc", self.value))


class _Field:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return _Expr((self.name, "==", other))

    def __add__(self, other):
        return _Expr(("add", self.name, getattr(other, "name", other)))

    def alias(self, name):
        return _Expr(("alias", self.name, name))

    def desc(self):
        return _Expr(("desc", self.name))

    def in_(self, values):
        return _Expr((self.name, "in", tuple(values)))


class _Query:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.where_calls = []
        self.order_by_calls = []
        self.join_calls = []
        self.group_by_calls = []
        self.limit_value = None
        self.c = SimpleNamespace(repository_id="repository_id", last_commit_at="last_commit_at")

    def where(self, *args):
        self.where_calls.append(args)
        return self

    def order_by(self, *args):
        self.order_by_calls.append(args)
        return self

    def join(self, *args, **kwargs):
        self.join_calls.append((args, kwargs))
        return self

    def group_by(self, *args):
        self.group_by_calls.append(args)
        return self

    def alias(self, name):
        self.alias_name = name
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def __iter__(self):
        items = self.items
        if self.limit_value is not None:
            items = items[: self.limit_value]
        return iter(items)


class _FakeRepositoryModel:
    repo_type = _Field("repo_type")
    namespace = _Field("namespace")
    private = _Field("private")
    likes_count = _Field("likes_count")
    downloads = _Field("downloads")
    created_at = _Field("created_at")
    id = _Field("id")

    select_queries = []

    @classmethod
    def reset(cls):
        cls.select_queries = []

    @classmethod
    def select(cls):
        if cls.select_queries:
            return cls.select_queries.pop(0)
        return _Query()


class _FakeCommitModel:
    repository = _Field("repository")
    created_at = _Field("created_at")
    repo_type = _Field("repo_type")
    branch = _Field("branch")

    select_query = _Query()

    @classmethod
    def select(cls, *args):
        return cls.select_query


class _FakeUserOrganizationModel:
    user = _Field("user")
    select_query = _Query()

    @classmethod
    def select(cls):
        return cls.select_query


class _FakeClient:
    def __init__(self):
        self.branch_result = {"commit_id": "commit-1234567890abcdef"}
        self.branch_error = None
        self.commit_result = {"creation_date": 1}
        self.commit_error = None
        self.list_payloads = []
        self.list_error = None

    async def get_branch(self, **kwargs):
        if self.branch_error:
            raise self.branch_error
        return self.branch_result

    async def get_commit(self, **kwargs):
        if self.commit_error:
            raise self.commit_error
        return self.commit_result

    async def list_objects(self, **kwargs):
        if self.list_error:
            raise self.list_error
        return self.list_payloads.pop(0)


def _request(path: str):
    return SimpleNamespace(url=SimpleNamespace(path=path))


def _async_return(value=None):
    async def _inner(*args, **kwargs):
        return value

    return _inner


@pytest.fixture(autouse=True)
def _reset_models():
    _FakeRepositoryModel.reset()
    _FakeCommitModel.select_query = _Query()
    _FakeUserOrganizationModel.select_query = _Query()


def test_apply_repo_sorting_and_filter_privacy_cover_all_sort_modes(monkeypatch):
    query = _Query()

    monkeypatch.setattr(repo_info, "Repository", _FakeRepositoryModel)
    monkeypatch.setattr(repo_info, "Commit", _FakeCommitModel)
    monkeypatch.setattr(repo_info, "UserOrganization", _FakeUserOrganizationModel)
    monkeypatch.setattr(repo_info, "JOIN", SimpleNamespace(LEFT_OUTER="left"))
    monkeypatch.setattr(
        repo_info,
        "fn",
        SimpleNamespace(
            MAX=lambda value: _Expr(("max", value)),
            COALESCE=lambda *values: _Expr(("coalesce", values)),
        ),
    )

    repo_info._apply_repo_sorting(query, "model", "likes")
    repo_info._apply_repo_sorting(query, "model", "downloads")
    updated_query = repo_info._apply_repo_sorting(query, "model", "updated")
    recent_query = repo_info._apply_repo_sorting(query, "model", "recent")
    assert updated_query.join_calls
    assert recent_query.order_by_calls

    public_query = _Query()
    filtered_public = repo_info._filter_repos_by_privacy(public_query, None)
    assert filtered_public.where_calls

    _FakeUserOrganizationModel.select_query = _Query(
        items=[SimpleNamespace(organization=SimpleNamespace(username="org-team"))]
    )
    private_query = _Query()
    filtered_private = repo_info._filter_repos_by_privacy(
        private_query,
        SimpleNamespace(username="alice"),
        author="alice",
    )
    assert filtered_private.where_calls


@pytest.mark.asyncio
async def test_get_repo_info_covers_invalid_type_not_found_siblings_and_storage_paths(
    monkeypatch,
):
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    client = _FakeClient()
    repo_row = SimpleNamespace(
        id=1,
        namespace="alice",
        created_at=now,
        private=False,
        downloads=12,
        likes_count=3,
    )

    monkeypatch.setattr(
        repo_info,
        "hf_error_response",
        lambda status, code, message: SimpleNamespace(status_code=status, message=message),
    )
    monkeypatch.setattr(
        repo_info,
        "hf_repo_not_found",
        lambda repo_id, repo_type: SimpleNamespace(status_code=404),
    )
    monkeypatch.setattr(repo_info, "check_repo_read_permission", lambda repo, user: None)
    monkeypatch.setattr(repo_info, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(repo_info, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(repo_info, "format_hf_datetime", lambda value: "2024-01-02T00:00:00.000000Z")
    monkeypatch.setattr(
        repo_info,
        "should_use_lfs",
        lambda repo, path, size: path.endswith(".bin"),
    )
    monkeypatch.setattr(
        repo_info,
        "get_file",
        lambda repo, path: (_ for _ in ()).throw(RuntimeError("db fail"))
        if path == "broken.bin"
        else SimpleNamespace(sha256="db-sha"),
    )

    invalid_type = await repo_info.get_repo_info.__wrapped__(
        "alice",
        "demo",
        request=_request("/api/unknown/alice/demo"),
        user=None,
    )
    assert invalid_type.status_code == 404

    monkeypatch.setattr(repo_info, "get_repository", lambda repo_type, namespace, name: None)
    not_found = await repo_info.get_repo_info.__wrapped__(
        "alice",
        "demo",
        request=_request("/api/models/alice/demo"),
        user=None,
    )
    assert not_found.status_code == 404

    monkeypatch.setattr(repo_info, "get_repository", lambda repo_type, namespace, name: repo_row)
    monkeypatch.setattr(
        repo_info,
        "get_repo_storage_info",
        lambda repo: {
            "quota_bytes": 100,
            "used_bytes": 20,
            "available_bytes": 80,
            "percentage_used": 20,
            "effective_quota_bytes": 100,
            "is_inheriting": False,
        },
    )
    client.list_payloads = [
        {
            "results": [
                {"path_type": "object", "path": "README.md", "size_bytes": 4, "checksum": "sha256:readme"},
                {"path_type": "object", "path": "weights.bin", "size_bytes": 8, "checksum": "sha256:weights"},
            ],
            "pagination": {"has_more": True, "next_offset": "cursor-2"},
        },
        {
            "results": [
                {"path_type": "object", "path": "broken.bin", "size_bytes": 9, "checksum": "sha256:broken"},
            ],
            "pagination": {"has_more": False},
        },
    ]
    info = await repo_info.get_repo_info.__wrapped__(
        "alice",
        "demo",
        request=_request("/api/models/alice/demo"),
        user=SimpleNamespace(username="alice"),
    )
    assert info["id"] == "alice/demo"
    assert info["storage"]["quota_bytes"] == 100
    assert info["siblings"][1]["lfs"]["sha256"] == "db-sha"
    assert info["siblings"][2]["lfs"]["sha256"] == "sha256:broken"

    client.commit_error = RuntimeError("commit fail")
    monkeypatch.setattr(repo_info, "get_repo_storage_info", lambda repo: (_ for _ in ()).throw(RuntimeError("quota fail")))
    client.list_payloads = [{"results": [], "pagination": {"has_more": False}}]
    info_without_storage = await repo_info.get_repo_info.__wrapped__(
        "alice",
        "demo",
        request=_request("/api/models/alice/demo"),
        user=SimpleNamespace(username="alice"),
    )
    assert "storage" not in info_without_storage

    client.commit_error = None
    client.list_error = RuntimeError("list fail")
    info_without_siblings = await repo_info.get_repo_info.__wrapped__(
        "alice",
        "demo",
        request=_request("/api/models/alice/demo"),
        user=None,
    )
    assert info_without_siblings["siblings"] == []

    client.branch_error = RuntimeError("missing branch")
    client.list_error = None
    client.list_payloads = [{"results": [], "pagination": {"has_more": False}}]
    info_without_sha = await repo_info.get_repo_info.__wrapped__(
        "alice",
        "demo",
        request=_request("/api/models/alice/demo"),
        user=None,
    )
    assert info_without_sha["sha"] is None
    client.branch_error = None


@pytest.mark.asyncio
async def test_list_routes_cover_trending_invalid_path_and_user_repo_error_paths(
    monkeypatch,
):
    client = _FakeClient()
    repo_row = SimpleNamespace(
        full_id="alice/demo",
        namespace="alice",
        private=False,
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        downloads=10,
        likes_count=5,
    )

    monkeypatch.setattr(repo_info, "Repository", _FakeRepositoryModel)
    monkeypatch.setattr(repo_info, "UserOrganization", _FakeUserOrganizationModel)
    monkeypatch.setattr(repo_info, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(repo_info, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(
        repo_info,
        "safe_strftime",
        lambda value, fmt: "2024-01-02T00:00:00.000000Z",
    )
    monkeypatch.setattr(repo_info, "_apply_repo_sorting", lambda query, rt, sort: query)
    monkeypatch.setattr(
        repo_info,
        "hf_error_response",
        lambda status, code, message: SimpleNamespace(status_code=status, message=message),
    )
    monkeypatch.setattr(
        "kohakuhub.api.utils.trending.get_trending_repositories",
        lambda rt, limit, days: [repo_row],
    )

    _FakeRepositoryModel.select_queries = [_Query(items=[repo_row])]
    client.commit_error = RuntimeError("commit fail")
    trending = await repo_info._list_repos_internal("model", sort="trending", user=None)
    assert trending[0]["id"] == "alice/demo"
    assert trending[0]["lastModified"] is None

    monkeypatch.setattr(repo_info, "_list_models_with_aggregation", _async_return(["models"]))
    monkeypatch.setattr(repo_info, "_list_datasets_with_aggregation", _async_return(["datasets"]))
    monkeypatch.setattr(repo_info, "_list_spaces_with_aggregation", _async_return(["spaces"]))
    assert await repo_info.list_repos(request=_request("/api/models")) == ["models"]
    assert await repo_info.list_repos(request=_request("/api/datasets")) == ["datasets"]
    assert await repo_info.list_repos(request=_request("/api/spaces")) == ["spaces"]
    invalid_list = await repo_info.list_repos(request=_request("/api/unknown"))
    assert invalid_list.status_code == 404

    monkeypatch.setattr(repo_info, "get_user_by_username", lambda username: None)
    monkeypatch.setattr(repo_info, "get_organization", lambda username: None)
    user_not_found = await repo_info.list_user_repos.__wrapped__(
        "ghost",
        request=None,
        user=None,
    )
    assert user_not_found.status_code == 404

    monkeypatch.setattr(
        repo_info,
        "get_user_by_username",
        lambda username: SimpleNamespace(username=username),
    )
    monkeypatch.setattr(repo_info, "get_organization", lambda username: None)
    monkeypatch.setattr(repo_info, "_filter_repos_by_privacy", lambda query, user, author=None: query)

    for sort in ["likes", "downloads"]:
        _FakeRepositoryModel.select_queries = [
            _Query(items=[repo_row]),
            _Query(items=[repo_row]),
            _Query(items=[repo_row]),
        ]
        result = await repo_info.list_user_repos.__wrapped__(
            "alice",
            request=None,
            limit=10,
            sort=sort,
            user=SimpleNamespace(username="alice"),
        )
        assert result["models"][0]["id"] == "alice/demo"
