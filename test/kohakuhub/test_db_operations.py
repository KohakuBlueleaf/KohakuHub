"""Tests for database helper operations."""

from datetime import datetime, timedelta, timezone

import pytest

from kohakuhub.db import RepositoryLike
from kohakuhub.db_operations import (
    check_invitation_available,
    consume_confirmation_token,
    count_repository_sessions,
    create_confirmation_token,
    create_download_session,
    create_invitation,
    create_repository_like,
    delete_repository_like,
    get_daily_stat,
    get_merged_external_tokens,
    get_repository,
    get_user_by_username,
    increment_download_session_files,
    mark_invitation_used,
    set_user_external_token,
    should_use_lfs,
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
