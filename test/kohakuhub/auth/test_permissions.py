"""Tests for authorization helpers."""

import pytest
from fastapi import HTTPException

from kohakuhub.auth.permissions import (
    check_namespace_permission,
    check_repo_delete_permission,
    check_repo_read_permission,
    check_repo_write_permission,
)
from kohakuhub.db_operations import get_repository, get_user_by_username

pytestmark = pytest.mark.usefixtures("prepared_backend_test_state")


def test_namespace_permission_allows_owner_namespace():
    owner = get_user_by_username("owner")
    assert check_namespace_permission("owner", owner) is True


def test_namespace_permission_allows_org_member():
    member = get_user_by_username("member")
    assert check_namespace_permission("acme-labs", member) is True


def test_namespace_permission_requires_admin_role_for_admin_only_paths():
    visitor = get_user_by_username("visitor")

    with pytest.raises(HTTPException) as exc:
        check_namespace_permission("acme-labs", visitor, require_admin=True)

    assert exc.value.status_code == 403


def test_repo_read_write_and_delete_permissions_cover_private_org_repo():
    member = get_user_by_username("member")
    visitor = get_user_by_username("visitor")
    outsider = get_user_by_username("outsider")
    private_repo = get_repository("dataset", "acme-labs", "private-dataset")

    assert check_repo_read_permission(private_repo, member) is True
    assert check_repo_write_permission(private_repo, member) is True

    assert check_repo_read_permission(private_repo, visitor) is True
    with pytest.raises(HTTPException):
        check_repo_write_permission(private_repo, visitor)
    with pytest.raises(HTTPException):
        check_repo_delete_permission(private_repo, visitor)

    with pytest.raises(HTTPException):
        check_repo_read_permission(private_repo, outsider)
