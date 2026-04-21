"""Unit tests for admin quota routes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.admin.routers.quota as admin_quota


class _Expr:
    def __init__(self, value):
        self.value = value

    def __add__(self, other):
        return _Expr(("add", self.value, getattr(other, "value", other)))

    def __and__(self, other):
        return _Expr(("and", self.value, getattr(other, "value", other)))

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


class _Query:
    def __init__(self, items=None, scalar_value=None):
        self.items = list(items or [])
        self.scalar_value = scalar_value
        self.where_calls = []
        self.order_by_calls = []
        self.limit_value = None

    def where(self, *args):
        self.where_calls.append(args)
        return self

    def order_by(self, *args):
        self.order_by_calls.append(args)
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def scalar(self):
        return self.scalar_value

    def __iter__(self):
        items = self.items
        if self.limit_value is not None:
            items = items[: self.limit_value]
        return iter(items)


class _FakeUserModel:
    username = _Field("username")
    is_org = _Field("is_org")
    private_used_bytes = _Field("private_used_bytes")
    public_used_bytes = _Field("public_used_bytes")

    get_or_none_responses = []
    select_queries = []

    @classmethod
    def reset(cls):
        cls.get_or_none_responses = []
        cls.select_queries = []

    @classmethod
    def get_or_none(cls, _expr):
        if cls.get_or_none_responses:
            return cls.get_or_none_responses.pop(0)
        return None

    @classmethod
    def select(cls, *args):
        if cls.select_queries:
            return cls.select_queries.pop(0)
        return _Query()


class _FakeRepositoryModel:
    repo_type = _Field("repo_type")
    namespace = _Field("namespace")
    select_query = _Query()

    @classmethod
    def reset(cls):
        cls.select_query = _Query()

    @classmethod
    def select(cls):
        return cls.select_query


class _FakeLFSHistoryModel:
    size = _Field("size")
    select_query = _Query()

    @classmethod
    def reset(cls):
        cls.select_query = _Query()

    @classmethod
    def select(cls, *args):
        return cls.select_query


@pytest.fixture(autouse=True)
def _reset_models():
    _FakeUserModel.reset()
    _FakeRepositoryModel.reset()
    _FakeLFSHistoryModel.reset()


def _async_return(value=None, error=None):
    async def _inner(*args, **kwargs):
        if error:
            raise error
        return value

    return _inner


@pytest.mark.asyncio
async def test_get_quota_overview_covers_overages_and_totals(monkeypatch):
    users = [
        SimpleNamespace(
            username="alice",
            is_org=False,
            private_used_bytes=150,
            private_quota_bytes=100,
            public_used_bytes=10,
            public_quota_bytes=20,
        ),
        SimpleNamespace(
            username="bob",
            is_org=False,
            private_used_bytes=10,
            private_quota_bytes=None,
            public_used_bytes=120,
            public_quota_bytes=100,
        ),
    ]
    top_consumers = [
        SimpleNamespace(username="alice", is_org=False, total=160),
        SimpleNamespace(username="org-team", is_org=True, total=99),
    ]
    repos = [
        SimpleNamespace(full_id="alice/demo", repo_type="model"),
        SimpleNamespace(full_id="bob/ok", repo_type="dataset"),
    ]

    monkeypatch.setattr(admin_quota, "User", _FakeUserModel)
    monkeypatch.setattr(admin_quota, "Repository", _FakeRepositoryModel)
    monkeypatch.setattr(admin_quota, "LFSObjectHistory", _FakeLFSHistoryModel)
    monkeypatch.setattr(admin_quota, "fn", SimpleNamespace(SUM=lambda value: ("sum", value)))
    monkeypatch.setattr(
        admin_quota,
        "get_repo_storage_info",
        lambda repo: {
            "used_bytes": 120,
            "quota_bytes": 100,
            "percentage_used": 120,
        }
        if repo.full_id == "alice/demo"
        else {
            "used_bytes": 10,
            "quota_bytes": 100,
            "percentage_used": 10,
        },
    )

    _FakeUserModel.select_queries = [
        _Query(items=users),
        _Query(items=top_consumers),
        _Query(scalar_value=160),
        _Query(scalar_value=130),
    ]
    _FakeRepositoryModel.select_query = _Query(items=repos)
    _FakeLFSHistoryModel.select_query = _Query(scalar_value=77)

    overview = await admin_quota.get_quota_overview()
    assert [item["username"] for item in overview["users_over_quota"]] == ["alice", "bob"]
    assert overview["repos_over_quota"] == [
        {
            "full_id": "alice/demo",
            "repo_type": "model",
            "used_bytes": 120,
            "quota_bytes": 100,
            "percentage": 120,
        }
    ]
    assert overview["top_consumers"][0]["username"] == "alice"
    assert overview["system_storage"] == {
        "private_used": 160,
        "public_used": 130,
        "lfs_used": 77,
        "total_used": 290,
    }


@pytest.mark.asyncio
async def test_quota_namespace_routes_cover_not_found_and_success(monkeypatch):
    monkeypatch.setattr(admin_quota, "User", _FakeUserModel)
    monkeypatch.setattr(
        admin_quota,
        "get_storage_info",
        lambda namespace, is_org: {"used_bytes": 10, "quota_bytes": 20},
    )
    monkeypatch.setattr(
        admin_quota,
        "set_quota",
        lambda namespace, private_quota_bytes, public_quota_bytes, is_org: {
            "private_quota_bytes": private_quota_bytes,
            "public_quota_bytes": public_quota_bytes,
        },
    )
    monkeypatch.setattr(
        admin_quota,
        "update_namespace_storage",
        _async_return({"private_used_bytes": 12, "public_used_bytes": 34}),
    )

    _FakeUserModel.get_or_none_responses = [None]
    with pytest.raises(HTTPException) as missing_get:
        await admin_quota.get_quota_admin("ghost", is_org=False)
    assert missing_get.value.status_code == 404

    _FakeUserModel.get_or_none_responses = [SimpleNamespace(username="alice")]
    got = await admin_quota.get_quota_admin("alice", is_org=False)
    assert got == {
        "namespace": "alice",
        "is_organization": False,
        "used_bytes": 10,
        "quota_bytes": 20,
    }

    _FakeUserModel.get_or_none_responses = [None]
    with pytest.raises(HTTPException) as missing_set:
        await admin_quota.set_quota_admin(
            "ghost",
            admin_quota.SetQuotaRequest(private_quota_bytes=1),
            is_org=True,
        )
    assert missing_set.value.status_code == 404

    _FakeUserModel.get_or_none_responses = [SimpleNamespace(username="org-team")]
    updated = await admin_quota.set_quota_admin(
        "org-team",
        admin_quota.SetQuotaRequest(private_quota_bytes=100, public_quota_bytes=200),
        is_org=True,
    )
    assert updated == {
        "namespace": "org-team",
        "is_organization": True,
        "private_quota_bytes": 100,
        "public_quota_bytes": 200,
    }

    _FakeUserModel.get_or_none_responses = [None]
    with pytest.raises(HTTPException) as missing_recalc:
        await admin_quota.recalculate_quota_admin("ghost")
    assert missing_recalc.value.status_code == 404

    _FakeUserModel.get_or_none_responses = [SimpleNamespace(username="alice")]
    recalculated = await admin_quota.recalculate_quota_admin("alice")
    assert recalculated == {
        "namespace": "alice",
        "is_organization": False,
        "recalculated": {"private_used_bytes": 12, "public_used_bytes": 34},
        "used_bytes": 10,
        "quota_bytes": 20,
    }


@pytest.mark.asyncio
async def test_recalculate_all_repo_storage_admin_covers_filters_progress_and_failures(
    monkeypatch,
):
    repos = [
        SimpleNamespace(full_id=f"owner/repo-{idx}", repo_type="model", namespace="owner")
        for idx in range(11)
    ]
    updated = []

    async def _update_repo(repo):
        updated.append(repo.full_id)
        if repo.full_id.endswith("5"):
            raise RuntimeError("boom")
        return {"ok": True}

    monkeypatch.setattr(admin_quota, "Repository", _FakeRepositoryModel)
    monkeypatch.setattr(admin_quota, "update_repository_storage", _update_repo)

    _FakeRepositoryModel.select_query = _Query(items=repos)
    result = await admin_quota.recalculate_all_repo_storage_admin(
        repo_type="model",
        namespace="owner",
    )
    assert result["total"] == 11
    assert result["success_count"] == 10
    assert result["failure_count"] == 1
    assert result["failures"] == [{"repo_id": "owner/repo-5", "error": "boom"}]
    assert len(_FakeRepositoryModel.select_query.where_calls) == 2
    assert len(updated) == 11
