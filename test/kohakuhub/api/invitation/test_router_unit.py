"""Unit tests for invitation routes."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

invitation_router = importlib.import_module("kohakuhub.api.invitation.router")


class _AtomicContext:
    def __init__(self, state: dict):
        self.state = state

    def __enter__(self):
        self.state["entered"] = self.state.get("entered", 0) + 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.state["exited"] = self.state.get("exited", 0) + 1
        return False


def _async_to_thread(func, *args):
    async def _inner():
        return func(*args)

    return _inner()


@pytest.mark.asyncio
async def test_create_org_invitation_covers_validation_membership_and_success(
    monkeypatch,
):
    atomic_state = {}
    created = []
    sent_emails = []
    inviter = SimpleNamespace(username="alice")
    org = SimpleNamespace(id=7, username="org-team")

    monkeypatch.setattr(
        invitation_router,
        "db",
        SimpleNamespace(atomic=lambda: _AtomicContext(atomic_state)),
    )
    monkeypatch.setattr(invitation_router.cfg.app, "base_url", "https://hub.example.com")
    monkeypatch.setattr(invitation_router.secrets, "token_urlsafe", lambda length: "invite-token")
    monkeypatch.setattr(invitation_router.asyncio, "to_thread", _async_to_thread)
    monkeypatch.setattr(
        invitation_router,
        "create_invitation",
        lambda **kwargs: created.append(kwargs) or SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        invitation_router,
        "send_org_invitation_email",
        lambda *args: sent_emails.append(args) or True,
    )

    with pytest.raises(HTTPException) as invalid_role:
        await invitation_router.create_org_invitation(
            "org-team",
            invitation_router.CreateOrgInvitationRequest(role="owner"),
            user=inviter,
        )
    assert invalid_role.value.status_code == 400

    monkeypatch.setattr(invitation_router, "get_organization", lambda org_name: None)
    with pytest.raises(HTTPException) as missing_org:
        await invitation_router.create_org_invitation(
            "org-team",
            invitation_router.CreateOrgInvitationRequest(role="member"),
            user=inviter,
        )
    assert missing_org.value.status_code == 404

    monkeypatch.setattr(invitation_router, "get_organization", lambda org_name: org)
    monkeypatch.setattr(
        invitation_router,
        "get_user_organization",
        lambda user, organization: None,
    )
    with pytest.raises(HTTPException) as unauthorized:
        await invitation_router.create_org_invitation(
            "org-team",
            invitation_router.CreateOrgInvitationRequest(role="member"),
            user=inviter,
        )
    assert unauthorized.value.status_code == 403

    invitee = SimpleNamespace(username="bob")

    def _existing_membership(user, organization):
        if user is inviter:
            return SimpleNamespace(role="admin")
        return SimpleNamespace(role="member")

    monkeypatch.setattr(invitation_router, "get_user_organization", _existing_membership)
    monkeypatch.setattr(invitation_router, "get_user_by_email", lambda email: invitee)
    with pytest.raises(HTTPException) as existing_member:
        await invitation_router.create_org_invitation(
            "org-team",
            invitation_router.CreateOrgInvitationRequest(
                email="bob@example.com",
                role="member",
            ),
            user=inviter,
        )
    assert existing_member.value.status_code == 400

    monkeypatch.setattr(
        invitation_router,
        "get_user_organization",
        lambda user, organization: SimpleNamespace(role="super-admin")
        if user is inviter
        else None,
    )
    monkeypatch.setattr(invitation_router, "get_user_by_email", lambda email: None)
    email_invitation = await invitation_router.create_org_invitation(
        "org-team",
        invitation_router.CreateOrgInvitationRequest(
            email="bob@example.com",
            role="admin",
            max_usage=3,
        ),
        user=inviter,
    )
    assert email_invitation["invitation_link"] == "https://hub.example.com/invite/invite-token"
    assert email_invitation["is_reusable"] is True
    assert sent_emails[-1] == (
        "bob@example.com",
        "org-team",
        "alice",
        "invite-token",
        "admin",
    )

    reusable_invitation = await invitation_router.create_org_invitation(
        "org-team",
        invitation_router.CreateOrgInvitationRequest(
            role="member",
            max_usage=None,
        ),
        user=inviter,
    )
    assert reusable_invitation["is_reusable"] is False
    assert created[-1]["action"] == "join_org"
    assert atomic_state["entered"] >= 2


@pytest.mark.asyncio
async def test_invitation_details_and_handlers_cover_json_errors_and_success(monkeypatch):
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    used = []
    memberships = []
    user = SimpleNamespace(username="alice")
    org = SimpleNamespace(id=7, username="org-team")

    monkeypatch.setattr(invitation_router, "get_invitation", lambda token: None)
    with pytest.raises(HTTPException) as missing_invitation:
        await invitation_router.get_invitation_details("missing")
    assert missing_invitation.value.status_code == 404

    invalid_json_invitation = SimpleNamespace(
        parameters="{bad-json",
        action="join_org",
        created_by=None,
        expires_at=now,
        max_usage=None,
        usage_count=0,
    )
    monkeypatch.setattr(
        invitation_router,
        "get_invitation",
        lambda token: invalid_json_invitation,
    )
    monkeypatch.setattr(
        invitation_router,
        "check_invitation_available",
        lambda invitation: (False, "expired"),
    )
    with pytest.raises(HTTPException) as invalid_json:
        await invitation_router.get_invitation_details("bad-json")
    assert invalid_json.value.status_code == 500

    valid_invitation = SimpleNamespace(
        parameters='{"org_name": "org-team", "role": "admin", "email": "bob@example.com"}',
        action="join_org",
        created_by=None,
        expires_at=now,
        max_usage=5,
        usage_count=1,
    )
    monkeypatch.setattr(invitation_router, "get_invitation", lambda token: valid_invitation)
    details = await invitation_router.get_invitation_details("valid")
    assert details["inviter_username"] == "Unknown"
    assert details["is_expired"] is True
    assert details["email"] == "bob@example.com"

    monkeypatch.setattr(invitation_router, "get_user_by_id", lambda user_id: None)
    with pytest.raises(HTTPException) as missing_join_org:
        invitation_router._handle_join_org_action(
            SimpleNamespace(), user, {"org_id": 7, "org_name": "org-team"}
        )
    assert missing_join_org.value.status_code == 404

    monkeypatch.setattr(invitation_router, "get_user_by_id", lambda user_id: org)
    monkeypatch.setattr(
        invitation_router,
        "get_user_organization",
        lambda target_user, organization: SimpleNamespace(role="member"),
    )
    with pytest.raises(HTTPException) as already_member:
        invitation_router._handle_join_org_action(
            SimpleNamespace(), user, {"org_id": 7, "org_name": "org-team"}
        )
    assert already_member.value.status_code == 400

    monkeypatch.setattr(
        invitation_router,
        "get_user_organization",
        lambda target_user, organization: None,
    )
    monkeypatch.setattr(
        invitation_router,
        "db",
        SimpleNamespace(atomic=lambda: _AtomicContext({})),
    )
    monkeypatch.setattr(
        invitation_router,
        "create_user_organization",
        lambda target_user, organization, role: memberships.append(
            (target_user.username, organization.username, role)
        ),
    )
    monkeypatch.setattr(
        invitation_router,
        "mark_invitation_used",
        lambda invitation, target_user: used.append(target_user.username),
    )
    joined = invitation_router._handle_join_org_action(
        SimpleNamespace(),
        user,
        {"org_id": 7, "org_name": "org-team", "role": "admin"},
    )
    assert joined["org_name"] == "org-team"
    assert memberships[-1] == ("alice", "org-team", "admin")
    assert used[-1] == "alice"

    invitation_router._handle_register_account_action(
        SimpleNamespace(),
        user,
        {"org_id": 7, "org_name": "org-team", "role": "member"},
    )
    assert memberships[-1] == ("alice", "org-team", "member")
    invitation_router._handle_register_account_action(SimpleNamespace(), user, {})


@pytest.mark.asyncio
async def test_accept_list_and_delete_invitation_cover_dispatch_and_auth_paths(
    monkeypatch,
):
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    deleted = []
    user = SimpleNamespace(username="alice")
    org = SimpleNamespace(id=7, username="org-team")

    monkeypatch.setattr(invitation_router, "get_invitation", lambda token: None)
    with pytest.raises(HTTPException) as missing_accept:
        await invitation_router.accept_invitation("missing", user=user)
    assert missing_accept.value.status_code == 404

    available_invitation = SimpleNamespace(action="join_org", parameters="{}")
    monkeypatch.setattr(
        invitation_router,
        "get_invitation",
        lambda token: available_invitation,
    )
    monkeypatch.setattr(
        invitation_router,
        "check_invitation_available",
        lambda invitation: (False, "already used"),
    )
    with pytest.raises(HTTPException) as unavailable:
        await invitation_router.accept_invitation("used", user=user)
    assert unavailable.value.detail == "already used"

    monkeypatch.setattr(
        invitation_router,
        "check_invitation_available",
        lambda invitation: (True, None),
    )
    invalid_json_invitation = SimpleNamespace(action="join_org", parameters="{bad-json")
    monkeypatch.setattr(invitation_router, "get_invitation", lambda token: invalid_json_invitation)
    with pytest.raises(HTTPException) as invalid_json:
        await invitation_router.accept_invitation("bad-json", user=user)
    assert invalid_json.value.status_code == 500

    unknown_action = SimpleNamespace(action="unknown", parameters="{}")
    monkeypatch.setattr(invitation_router, "get_invitation", lambda token: unknown_action)
    with pytest.raises(HTTPException) as unknown_action_error:
        await invitation_router.accept_invitation("unknown", user=user)
    assert unknown_action_error.value.status_code == 400

    monkeypatch.setattr(
        invitation_router,
        "_handle_join_org_action",
        lambda invitation, target_user, params: {"handler": "join_org"},
    )
    monkeypatch.setattr(
        invitation_router,
        "_handle_register_account_action",
        lambda invitation, target_user, params: {"handler": "register_account"},
    )
    join_org_invitation = SimpleNamespace(action="join_org", parameters="{}")
    register_invitation = SimpleNamespace(action="register_account", parameters="{}")
    monkeypatch.setattr(invitation_router, "get_invitation", lambda token: join_org_invitation)
    assert await invitation_router.accept_invitation("join", user=user) == {
        "handler": "join_org"
    }
    monkeypatch.setattr(invitation_router, "get_invitation", lambda token: register_invitation)
    assert await invitation_router.accept_invitation("register", user=user) == {
        "handler": "register_account"
    }

    monkeypatch.setattr(invitation_router, "get_organization", lambda org_name: None)
    with pytest.raises(HTTPException) as missing_list_org:
        await invitation_router.list_organization_invitations("org-team", user=user)
    assert missing_list_org.value.status_code == 404

    monkeypatch.setattr(invitation_router, "get_organization", lambda org_name: org)
    monkeypatch.setattr(
        invitation_router,
        "get_user_organization",
        lambda target_user, organization: None,
    )
    with pytest.raises(HTTPException) as unauthorized_list:
        await invitation_router.list_organization_invitations("org-team", user=user)
    assert unauthorized_list.value.status_code == 403

    monkeypatch.setattr(
        invitation_router,
        "get_user_organization",
        lambda target_user, organization: SimpleNamespace(role="admin"),
    )
    monkeypatch.setattr(
        invitation_router,
        "check_invitation_available",
        lambda invitation: (invitation.token == "pending", None),
    )
    monkeypatch.setattr(
        invitation_router,
        "list_org_invitations",
        lambda organization: [
            SimpleNamespace(
                id=1,
                token="pending",
                parameters='{"email": "bob@example.com", "role": "member"}',
                created_by=SimpleNamespace(username="alice"),
                created_at=now,
                expires_at=now,
                max_usage=None,
                usage_count=0,
                used_at=None,
            ),
            SimpleNamespace(
                id=2,
                token="broken",
                parameters="{bad-json",
                created_by=None,
                created_at=now,
                expires_at=now,
                max_usage=1,
                usage_count=1,
                used_at=now,
            ),
        ],
    )
    listed = await invitation_router.list_organization_invitations("org-team", user=user)
    assert listed["invitations"] == [
        {
            "id": 1,
            "token": "pending",
            "email": "bob@example.com",
            "role": "member",
            "created_by": "alice",
            "created_at": now.isoformat(),
            "expires_at": now.isoformat(),
            "max_usage": None,
            "usage_count": 0,
            "is_reusable": False,
            "is_available": True,
            "used_at": None,
            "is_pending": True,
        }
    ]

    monkeypatch.setattr(invitation_router, "get_invitation", lambda token: None)
    with pytest.raises(HTTPException) as missing_delete:
        await invitation_router.delete_invitation_endpoint("missing", user=user)
    assert missing_delete.value.status_code == 404

    monkeypatch.setattr(
        invitation_router,
        "get_invitation",
        lambda token: SimpleNamespace(action="join_org", parameters="{bad-json"),
    )
    with pytest.raises(HTTPException) as invalid_delete_json:
        await invitation_router.delete_invitation_endpoint("bad-json", user=user)
    assert invalid_delete_json.value.status_code == 500

    join_invitation = SimpleNamespace(
        action="join_org",
        parameters='{"org_id": 7}',
    )
    monkeypatch.setattr(invitation_router, "get_invitation", lambda token: join_invitation)
    monkeypatch.setattr(invitation_router, "get_user_by_id", lambda user_id: None)
    with pytest.raises(HTTPException) as missing_delete_org:
        await invitation_router.delete_invitation_endpoint("missing-org", user=user)
    assert missing_delete_org.value.status_code == 404

    monkeypatch.setattr(invitation_router, "get_user_by_id", lambda user_id: org)
    monkeypatch.setattr(
        invitation_router,
        "get_user_organization",
        lambda target_user, organization: None,
    )
    with pytest.raises(HTTPException) as unauthorized_delete:
        await invitation_router.delete_invitation_endpoint("unauthorized", user=user)
    assert unauthorized_delete.value.status_code == 403

    monkeypatch.setattr(
        invitation_router,
        "get_user_organization",
        lambda target_user, organization: SimpleNamespace(role="super-admin"),
    )
    monkeypatch.setattr(
        invitation_router,
        "delete_invitation",
        lambda invitation: deleted.append(invitation.action),
    )
    deleted_join = await invitation_router.delete_invitation_endpoint("delete-join", user=user)
    assert deleted_join["success"] is True

    register_invitation = SimpleNamespace(action="register_account", parameters="{}")
    monkeypatch.setattr(invitation_router, "get_invitation", lambda token: register_invitation)
    deleted_register = await invitation_router.delete_invitation_endpoint("delete-register", user=user)
    assert deleted_register["success"] is True
    assert deleted == ["join_org", "register_account"]
