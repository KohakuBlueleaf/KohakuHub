"""Tests for database helper operations."""

from datetime import datetime, timedelta, timezone

import pytest

from kohakuhub.config import cfg
from kohakuhub.db import DailyRepoStats, RepositoryLike
from kohakuhub.api.quota.util import (
    get_repo_storage_info,
    get_storage_info,
    increment_storage,
    set_quota,
    set_repo_quota,
)
from kohakuhub.db_operations import (
    check_invitation_available,
    cleanup_expired_confirmation_tokens,
    consume_confirmation_token,
    count_repository_sessions,
    create_commit,
    create_confirmation_token,
    create_download_session,
    create_email_verification,
    create_invitation,
    create_lfs_history,
    create_organization,
    create_repository,
    create_repository_like,
    create_session,
    create_ssh_key,
    create_staging_upload,
    create_token,
    create_user,
    create_user_organization,
    delete_email_verification,
    delete_file,
    delete_repository_like,
    delete_session,
    delete_ssh_key,
    delete_staging_upload,
    delete_token,
    delete_user,
    delete_user_organization,
    get_commit,
    get_confirmation_token,
    get_daily_stat,
    get_download_session,
    get_effective_lfs_keep_versions,
    get_effective_lfs_suffix_rules,
    get_effective_lfs_threshold,
    get_email_verification,
    get_file,
    get_file_by_sha256,
    get_merged_external_tokens,
    get_repository,
    get_repository_by_full_id,
    get_repository_like,
    get_ssh_key_by_fingerprint,
    get_ssh_key_by_id,
    get_user_by_email,
    get_user_by_email_excluding_id,
    get_user_by_id,
    get_user_by_username,
    get_user_organization,
    increment_download_session_files,
    list_commits_by_repo,
    list_daily_stats,
    list_lfs_history,
    list_organization_members,
    list_repositories,
    list_repository_likers,
    list_user_organizations,
    list_user_ssh_keys,
    list_user_tokens,
    mark_invitation_used,
    refresh_lfs_history_timestamp,
    set_user_external_token,
    should_use_lfs,
    update_file,
    update_organization,
    update_repository,
    update_ssh_key,
    update_token_last_used,
    update_user,
    update_user_organization,
    get_lfs_history_entry,
    get_invitation,
    get_invitation_by_id,
    list_org_invitations,
    delete_expired_invitations,
    list_user_tokens,
    create_file,
    get_latest_daily_stat,
)

pytestmark = pytest.mark.usefixtures("prepared_backend_test_state")


def test_set_user_external_token_and_merge_with_header_overrides():
    owner = get_user_by_username("owner")
    set_user_external_token(owner, "https://huggingface.co", "db-token")
    set_user_external_token(owner, "https://mirror.local", "mirror-token")

    merged = get_merged_external_tokens(
        owner,
        {"https://huggingface.co": "header-token"},
    )

    assert merged == {
        "https://huggingface.co": "header-token",
        "https://mirror.local": "mirror-token",
    }


def test_should_use_lfs_uses_size_threshold_and_suffix_rules():
    repo = get_repository("model", "owner", "demo-model")

    assert should_use_lfs(repo, "weights/model.safetensors", 12) is True
    assert should_use_lfs(repo, "small.bin", 8) is True
    assert should_use_lfs(repo, "docs/readme.md", 8) is False
    assert should_use_lfs(repo, "docs/large.txt", 4096) is True


def test_invitation_usage_and_expiration_rules():
    owner = get_user_by_username("owner")
    member = get_user_by_username("member")
    invitation = create_invitation(
        token="invite-123",
        action="join_org",
        parameters='{"org":"acme-labs"}',
        created_by=owner,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        max_usage=2,
    )

    assert check_invitation_available(invitation) == (True, None)

    mark_invitation_used(invitation, member)
    assert check_invitation_available(invitation) == (True, None)

    mark_invitation_used(invitation, owner)
    available, message = check_invitation_available(invitation)
    assert available is False
    assert "maximum usage limit" in message


def test_confirmation_token_is_single_use():
    token = create_confirmation_token(
        action_type="delete_s3_prefix",
        action_data={"prefix": "owner/demo"},
        ttl_seconds=60,
    )

    assert consume_confirmation_token(token.token) == {"prefix": "owner/demo"}
    assert consume_confirmation_token(token.token) is None


def test_like_and_download_session_helpers_update_state():
    owner = get_user_by_username("owner")
    repo = get_repository("dataset", "acme-labs", "private-dataset")

    create_repository_like(repo, owner)
    assert RepositoryLike.select().where(RepositoryLike.repository == repo).count() == 1
    assert delete_repository_like(repo, owner) == 1

    session = create_download_session(
        repository=repo,
        session_id="download-session",
        time_bucket=123,
        first_file="data/train.jsonl",
        user=owner,
    )
    increment_download_session_files(session.id)
    session = session.__class__.get_by_id(session.id)

    assert session.file_count == 2
    assert count_repository_sessions(repo) == 1
    assert get_daily_stat(repo, datetime.now(timezone.utc).date()) is None


