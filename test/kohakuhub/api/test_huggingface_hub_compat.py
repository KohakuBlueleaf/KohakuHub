"""Compatibility tests using the real huggingface_hub Python client."""

from __future__ import annotations

import asyncio
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download


async def test_hf_api_whoami_repo_info_and_refs(live_server_url, hf_api_token):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)

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

    refs = await asyncio.to_thread(lambda: api.list_repo_refs("owner/demo-model"))
    assert any(branch.name == "main" for branch in refs.branches)


async def test_hf_api_tree_and_download(live_server_url, hf_api_token, tmp_path):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)

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


async def test_hf_api_create_repo_and_upload_file(live_server_url, hf_api_token):
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

    tree_entries = await asyncio.to_thread(
        lambda: list(api.list_repo_tree("owner/hf-api-created", recursive=True))
    )
    assert any(entry.path == "nested/hf-api.txt" for entry in tree_entries)


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
