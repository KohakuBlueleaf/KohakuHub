"""Compatibility tests using the real huggingface_hub Python client."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from huggingface_hub import (
    CommitOperationAdd,
    CommitOperationDelete,
    HfApi,
    hf_hub_download,
    snapshot_download,
)


async def _create_hf_token(client, name: str) -> str:
    response = await client.post(
        "/api/auth/tokens/create",
        json={"name": name},
    )
    response.raise_for_status()
    return response.json()["token"]


def _set_repo_private(api: HfApi, repo_id: str, private: bool) -> None:
    update_settings = getattr(api, "update_repo_settings", None)
    if callable(update_settings):
        update_settings(repo_id, private=private)
        return

    update_visibility = getattr(api, "update_repo_visibility", None)
    if callable(update_visibility):
        update_visibility(repo_id, private=private)
        return

    pytest.skip("huggingface_hub does not expose repository visibility updates")


@pytest.fixture
async def member_hf_api_token(member_client):
    return await _create_hf_token(member_client, "hf-api-member")


@pytest.fixture
async def outsider_hf_api_token(outsider_client):
    return await _create_hf_token(outsider_client, "hf-api-outsider")


async def test_hf_api_repo_info_permissions_and_existence(
    live_server_url,
    hf_api_token,
    member_hf_api_token,
    outsider_hf_api_token,
):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    member_api = HfApi(endpoint=live_server_url, token=member_hf_api_token)
    outsider_api = HfApi(endpoint=live_server_url, token=outsider_hf_api_token)

    whoami = await asyncio.to_thread(api.whoami)
    assert whoami["name"] == "owner"
    assert any(org["name"] == "acme-labs" for org in whoami["orgs"])

    info = await asyncio.to_thread(
        lambda: api.repo_info("owner/demo-model", files_metadata=True)
    )
    assert info.id == "owner/demo-model"
    assert any(sibling.rfilename == "README.md" for sibling in info.siblings)
    assert any(
        sibling.rfilename == "weights/model.safetensors" for sibling in info.siblings
    )

    assert await asyncio.to_thread(lambda: api.repo_exists("owner/demo-model")) is True
    assert (
        await asyncio.to_thread(
            lambda: api.file_exists("owner/demo-model", "README.md")
        )
        is True
    )
    assert (
        await asyncio.to_thread(
            lambda: api.file_exists("owner/demo-model", "missing.txt")
        )
        is False
    )

    private_dataset = await asyncio.to_thread(
        lambda: member_api.dataset_info(
            "acme-labs/private-dataset",
            files_metadata=True,
        )
    )
    assert private_dataset.private is True
    assert any(
        sibling.rfilename == "data/train.jsonl"
        for sibling in private_dataset.siblings
    )

    assert (
        await asyncio.to_thread(
            lambda: member_api.repo_exists(
                "acme-labs/private-dataset",
                repo_type="dataset",
            )
        )
        is True
    )
    assert (
        await asyncio.to_thread(
            lambda: member_api.file_exists(
                "acme-labs/private-dataset",
                "data/train.jsonl",
                repo_type="dataset",
            )
        )
        is True
    )
    assert (
        await asyncio.to_thread(
            lambda: outsider_api.repo_exists(
                "acme-labs/private-dataset",
                repo_type="dataset",
            )
        )
        is False
    )
    assert (
        await asyncio.to_thread(
            lambda: outsider_api.file_exists(
                "acme-labs/private-dataset",
                "data/train.jsonl",
                repo_type="dataset",
            )
        )
        is False
    )


async def test_hf_api_listings_tree_and_downloads(
    live_server_url,
    hf_api_token,
    member_hf_api_token,
    tmp_path,
):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    member_api = HfApi(endpoint=live_server_url, token=member_hf_api_token)

    await asyncio.to_thread(
        lambda: api.create_repo(
            "owner/hf-space-listing",
            repo_type="space",
            space_sdk="static",
        )
    )

    models = await asyncio.to_thread(lambda: list(api.list_models(author="owner", limit=10)))
    datasets = await asyncio.to_thread(
        lambda: list(member_api.list_datasets(author="acme-labs", limit=10))
    )
    spaces = await asyncio.to_thread(lambda: list(api.list_spaces(author="owner", limit=10)))
    assert any(item.id == "owner/demo-model" for item in models)
    assert any(item.id == "acme-labs/private-dataset" for item in datasets)
    assert any(item.id == "owner/hf-space-listing" for item in spaces)

    space_info = await asyncio.to_thread(
        lambda: api.space_info("owner/hf-space-listing")
    )
    assert space_info.id == "owner/hf-space-listing"

    refs = await asyncio.to_thread(lambda: api.list_repo_refs("owner/demo-model"))
    assert any(branch.name == "main" for branch in refs.branches)

    tree_entries = await asyncio.to_thread(
        lambda: list(api.list_repo_tree("owner/demo-model", recursive=True))
    )
    paths = {entry.path for entry in tree_entries}
    assert "README.md" in paths
    assert "weights/model.safetensors" in paths

    downloaded = await asyncio.to_thread(
        lambda: hf_hub_download(
            repo_id="owner/demo-model",
            filename="README.md",
            endpoint=live_server_url,
            token=hf_api_token,
            cache_dir=tmp_path,
        )
    )
    assert Path(downloaded).read_text(encoding="utf-8") == "# Demo Model\n\nseed data\n"

    snapshot_dir = await asyncio.to_thread(
        lambda: snapshot_download(
            repo_id="owner/demo-model",
            endpoint=live_server_url,
            token=hf_api_token,
            cache_dir=tmp_path / "cache",
            local_dir=tmp_path / "snapshot",
        )
    )
    assert (Path(snapshot_dir) / "README.md").read_text(encoding="utf-8") == (
        "# Demo Model\n\nseed data\n"
    )
    assert (Path(snapshot_dir) / "weights" / "model.safetensors").read_bytes() == (
        b"safe tensor payload"
    )

async def test_hf_api_create_repo_upload_file_and_upload_folder(
    live_server_url,
    hf_api_token,
    tmp_path,
):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)

    repo_url = await asyncio.to_thread(
        lambda: api.create_repo("owner/hf-api-created", exist_ok=False)
    )
    assert str(repo_url).endswith("/models/owner/hf-api-created")

    await asyncio.to_thread(
        lambda: api.upload_file(
            path_or_fileobj=b"hello from huggingface_hub\n",
            path_in_repo="nested/hf-api.txt",
            repo_id="owner/hf-api-created",
            commit_message="Upload through huggingface_hub",
        )
    )

    upload_folder = tmp_path / "upload-folder"
    (upload_folder / "docs").mkdir(parents=True)
    (upload_folder / "README.md").write_text("# Folder Upload\n", encoding="utf-8")
    (upload_folder / "docs" / "guide.md").write_text("hello\n", encoding="utf-8")

    await asyncio.to_thread(
        lambda: api.upload_folder(
            repo_id="owner/hf-api-created",
            folder_path=upload_folder,
            path_in_repo="bundle",
            commit_message="Upload folder through huggingface_hub",
        )
    )

    tree_entries = await asyncio.to_thread(
        lambda: list(api.list_repo_tree("owner/hf-api-created", recursive=True))
    )
    tree_paths = {entry.path for entry in tree_entries}
    assert "nested/hf-api.txt" in tree_paths
    assert "bundle/README.md" in tree_paths
    assert "bundle/docs/guide.md" in tree_paths


async def test_hf_api_create_commit_delete_file_and_list_commits(
    live_server_url,
    hf_api_token,
):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)

    await asyncio.to_thread(lambda: api.create_repo("owner/hf-commit-compat"))
    await asyncio.to_thread(
        lambda: api.upload_file(
            path_or_fileobj=b"delete me\n",
            path_in_repo="delete-me.txt",
            repo_id="owner/hf-commit-compat",
            commit_message="Seed delete target",
        )
    )

    commit_info = await asyncio.to_thread(
        lambda: api.create_commit(
            "owner/hf-commit-compat",
            operations=[
                CommitOperationAdd(
                    path_in_repo="README.md",
                    path_or_fileobj=b"# Commit Compat\n",
                ),
                CommitOperationDelete(path_in_repo="delete-me.txt"),
            ],
            commit_message="Replace delete target",
        )
    )
    assert commit_info.oid

    commits = await asyncio.to_thread(
        lambda: api.list_repo_commits("owner/hf-commit-compat")
    )
    assert commits[0].title == "Replace delete target"
    assert commits[0].authors == ["owner"]
    assert any(commit.title == "Seed delete target" for commit in commits)

    assert (
        await asyncio.to_thread(
            lambda: api.file_exists("owner/hf-commit-compat", "README.md")
        )
        is True
    )
    assert (
        await asyncio.to_thread(
            lambda: api.file_exists("owner/hf-commit-compat", "delete-me.txt")
        )
        is False
    )

    await asyncio.to_thread(
        lambda: api.delete_file(
            "README.md",
            "owner/hf-commit-compat",
            commit_message="Remove README via huggingface_hub",
        )
    )
    assert (
        await asyncio.to_thread(
            lambda: api.file_exists("owner/hf-commit-compat", "README.md")
        )
        is False
    )


async def test_hf_api_branch_and_tag_lifecycle(live_server_url, hf_api_token):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)

    await asyncio.to_thread(lambda: api.create_repo("owner/hf-refs-compat"))
    await asyncio.to_thread(
        lambda: api.create_branch("owner/hf-refs-compat", branch="feature-compat")
    )
    await asyncio.to_thread(
        lambda: api.create_tag("owner/hf-refs-compat", tag="v0.1.0")
    )

    refs = await asyncio.to_thread(lambda: api.list_repo_refs("owner/hf-refs-compat"))
    assert any(branch.name == "feature-compat" for branch in refs.branches)
    assert any(tag.name == "v0.1.0" for tag in refs.tags)

    await asyncio.to_thread(lambda: api.delete_tag("owner/hf-refs-compat", tag="v0.1.0"))
    await asyncio.to_thread(
        lambda: api.delete_branch("owner/hf-refs-compat", branch="feature-compat")
    )

    refs_after_delete = await asyncio.to_thread(
        lambda: api.list_repo_refs("owner/hf-refs-compat")
    )
    assert all(tag.name != "v0.1.0" for tag in refs_after_delete.tags)
    assert all(
        branch.name != "feature-compat" for branch in refs_after_delete.branches
    )


async def test_hf_api_likes_visibility_move_delete_and_list_liked_repos(
    live_server_url,
    hf_api_token,
    outsider_hf_api_token,
):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    outsider_api = HfApi(endpoint=live_server_url, token=outsider_hf_api_token)

    await asyncio.to_thread(lambda: api.create_repo("owner/hf-lifecycle-compat"))

    likers = await asyncio.to_thread(
        lambda: list(api.list_repo_likers("owner/demo-model"))
    )
    assert [user.username for user in likers] == ["owner"]

    liked_repos = await asyncio.to_thread(lambda: api.list_liked_repos("owner"))
    assert "owner/demo-model" in liked_repos.models

    await asyncio.to_thread(lambda: api.unlike("owner/demo-model"))
    liked_after_unlike = await asyncio.to_thread(lambda: api.list_liked_repos("owner"))
    assert "owner/demo-model" not in liked_after_unlike.models

    like_method = getattr(api, "like", None)
    if callable(like_method):
        await asyncio.to_thread(lambda: like_method("owner/demo-model"))
        reliked = await asyncio.to_thread(lambda: api.list_liked_repos("owner"))
        assert "owner/demo-model" in reliked.models

    if callable(getattr(api, "update_repo_settings", None)) or callable(
        getattr(api, "update_repo_visibility", None)
    ):
        await asyncio.to_thread(
            lambda: _set_repo_private(api, "owner/hf-lifecycle-compat", True)
        )
        private_info = await asyncio.to_thread(
            lambda: api.repo_info("owner/hf-lifecycle-compat")
        )
        assert private_info.private is True
        assert (
            await asyncio.to_thread(
                lambda: outsider_api.repo_exists("owner/hf-lifecycle-compat")
            )
            is False
        )

    await asyncio.to_thread(
        lambda: api.move_repo(
            "owner/hf-lifecycle-compat",
            "owner/hf-lifecycle-renamed",
        )
    )
    assert (
        await asyncio.to_thread(
            lambda: api.repo_exists("owner/hf-lifecycle-compat")
        )
        is False
    )
    assert (
        await asyncio.to_thread(
            lambda: api.repo_exists("owner/hf-lifecycle-renamed")
        )
        is True
    )

    await asyncio.to_thread(lambda: api.delete_repo("owner/hf-lifecycle-renamed"))
    assert (
        await asyncio.to_thread(
            lambda: api.repo_exists("owner/hf-lifecycle-renamed")
        )
        is False
    )