def test_user_org_repo_session_and_token_helpers_cover_crud():
    user = create_user(
        username="helper-user",
        email="helper-user@example.com",
        password_hash="hashed",
    )
    assert user.private_quota_bytes == cfg.quota.default_user_private_quota_bytes
    assert user.public_quota_bytes == cfg.quota.default_user_public_quota_bytes
    assert get_user_by_id(user.id).username == "helper-user"
    assert get_user_by_email("helper-user@example.com").username == "helper-user"
    assert get_user_by_email_excluding_id("helper-user@example.com", 999).username == "helper-user"

    update_user(user, full_name="Helper User", bio="Testing")
    assert get_user_by_id(user.id).full_name == "Helper User"

    org = create_organization("helper-org", description="Helper Org")
    assert org.private_quota_bytes == cfg.quota.default_org_private_quota_bytes
    assert org.public_quota_bytes == cfg.quota.default_org_public_quota_bytes
    update_organization(org, website="https://helper.example.com")
    assert get_user_by_id(org.id).website == "https://helper.example.com"

    membership = create_user_organization(user, org, "member")
    assert get_user_organization(user, org).role == "member"
    update_user_organization(membership, role="admin")
    assert list_user_organizations(user)[0].organization.username == "helper-org"
    assert list_organization_members(org)[0].role == "admin"

    repo = create_repository(
        repo_type="model",
        namespace="helper-org",
        name="helper-repo",
        full_id="helper-org/helper-repo",
        private=True,
        owner=org,
    )
    assert get_repository("model", "helper-org", "helper-repo").full_id == "helper-org/helper-repo"
    assert get_repository_by_full_id("helper-org/helper-repo", "model").name == "helper-repo"
    listed = list_repositories(repo_type="model", namespace="helper-org", limit=1)
    assert [item.full_id for item in listed] == ["helper-org/helper-repo"]

    update_repository(repo, private=False)
    assert get_repository("model", "helper-org", "helper-repo").private is False

    session = create_session("helper-session", user, "secret", datetime.now(timezone.utc) + timedelta(hours=1))
    assert session.user.username == "helper-user"
    delete_session("helper-session")
    assert get_user_by_id(user.id).sessions.count() == 0

    token = create_token(user, "token-hash", "cli")
    assert list_user_tokens(user)[0].name == "cli"
    update_token_last_used(token, datetime.now(timezone.utc))
    assert token.last_used is not None
    delete_token(token.id)
    assert list_user_tokens(user) == []

    delete_user_organization(membership)
    assert list_user_organizations(user) == []
    delete_user(user)
    delete_organization = get_user_by_username("helper-org")
    delete_organization.delete_instance()


def test_file_commit_ssh_lfs_and_quota_helpers_cover_remaining_crud():
    owner = get_user_by_username("owner")
    repo = create_repository(
        repo_type="dataset",
        namespace="owner",
        name="helper-dataset",
        full_id="owner/helper-dataset",
        private=False,
        owner=owner,
    )

    file_record = create_file(
        repository=repo,
        path_in_repo="weights.bin",
        size=123,
        sha256="sha256-1",
        lfs=True,
        owner=owner,
    )
    assert get_file(repo, "weights.bin").sha256 == "sha256-1"
    assert get_file_by_sha256("sha256-1").path_in_repo == "weights.bin"
    update_file(file_record, size=456)
    assert get_file(repo, "weights.bin").size == 456

    commit = create_commit(
        commit_id="commit-helper",
        repository=repo,
        repo_type="dataset",
        branch="main",
        author=owner,
        username=owner.username,
        message="Add file",
        description="desc",
    )
    assert get_commit("commit-helper", repo).message == "Add file"
    assert [item.commit_id for item in list_commits_by_repo(repo, branch="main", limit=1)] == [
        "commit-helper"
    ]

    ssh_key = create_ssh_key(
        user=owner,
        key_type="ssh-ed25519",
        public_key="ssh-ed25519 AAAATEST owner@example.com",
        fingerprint="helper-fingerprint",
        title="Helper Key",
    )
    assert get_ssh_key_by_id(ssh_key.id).title == "Helper Key"
    assert get_ssh_key_by_fingerprint("helper-fingerprint").id == ssh_key.id
    update_ssh_key(ssh_key, title="Updated Helper Key")
    assert list_user_ssh_keys(owner)[0].title == "Updated Helper Key"
    delete_ssh_key(ssh_key)
    assert get_ssh_key_by_fingerprint("helper-fingerprint") is None

    lfs_entry = create_lfs_history(
        repository=repo,
        path_in_repo="weights.bin",
        sha256="lfs-sha-1",
        size=789,
        commit_id="commit-helper",
        file=file_record,
    )
    create_lfs_history(
        repository=repo,
        path_in_repo="weights.bin",
        sha256="lfs-sha-2",
        size=790,
        commit_id="commit-helper-2",
        file=file_record,
    )
    assert [item.sha256 for item in list_lfs_history(repo, "weights.bin", limit=1)] == ["lfs-sha-2"]
    assert get_lfs_history_entry(repo, "weights.bin", "lfs-sha-1").id == lfs_entry.id
    refresh_lfs_history_timestamp(lfs_entry, "commit-refresh")
    assert get_lfs_history_entry(repo, "weights.bin", "lfs-sha-1").commit_id == "commit-refresh"

    repo.lfs_threshold_bytes = None
    repo.lfs_keep_versions = None
    repo.lfs_suffix_rules = '[".gguf", ".bin"]'
    repo.save()
    assert get_effective_lfs_threshold(repo) == cfg.app.lfs_threshold_bytes
    assert get_effective_lfs_keep_versions(repo) == cfg.app.lfs_keep_versions
    assert ".gguf" in get_effective_lfs_suffix_rules(repo)
    repo.lfs_suffix_rules = "{bad-json"
    repo.save()
    assert get_effective_lfs_suffix_rules(repo) == cfg.app.lfs_suffix_rules_default

    owner.private_used_bytes = 20
    owner.public_used_bytes = 10
    owner.private_quota_bytes = 100
    owner.public_quota_bytes = 50
    owner.save()
    assert increment_storage("owner", 5, is_private=True) == (25, 10)
    storage_info = get_storage_info("owner")
    assert storage_info["private_percentage_used"] == 25.0
    repo.quota_bytes = None
    repo.used_bytes = 12
    repo.private = False
    repo.save()
    assert get_repo_storage_info(repo)["is_inheriting"] is True
    set_quota("owner", private_quota_bytes=120, public_quota_bytes=60)
    assert get_storage_info("owner")["public_quota_bytes"] == 60
    set_repo_quota(repo, 30)
    assert get_repo_storage_info(repo)["quota_bytes"] == 30
    with pytest.raises(ValueError):
        set_repo_quota(repo, 10**12)

    delete_file(file_record)
    assert get_file(repo, "weights.bin") is None


