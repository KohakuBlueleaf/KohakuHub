"""Tests for deprecated organization utility helpers."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.org.util as org_util


def test_create_organization_creates_membership_and_rejects_duplicates(monkeypatch):
    created: list[tuple[object, object, str]] = []
    user = SimpleNamespace(username="owner")
    org = SimpleNamespace(username="acme-labs", is_org=True)

    monkeypatch.setattr(org_util, "get_organization", lambda name: None)
    monkeypatch.setattr(org_util, "create_org_op", lambda name, description: org)
    monkeypatch.setattr(org_util, "create_user_org_op", lambda user_obj, org_obj, role: created.append((user_obj, org_obj, role)))
    monkeypatch.setattr("kohakuhub.db.db.atomic", lambda: nullcontext())

    assert org_util.create_organization("acme-labs", "A test org", user) is org
    assert created == [(user, org, "super-admin")]

    monkeypatch.setattr(org_util, "get_organization", lambda name: org)
    with pytest.raises(HTTPException) as exc:
        org_util.create_organization("acme-labs", "A test org", user)
    assert exc.value.status_code == 400


def test_get_organization_details_delegates_to_db_operation(monkeypatch):
    org = SimpleNamespace(username="acme-labs", is_org=True)
    monkeypatch.setattr(org_util, "get_organization", lambda name: org)

    assert org_util.get_organization_details("acme-labs") is org


def test_add_member_to_organization_covers_validation_and_success(monkeypatch):
    db_ops = __import__("kohakuhub.db_operations", fromlist=["_sentinel"])
    user = SimpleNamespace(username="member")
    org = SimpleNamespace(id=1, username="acme-labs", is_org=True)
    created: list[tuple[object, object, str]] = []

    monkeypatch.setattr(db_ops, "get_user_by_username", lambda username: user if username == "member" else None)
    monkeypatch.setattr(db_ops, "get_user_by_id", lambda org_id: org if org_id == 1 else None)
    monkeypatch.setattr(db_ops, "get_user_organization", lambda user_obj, org_obj: None)
    monkeypatch.setattr(org_util, "create_user_org_op", lambda user_obj, org_obj, role: created.append((user_obj, org_obj, role)))

    org_util.add_member_to_organization(1, "member", "admin")
    assert created == [(user, org, "admin")]

    with pytest.raises(HTTPException, match="User not found"):
        org_util.add_member_to_organization(1, "missing", "admin")

    with pytest.raises(HTTPException, match="Organization not found"):
        org_util.add_member_to_organization(2, "member", "admin")

    monkeypatch.setattr(db_ops, "get_user_organization", lambda user_obj, org_obj: object())
    with pytest.raises(HTTPException, match="already a member"):
        org_util.add_member_to_organization(1, "member", "admin")


def test_remove_member_from_organization_covers_validation_and_success(monkeypatch):
    db_ops = __import__("kohakuhub.db_operations", fromlist=["_sentinel"])
    user = SimpleNamespace(username="member")
    org = SimpleNamespace(id=1, username="acme-labs", is_org=True)
    membership = SimpleNamespace(id=99)
    deleted: list[object] = []

    monkeypatch.setattr(db_ops, "get_user_by_username", lambda username: user if username == "member" else None)
    monkeypatch.setattr(db_ops, "get_user_by_id", lambda org_id: org if org_id == 1 else None)
    monkeypatch.setattr(db_ops, "get_user_organization", lambda user_obj, org_obj: membership)
    monkeypatch.setattr(db_ops, "delete_user_organization", lambda user_org: deleted.append(user_org))

    org_util.remove_member_from_organization(1, "member")
    assert deleted == [membership]

    with pytest.raises(HTTPException, match="User not found"):
        org_util.remove_member_from_organization(1, "missing")

    with pytest.raises(HTTPException, match="Organization not found"):
        org_util.remove_member_from_organization(2, "member")

    monkeypatch.setattr(db_ops, "get_user_organization", lambda user_obj, org_obj: None)
    with pytest.raises(HTTPException, match="not a member"):
        org_util.remove_member_from_organization(1, "member")


def test_get_user_organizations_requires_user_and_returns_memberships(monkeypatch):
    db_ops = __import__("kohakuhub.db_operations", fromlist=["_sentinel"])
    user = SimpleNamespace(id=1, username="member")
    memberships = [SimpleNamespace(org="acme-labs")]

    monkeypatch.setattr(db_ops, "get_user_by_id", lambda user_id: user if user_id == 1 else None)
    monkeypatch.setattr(db_ops, "list_user_organizations", lambda user_obj: memberships)

    assert org_util.get_user_organizations(1) == memberships

    with pytest.raises(HTTPException, match="User not found"):
        org_util.get_user_organizations(2)


def test_update_member_role_covers_validation_and_success(monkeypatch):
    db_ops = __import__("kohakuhub.db_operations", fromlist=["_sentinel"])
    user = SimpleNamespace(username="member")
    org = SimpleNamespace(id=1, username="acme-labs", is_org=True)
    membership = SimpleNamespace(id=1, role="member")
    updates: list[tuple[object, dict[str, str]]] = []

    monkeypatch.setattr(db_ops, "get_user_by_username", lambda username: user if username == "member" else None)
    monkeypatch.setattr(db_ops, "get_user_by_id", lambda org_id: org if org_id == 1 else None)
    monkeypatch.setattr(db_ops, "get_user_organization", lambda user_obj, org_obj: membership)
    monkeypatch.setattr(
        db_ops,
        "update_user_organization",
        lambda user_org, **kwargs: updates.append((user_org, kwargs)),
    )

    org_util.update_member_role(1, "member", "admin")
    assert updates == [(membership, {"role": "admin"})]

    with pytest.raises(HTTPException, match="User not found"):
        org_util.update_member_role(1, "missing", "admin")

    with pytest.raises(HTTPException, match="Organization not found"):
        org_util.update_member_role(2, "member", "admin")

    monkeypatch.setattr(db_ops, "get_user_organization", lambda user_obj, org_obj: None)
    with pytest.raises(HTTPException, match="not a member"):
        org_util.update_member_role(1, "member", "admin")
