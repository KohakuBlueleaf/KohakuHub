"""Unit tests for name validation helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import kohakuhub.api.validation as validation_api


class _Expr:
    def __and__(self, other):
        return self


class _Field:
    def __eq__(self, other):
        return _Expr()


class _Query:
    def __init__(self, items):
        self.items = list(items)

    def where(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self.items)


class _FakeRepositoryModel:
    repo_type = _Field()
    namespace = _Field()
    name = _Field()
    get_or_none_responses = []
    select_items = []

    @classmethod
    def get_or_none(cls, expr):
        if cls.get_or_none_responses:
            return cls.get_or_none_responses.pop(0)
        return None

    @classmethod
    def select(cls):
        return _Query(cls.select_items)


class _FakeUserModel:
    username = _Field()
    normalized_name = _Field()
    is_org = _Field()
    get_or_none_responses = []
    select_items = []

    @classmethod
    def get_or_none(cls, expr):
        if cls.get_or_none_responses:
            return cls.get_or_none_responses.pop(0)
        return None

    @classmethod
    def select(cls):
        return _Query(cls.select_items)


@pytest.fixture(autouse=True)
def _patch_models(monkeypatch):
    _FakeRepositoryModel.get_or_none_responses = []
    _FakeRepositoryModel.select_items = []
    _FakeUserModel.get_or_none_responses = []
    _FakeUserModel.select_items = []
    monkeypatch.setattr(validation_api, "Repository", _FakeRepositoryModel)
    monkeypatch.setattr(validation_api, "User", _FakeUserModel)


@pytest.mark.asyncio
async def test_check_name_availability_covers_exact_repository_conflict():
    _FakeRepositoryModel.get_or_none_responses = [SimpleNamespace(name="demo-model")]

    response = await validation_api.check_name_availability(
        validation_api.CheckNameRequest(
            name="demo-model",
            namespace="owner",
            type="model",
        )
    )

    assert response.available is False
    assert response.conflict_with == "owner/demo-model"
    assert "already exists" in response.message


@pytest.mark.asyncio
async def test_check_name_availability_covers_user_conflict_paths():
    _FakeUserModel.get_or_none_responses = [SimpleNamespace(username="taken-user")]

    exact_user = await validation_api.check_name_availability(
        validation_api.CheckNameRequest(name="taken-user")
    )
    assert exact_user.available is False
    assert exact_user.conflict_with == "taken-user"

    _FakeUserModel.get_or_none_responses = [None, None]
    _FakeUserModel.select_items = [SimpleNamespace(username="Taken_User")]

    normalized_user = await validation_api.check_name_availability(
        validation_api.CheckNameRequest(name="taken-user")
    )
    assert normalized_user.available is False
    assert normalized_user.conflict_with == "Taken_User"
    assert "case-insensitive" in normalized_user.message


@pytest.mark.asyncio
async def test_check_name_availability_covers_org_conflict_and_available_username():
    _FakeUserModel.get_or_none_responses = [None, SimpleNamespace(username="acme-team")]

    org_conflict = await validation_api.check_name_availability(
        validation_api.CheckNameRequest(name="acme-team")
    )
    assert org_conflict.available is False
    assert org_conflict.conflict_with == "acme-team"

    _FakeUserModel.get_or_none_responses = [None, None]
    _FakeUserModel.select_items = []

    available = await validation_api.check_name_availability(
        validation_api.CheckNameRequest(name="brand-new-user")
    )
    assert available.available is True
    assert available.message == "Name is available"
