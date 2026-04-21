"""Unit tests for admin user routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.admin.routers.users as admin_users


class _Expr:
    def __init__(self, value):
        self.value = value

    def __or__(self, other):
        return _Expr(("or", self.value, getattr(other, "value", other)))


class _Field:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return _Expr((self.name, "==", other))

    def contains(self, other):
        return _Expr((self.name, "contains", other))


class _Query:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.where_calls = []
        self.limit_value = None
        self.offset_value = 0

    def where(self, *args):
        self.where_calls.append(args)
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def offset(self, value):
        self.offset_value = value
        return self

    def __iter__(self):
        items = self.items[self.offset_value :]
        if self.limit_value is not None:
            items = items[: self.limit_value]
        return iter(items)


class _AtomicContext:
    def __init__(self, state: dict):
        self.state = state

    def __enter__(self):
        self.state["entered"] = self.state.get("entered", 0) + 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.state["exited"] = self.state.get("exited", 0) + 1
        return False


class _FakeUserModel:
    username = _Field("username")
    email = _Field("email")
    is_org = _Field("is_org")

    select_query = _Query()
    get_or_none_responses = []

    @classmethod
    def reset(cls):
        cls.select_query = _Query()
        cls.get_or_none_responses = []

    @classmethod
    def select(cls):
        return cls.select_query

    @classmethod
    def get_or_none(cls, _expr):
        if cls.get_or_none_responses:
            return cls.get_or_none_responses.pop(0)
        return None


class _FakeRepositoryModel:
    owner = _Field("owner")
    select_query = _Query()

    @classmethod
    def reset(cls):
        cls.select_query = _Query()

    @classmethod
    def select(cls):
        return cls.select_query


@pytest.fixture(autouse=True)
def _reset_models():
    _FakeUserModel.reset()
    _FakeRepositoryModel.reset()


@pytest.mark.asyncio
async def test_get_user_info_and_list_users_cover_not_found_and_filters(monkeypatch):
    created_at = datetime(2024, 1, 2, tzinfo=timezone.utc)
    alice = SimpleNamespace(
        id=1,
        username="alice",
        email="alice@example.com",
        email_verified=True,
        is_active=True,
        is_org=False,
        private_quota_bytes=100,
        public_quota_bytes=200,
        private_used_bytes=10,
        public_used_bytes=20,
        created_at=created_at,
    )
    org = SimpleNamespace(
        id=2,
        username="org-team",
        email=None,
        email_verified=True,
        is_active=True,
        is_org=True,
        private_quota_bytes=300,
        public_quota_bytes=400,
        private_used_bytes=30,
        public_used_bytes=40,
        created_at=created_at,
    )

    monkeypatch.setattr(admin_users, "User", _FakeUserModel)

    with pytest.raises(HTTPException) as not_found:
        await admin_users.get_user_info("missing")
    assert not_found.value.status_code == 404

    _FakeUserModel.get_or_none_responses = [alice]
    payload = await admin_users.get_user_info("alice")
    assert payload.username == "alice"
    assert payload.is_org is False

    _FakeUserModel.select_query = _Query(items=[alice, org])
    listed = await admin_users.list_users(search="ali", limit=1, offset=0, include_orgs=False)
    assert listed["users"] == [
        {
            "id": 1,
            "username": "alice",
            "email": "alice@example.com",
            "email_verified": True,
            "is_active": True,
            "is_org": False,
            "private_quota_bytes": 100,
            "public_quota_bytes": 200,
            "private_used_bytes": 10,
            "public_used_bytes": 20,
            "created_at": created_at.isoformat(),
        }
    ]
    assert _FakeUserModel.select_query.where_calls

    _FakeUserModel.select_query = _Query(items=[alice, org])
    listed_with_orgs = await admin_users.list_users(include_orgs=True, limit=10, offset=1)
    assert [item["username"] for item in listed_with_orgs["users"]] == ["org-team"]


@pytest.mark.asyncio
async def test_create_user_and_delete_user_cover_conflicts_force_and_success(monkeypatch):
    created_at = datetime(2024, 1, 2, tzinfo=timezone.utc)
    atomic_state = {}
    created_calls = []
    deleted_users = []
    deleted_repos = []
    user = SimpleNamespace(
        id=3,
        username="bob",
        email="bob@example.com",
        email_verified=False,
        is_active=True,
        is_org=False,
        private_quota_bytes=123,
        public_quota_bytes=456,
        private_used_bytes=0,
        public_used_bytes=0,
        created_at=created_at,
    )
    repo = SimpleNamespace(repo_type="model", full_id="bob/demo")

    monkeypatch.setattr(admin_users, "User", _FakeUserModel)
    monkeypatch.setattr(admin_users, "Repository", _FakeRepositoryModel)
    monkeypatch.setattr(
        admin_users, "db", SimpleNamespace(atomic=lambda: _AtomicContext(atomic_state))
    )
    monkeypatch.setattr(admin_users.bcrypt, "gensalt", lambda: b"salt")
    monkeypatch.setattr(
        admin_users.bcrypt, "hashpw", lambda password, salt: b"hashed-password"
    )
    monkeypatch.setattr(
        admin_users,
        "create_user",
        lambda **kwargs: created_calls.append(kwargs) or user,
    )
    monkeypatch.setattr(
        admin_users, "delete_user", lambda target: deleted_users.append(target.username)
    )
    monkeypatch.setattr(
        admin_users,
        "delete_repository",
        lambda target: deleted_repos.append(target.full_id),
    )

    _FakeUserModel.get_or_none_responses = [SimpleNamespace()]
    with pytest.raises(HTTPException) as username_conflict:
        await admin_users.create_user_admin(
            admin_users.CreateUserRequest(
                username="bob",
                email="bob@example.com",
                password="secret",
            )
        )
    assert username_conflict.value.status_code == 400

    _FakeUserModel.get_or_none_responses = [None, SimpleNamespace()]
    with pytest.raises(HTTPException) as email_conflict:
        await admin_users.create_user_admin(
            admin_users.CreateUserRequest(
                username="bob",
                email="bob@example.com",
                password="secret",
            )
        )
    assert email_conflict.value.status_code == 400

    _FakeUserModel.get_or_none_responses = [None, None]
    created = await admin_users.create_user_admin(
        admin_users.CreateUserRequest(
            username="bob",
            email="bob@example.com",
            password="secret",
            email_verified=True,
            private_quota_bytes=123,
            public_quota_bytes=456,
        )
    )
    assert created.username == "bob"
    assert created_calls[-1]["password_hash"] == "hashed-password"
    assert atomic_state == {"entered": 3, "exited": 3}

    _FakeUserModel.get_or_none_responses = [None]
    with pytest.raises(HTTPException) as missing_user:
        await admin_users.delete_user_admin("ghost")
    assert missing_user.value.status_code == 404

    _FakeUserModel.get_or_none_responses = [user]
    _FakeRepositoryModel.select_query = _Query(items=[repo])
    with pytest.raises(HTTPException) as owns_repos:
        await admin_users.delete_user_admin("bob", force=False)
    assert owns_repos.value.status_code == 400

    _FakeUserModel.get_or_none_responses = [user]
    _FakeRepositoryModel.select_query = _Query(items=[repo])
    deleted = await admin_users.delete_user_admin("bob", force=True)
    assert deleted["deleted_repositories"] == ["model:bob/demo"]
    assert deleted_users[-1] == "bob"
    assert deleted_repos[-1] == "bob/demo"


@pytest.mark.asyncio
async def test_email_verification_and_quota_update_cover_not_found_and_success(monkeypatch):
    save_calls = []
    user = SimpleNamespace(
        username="alice",
        email="alice@example.com",
        email_verified=False,
        private_quota_bytes=10,
        public_quota_bytes=20,
        private_used_bytes=1,
        public_used_bytes=2,
        save=lambda: save_calls.append("saved"),
    )

    monkeypatch.setattr(admin_users, "User", _FakeUserModel)

    _FakeUserModel.get_or_none_responses = [None]
    with pytest.raises(HTTPException) as verification_missing:
        await admin_users.set_email_verification("alice", True)
    assert verification_missing.value.status_code == 404

    _FakeUserModel.get_or_none_responses = [user]
    verified = await admin_users.set_email_verification("alice", True)
    assert verified["email_verified"] is True
    assert save_calls == ["saved"]

    _FakeUserModel.get_or_none_responses = [None]
    with pytest.raises(HTTPException) as quota_missing:
        await admin_users.update_user_quota(
            "alice", admin_users.UpdateQuotaRequest(private_quota_bytes=99)
        )
    assert quota_missing.value.status_code == 404

    _FakeUserModel.get_or_none_responses = [user]
    updated = await admin_users.update_user_quota(
        "alice",
        admin_users.UpdateQuotaRequest(private_quota_bytes=99, public_quota_bytes=199),
    )
    assert updated == {
        "username": "alice",
        "private_quota_bytes": 99,
        "public_quota_bytes": 199,
        "private_used_bytes": 1,
        "public_used_bytes": 2,
    }
    assert save_calls == ["saved", "saved"]
