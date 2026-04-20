"""Unit tests for permission branches not covered by integration tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.auth.permissions as permissions


def test_namespace_permission_covers_admin_bypass_and_common_failures(monkeypatch):
    user = SimpleNamespace(username="owner")
    org = SimpleNamespace(username="acme")

    assert permissions.check_namespace_permission("any", None, is_admin=True) is True

    with pytest.raises(HTTPException) as no_user_exc:
        permissions.check_namespace_permission("acme", None)
    assert no_user_exc.value.status_code == 403

    monkeypatch.setattr(permissions, "get_organization", lambda namespace: None)
    with pytest.raises(HTTPException) as missing_org_exc:
        permissions.check_namespace_permission("missing-org", user)
    assert missing_org_exc.value.status_code == 403

    monkeypatch.setattr(permissions, "get_organization", lambda namespace: org)
    monkeypatch.setattr(permissions, "get_user_organization", lambda user, org: None)
    with pytest.raises(HTTPException) as membership_exc:
        permissions.check_namespace_permission("acme", user)
    assert membership_exc.value.status_code == 403


def test_repo_read_permission_covers_admin_public_owner_and_unauthenticated_paths(monkeypatch):
    public_repo = SimpleNamespace(namespace="owner", full_id="owner/public", private=False)
    private_repo = SimpleNamespace(namespace="owner", full_id="owner/private", private=True)
    owner = SimpleNamespace(username="owner")

    assert permissions.check_repo_read_permission(private_repo, None, is_admin=True) is True
    assert permissions.check_repo_read_permission(public_repo, None) is True
    assert permissions.check_repo_read_permission(private_repo, owner) is True

    with pytest.raises(HTTPException) as unauth_exc:
        permissions.check_repo_read_permission(private_repo, None)
    assert unauth_exc.value.status_code == 401


def test_repo_write_and_delete_permission_cover_admin_owner_and_org_admin(monkeypatch):
    org = SimpleNamespace(username="acme")
    repo = SimpleNamespace(namespace="acme", full_id="acme/repo", private=True)
    owner_repo = SimpleNamespace(namespace="owner", full_id="owner/repo", private=True)
    user = SimpleNamespace(username="owner")
    admin_membership = SimpleNamespace(role="admin")

    assert permissions.check_repo_write_permission(repo, None, is_admin=True) is True
    assert permissions.check_repo_write_permission(owner_repo, user) is True
    assert permissions.check_repo_delete_permission(repo, None, is_admin=True) is True
    assert permissions.check_repo_delete_permission(owner_repo, user) is True

    with pytest.raises(HTTPException) as write_no_user_exc:
        permissions.check_repo_write_permission(repo, None)
    assert write_no_user_exc.value.status_code == 403

    with pytest.raises(HTTPException) as delete_no_user_exc:
        permissions.check_repo_delete_permission(repo, None)
    assert delete_no_user_exc.value.status_code == 403

    monkeypatch.setattr(permissions, "get_organization", lambda namespace: org)
    monkeypatch.setattr(permissions, "get_user_organization", lambda user, org: admin_membership)
    assert permissions.check_repo_delete_permission(repo, user) is True
