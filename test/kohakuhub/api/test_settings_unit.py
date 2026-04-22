"""Unit tests for settings routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.settings as settings_api
import kohakuhub.db_operations as db_ops


def _async_return(value=None):
    async def _inner(*args, **kwargs):
        return value

    return _inner


@pytest.mark.asyncio
async def test_user_settings_namespace_type_and_profile_cover_validation_and_fallback(
    monkeypatch,
):
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    updated_users = []
    user = SimpleNamespace(
        id=1,
        username="alice",
        full_name="Alice",
        bio="Bio",
        website="https://example.com",
        social_media="{bad-json",
        created_at=now,
    )
    org = SimpleNamespace(username="org-team")

    monkeypatch.setattr(
        settings_api, "update_user", lambda target, **kwargs: updated_users.append(kwargs)
    )
    monkeypatch.setattr(settings_api, "get_user_by_email_excluding_id", lambda email, user_id: None)

    with pytest.raises(HTTPException) as wrong_user:
        await settings_api.update_user_settings(
            "alice",
            settings_api.UpdateUserSettingsRequest(full_name="Alice"),
            user=SimpleNamespace(username="bob", id=2),
        )
    assert wrong_user.value.status_code == 403

    monkeypatch.setattr(
        settings_api,
        "get_user_by_email_excluding_id",
        lambda email, user_id: SimpleNamespace(id=9),
    )
    with pytest.raises(HTTPException) as email_conflict:
        await settings_api.update_user_settings(
            "alice",
            settings_api.UpdateUserSettingsRequest(email="taken@example.com"),
            user=SimpleNamespace(username="alice", id=1),
        )
    assert email_conflict.value.detail == "Email already in use"

    monkeypatch.setattr(settings_api, "get_user_by_email_excluding_id", lambda email, user_id: None)
    with pytest.raises(HTTPException) as invalid_social:
        await settings_api.update_user_settings(
            "alice",
            settings_api.UpdateUserSettingsRequest.model_construct(social_media="bad"),
            user=SimpleNamespace(username="alice", id=1),
        )
    assert invalid_social.value.detail == "social_media must be a dictionary"

    updated = await settings_api.update_user_settings(
        "alice",
        settings_api.UpdateUserSettingsRequest(
            email="alice@example.com",
            full_name="Alice L",
            social_media={"github": "alice"},
        ),
        user=SimpleNamespace(username="alice", id=1),
    )
    assert updated["success"] is True
    assert updated_users[-1] == {
        "email": "alice@example.com",
        "email_verified": False,
        "full_name": "Alice L",
        "social_media": '{"github": "alice"}',
    }

    monkeypatch.setattr(
        settings_api,
        "get_user_by_username",
        lambda username: SimpleNamespace(is_org=False) if username == "alice" else None,
    )
    monkeypatch.setattr(
        settings_api,
        "get_organization",
        lambda username: org if username == "org-team" else None,
    )
    assert await settings_api.get_namespace_type("alice", request=None) == {
        "type": "user",
        "_source": "local",
    }
    assert await settings_api.get_namespace_type("org-team", request=None) == {
        "type": "org",
        "_source": "local",
    }

    monkeypatch.setattr(settings_api, "get_user_by_username", lambda username: None)
    monkeypatch.setattr(settings_api, "get_organization", lambda username: None)
    with pytest.raises(HTTPException) as no_fallback:
        await settings_api.get_namespace_type("ghost", request=None, fallback=False)
    assert no_fallback.value.status_code == 404

    monkeypatch.setattr(
        "kohakuhub.api.fallback.operations.try_fallback_user_profile",
        _async_return({"_hf_type": "organization", "_source": "hf"}),
    )
    assert await settings_api.get_namespace_type("remote-org", request=None) == {
        "type": "org",
        "_source": "hf",
    }

    monkeypatch.setattr(
        "kohakuhub.api.fallback.operations.try_fallback_user_profile",
        _async_return({"_hf_type": "user", "_source": "hf"}),
    )
    assert await settings_api.get_namespace_type("remote-user", request=None) == {
        "type": "user",
        "_source": "hf",
    }

    monkeypatch.setattr(
        "kohakuhub.api.fallback.operations.try_fallback_user_profile",
        _async_return(None),
    )
    with pytest.raises(HTTPException) as fallback_miss:
        await settings_api.get_namespace_type("missing", request=None)
    assert fallback_miss.value.status_code == 404

    monkeypatch.setattr(settings_api, "get_user_by_username", lambda username: None)
    with pytest.raises(HTTPException) as missing_profile:
        await settings_api.get_user_profile.__wrapped__("ghost", request=None)
    assert missing_profile.value.status_code == 404

    monkeypatch.setattr(settings_api, "get_user_by_username", lambda username: user)
    profile = await settings_api.get_user_profile.__wrapped__("alice", request=None)
    assert profile["social_media"] is None
    assert profile["_source"] == "local"


@pytest.mark.asyncio
async def test_organization_settings_and_profile_cover_not_found_auth_and_json(
    monkeypatch,
):
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    updates = []
    org = SimpleNamespace(
        username="org-team",
        description="desc",
        bio="bio",
        website="https://org.example.com",
        social_media="{bad-json",
        created_at=now,
    )

    monkeypatch.setattr(settings_api, "get_organization", lambda org_name: None)
    with pytest.raises(HTTPException) as missing_org:
        await settings_api.update_organization_settings(
            "org-team",
            settings_api.UpdateOrganizationSettingsRequest(description="updated"),
            user=SimpleNamespace(username="alice"),
        )
    assert missing_org.value.status_code == 404

    monkeypatch.setattr(settings_api, "get_organization", lambda org_name: org)
    monkeypatch.setattr(settings_api, "get_user_organization", lambda user, organization: None)
    with pytest.raises(HTTPException) as unauthorized:
        await settings_api.update_organization_settings(
            "org-team",
            settings_api.UpdateOrganizationSettingsRequest(description="updated"),
            user=SimpleNamespace(username="alice"),
        )
    assert unauthorized.value.status_code == 403

    monkeypatch.setattr(
        settings_api,
        "get_user_organization",
        lambda user, organization: SimpleNamespace(role="admin"),
    )
    monkeypatch.setattr(
        settings_api,
        "update_organization",
        lambda organization, **kwargs: updates.append(kwargs),
    )
    with pytest.raises(HTTPException) as invalid_social:
        await settings_api.update_organization_settings(
            "org-team",
            settings_api.UpdateOrganizationSettingsRequest.model_construct(social_media="bad"),
            user=SimpleNamespace(username="alice"),
        )
    assert invalid_social.value.detail == "social_media must be a dictionary"

    updated = await settings_api.update_organization_settings(
        "org-team",
        settings_api.UpdateOrganizationSettingsRequest(
            description="new desc",
            bio="new bio",
            website="https://new.example.com",
            social_media={"github": "org-team"},
        ),
        user=SimpleNamespace(username="alice"),
    )
    assert updated["success"] is True
    assert updates[-1] == {
        "description": "new desc",
        "bio": "new bio",
        "website": "https://new.example.com",
        "social_media": '{"github": "org-team"}',
    }

    monkeypatch.setattr(settings_api, "get_organization", lambda org_name: None)
    with pytest.raises(HTTPException) as missing_org_profile:
        await settings_api.get_organization_profile("org-team")
    assert missing_org_profile.value.status_code == 404

    monkeypatch.setattr(settings_api, "get_organization", lambda org_name: org)
    monkeypatch.setattr(
        settings_api,
        "list_organization_members",
        lambda organization: [SimpleNamespace(), SimpleNamespace()],
    )
    profile = await settings_api.get_organization_profile("org-team")
    assert profile["social_media"] is None
    assert profile["member_count"] == 2


@pytest.mark.asyncio
async def test_repo_settings_and_lfs_settings_cover_validation_quota_and_defaults(
    monkeypatch,
):
    repo_row = SimpleNamespace(
        private=False,
        lfs_threshold_bytes=None,
        lfs_keep_versions=None,
        lfs_suffix_rules="{bad-json",
    )
    update_calls = []

    monkeypatch.setattr(
        settings_api,
        "hf_repo_not_found",
        lambda repo_id, repo_type: SimpleNamespace(status_code=404, repo_id=repo_id, repo_type=repo_type),
    )
    monkeypatch.setattr(settings_api, "get_repository", lambda repo_type, namespace, name: None)
    not_found = await settings_api.update_repo_settings(
        "model",
        "alice",
        "demo",
        settings_api.UpdateRepoSettingsPayload(),
        user=SimpleNamespace(username="alice"),
    )
    assert not_found.status_code == 404

    monkeypatch.setattr(
        settings_api,
        "get_repository",
        lambda repo_type, namespace, name: repo_row,
    )
    monkeypatch.setattr(
        settings_api,
        "check_repo_delete_permission",
        lambda repo, user: None,
    )

    with pytest.raises(HTTPException) as bad_threshold:
        await settings_api.update_repo_settings(
            "model",
            "alice",
            "demo",
            settings_api.UpdateRepoSettingsPayload(lfs_threshold_bytes=999999),
            user=SimpleNamespace(username="alice"),
        )
    assert bad_threshold.value.status_code == 400

    with pytest.raises(HTTPException) as bad_keep_versions:
        await settings_api.update_repo_settings(
            "model",
            "alice",
            "demo",
            settings_api.UpdateRepoSettingsPayload(lfs_keep_versions=1),
            user=SimpleNamespace(username="alice"),
        )
    assert bad_keep_versions.value.status_code == 400

    with pytest.raises(HTTPException) as bad_suffix:
        await settings_api.update_repo_settings(
            "model",
            "alice",
            "demo",
            settings_api.UpdateRepoSettingsPayload(lfs_suffix_rules=["bin"]),
            user=SimpleNamespace(username="alice"),
        )
    assert bad_suffix.value.status_code == 400

    with pytest.raises(HTTPException) as bad_visibility:
        await settings_api.update_repo_settings(
            "model",
            "alice",
            "demo",
            settings_api.UpdateRepoSettingsPayload(visibility="protected"),
            user=SimpleNamespace(username="alice"),
        )
    assert bad_visibility.value.status_code == 400

    monkeypatch.setattr(
        settings_api,
        "calculate_repository_storage",
        _async_return({"total_bytes": 55}),
    )
    monkeypatch.setattr(settings_api, "get_organization", lambda namespace: None)
    monkeypatch.setattr(
        settings_api,
        "check_quota",
        lambda namespace, additional_bytes, is_private, is_org: (False, "quota exceeded"),
    )
    with pytest.raises(HTTPException) as quota_error:
        await settings_api.update_repo_settings(
            "model",
            "alice",
            "demo",
            settings_api.UpdateRepoSettingsPayload(private=True),
            user=SimpleNamespace(username="alice"),
        )
    assert quota_error.value.detail == {
        "error": "quota exceeded",
        "repo_size_bytes": 55,
    }

    monkeypatch.setattr(
        settings_api,
        "check_quota",
        lambda namespace, additional_bytes, is_private, is_org: (True, None),
    )
    monkeypatch.setattr(
        settings_api,
        "update_repository",
        lambda repo, **kwargs: update_calls.append(kwargs),
    )
    repo_row.private = False
    updated = await settings_api.update_repo_settings(
        "model",
        "alice",
        "demo",
        settings_api.UpdateRepoSettingsPayload(
            private=True,
            lfs_threshold_bytes=1000000,
            lfs_keep_versions=3,
            lfs_suffix_rules=[".bin", ".pt"],
        ),
        user=SimpleNamespace(username="alice"),
    )
    assert updated["success"] is True
    assert update_calls[-1] == {
        "lfs_threshold_bytes": 1000000,
        "lfs_keep_versions": 3,
        "lfs_suffix_rules": '[".bin", ".pt"]',
        "private": True,
    }

    repo_row.private = False
    updated_visibility = await settings_api.update_repo_settings(
        "model",
        "alice",
        "demo",
        settings_api.UpdateRepoSettingsPayload(visibility="public"),
        user=SimpleNamespace(username="alice"),
    )
    assert updated_visibility["success"] is True
    assert update_calls[-1] == {"private": False}

    monkeypatch.setattr(settings_api, "get_repository", lambda repo_type, namespace, name: None)
    lfs_not_found = await settings_api.get_repo_lfs_settings(
        "model",
        "alice",
        "missing",
        user=SimpleNamespace(username="alice"),
    )
    assert lfs_not_found.status_code == 404

    monkeypatch.setattr(
        settings_api,
        "get_repository",
        lambda repo_type, namespace, name: repo_row,
    )
    monkeypatch.setattr(db_ops, "get_effective_lfs_threshold", lambda repo: 1234567)
    monkeypatch.setattr(db_ops, "get_effective_lfs_keep_versions", lambda repo: 9)
    monkeypatch.setattr(db_ops, "get_effective_lfs_suffix_rules", lambda repo: [".bin"])
    settings_payload = await settings_api.get_repo_lfs_settings(
        "model",
        "alice",
        "demo",
        user=SimpleNamespace(username="alice"),
    )
    assert settings_payload["lfs_suffix_rules"] is None
    assert settings_payload["lfs_threshold_bytes_source"] == "server_default"
    assert settings_payload["lfs_keep_versions_source"] == "server_default"
    assert settings_payload["lfs_suffix_rules_source"] == "merged"
