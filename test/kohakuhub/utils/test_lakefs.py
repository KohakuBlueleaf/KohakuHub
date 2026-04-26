"""Tests for LakeFS utility helpers."""

import pytest

from kohakuhub.utils.lakefs import _sanitize_repo_id, lakefs_repo_name, resolve_revision
from test.kohakuhub.support.fakes import FakeLakeFSClient, FakeS3Service


def test_sanitize_repo_id_replaces_invalid_characters():
    assert _sanitize_repo_id("Org/My_Repo.v2!") == "org-my-repo-v2"


def test_lakefs_repo_name_is_deterministic_and_length_bound():
    repo_name = lakefs_repo_name("model", "owner/demo-model")

    assert repo_name == lakefs_repo_name("model", "owner/demo-model")
    assert len(repo_name) <= 63
    assert repo_name.startswith("m-")


@pytest.mark.asyncio
async def test_resolve_revision_prefers_branch_then_commit():
    s3 = FakeS3Service()
    lakefs = FakeLakeFSClient(s3_service=s3, default_bucket="test-bucket")
    await lakefs.create_repository(
        name="m-owner-demo",
        storage_namespace="s3://test-bucket/m-owner-demo",
        default_branch="main",
    )
    branch = await lakefs.get_branch("m-owner-demo", "main")

    commit_id, commit_info = await resolve_revision(lakefs, "m-owner-demo", "main")
    assert commit_id == branch["commit_id"]
    assert commit_info["id"] == branch["commit_id"]

    commit_id_from_sha, commit_info_from_sha = await resolve_revision(
        lakefs, "m-owner-demo", branch["commit_id"]
    )
    assert commit_id_from_sha == branch["commit_id"]
    assert commit_info_from_sha["id"] == branch["commit_id"]


@pytest.mark.asyncio
async def test_resolve_revision_raises_for_missing_revision():
    s3 = FakeS3Service()
    lakefs = FakeLakeFSClient(s3_service=s3, default_bucket="test-bucket")
    await lakefs.create_repository(
        name="m-owner-demo",
        storage_namespace="s3://test-bucket/m-owner-demo",
        default_branch="main",
    )

    with pytest.raises(ValueError):
        await resolve_revision(lakefs, "m-owner-demo", "missing-ref")