def test_email_staging_invitation_and_stats_helpers_cover_remaining_paths():
    owner = get_user_by_username("owner")
    org = get_user_by_username("acme-labs")
    repo = get_repository("dataset", "acme-labs", "private-dataset")

    verification = create_email_verification(
        owner,
        "verify-helper",
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert get_email_verification("verify-helper").id == verification.id
    delete_email_verification(verification)
    assert get_email_verification("verify-helper") is None

    staging = create_staging_upload(
        repository=repo,
        repo_type="dataset",
        revision="main",
        path_in_repo="data/train.jsonl",
        sha256="stage-sha",
        size=321,
        storage_key="stage/key",
        lfs=False,
        upload_id="upload-1",
        uploader=owner,
    )
    assert staging.upload_id == "upload-1"
    delete_staging_upload(staging)

    invite = create_invitation(
        token="org-invite-helper",
        action="join_org",
        parameters=f'{{"org_id": {org.id}, "email": "user@example.com"}}',
        created_by=owner,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        max_usage=None,
    )
    broken_invite = create_invitation(
        token="org-invite-broken",
        action="join_org",
        parameters="{bad-json",
        created_by=owner,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        max_usage=None,
    )
    expired_invite = create_invitation(
        token="org-invite-expired",
        action="join_org",
        parameters=f'{{"org_id": {org.id}}}',
        created_by=owner,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        max_usage=None,
    )
    assert get_invitation("org-invite-helper").id == invite.id
    assert get_invitation_by_id(invite.id).token == "org-invite-helper"
    assert {item.token for item in list_org_invitations(org)} == {
        "org-invite-helper",
        "org-invite-expired",
    }
    assert delete_expired_invitations() >= 1
    assert [item.token for item in list_org_invitations(org)] == ["org-invite-helper"]

    liker = get_user_by_username("member")
    create_repository_like(repo, owner)
    create_repository_like(repo, liker)
    assert get_repository_like(repo, owner) is not None
    assert [user.username for user in list_repository_likers(repo, limit=2)] == [
        liker.username,
        owner.username,
    ]

    session = create_download_session(
        repository=repo,
        session_id="helper-session-2",
        time_bucket=456,
        first_file="data/eval.jsonl",
        user=None,
    )
    assert get_download_session(repo, "helper-session-2", 456).id == session.id

    today = datetime.now(timezone.utc).date()
    DailyRepoStats.create(repository=repo, date=today - timedelta(days=1), download_sessions=1, file_downloads=2)
    DailyRepoStats.create(repository=repo, date=today, download_sessions=3, file_downloads=4)
    assert len(list_daily_stats(repo, today - timedelta(days=1), today)) == 2
    assert get_latest_daily_stat(repo).date == today

    confirmation = create_confirmation_token(
        action_type="delete_s3_prefix",
        action_data={"prefix": "owner/demo"},
        ttl_seconds=-1,
    )
    assert get_confirmation_token(confirmation.token) is None
    assert cleanup_expired_confirmation_tokens() >= 1
