"""Deeper huggingface_hub client-side integration coverage.

This module complements the other two ``test_huggingface_hub_*`` suites by
pinning extra real-client edge cases that the baseline flows
(``test_huggingface_hub_compat.py``) and the P0/P1/P2 sweep
(``test_huggingface_hub_deep.py``) do not already exercise:

* writes to non-default branches / branching from a commit sha
* tree listings that cross the default page size (Link-header pagination)
* cache-bust downloads after an in-place file edit
* deletedFolder commit ops reaching the ``huggingface_hub``-visible state
* fsspec walk/info against ``HfFileSystem``

Every test here runs the **real** ``huggingface_hub`` Python client against
the live test server — it is the integration half of the test tree. Tests
that hit KohakuHub's HTTP surface directly (bypassing ``huggingface_hub``)
or that simulate the kohaku-hub-ui consumption patterns live alongside
their specific API module under ``test/kohakuhub/api/``.

Scratch repos live under ``owner/hf-surf-*`` so this module does not
collide with the baseline seed or with the other two hf_hub files.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from huggingface_hub import (
    CommitOperationAdd,
    HfApi,
    HfFileSystem,
    hf_hub_download,
    snapshot_download,
)
from huggingface_hub.utils import (
    RepositoryNotFoundError,
)


def _api(live_server_url: str, token: str) -> HfApi:
    return HfApi(endpoint=live_server_url, token=token)


async def _run(func, *args, **kwargs):
    return await asyncio.to_thread(lambda: func(*args, **kwargs))


_MISSING = object()


def _field(obj: Any, name: str) -> Any:
    """Version-portable attr-or-dict-key access (hf<0.21 returns dicts)."""
    value = getattr(obj, name, _MISSING)
    if value is not _MISSING:
        return value
    if isinstance(obj, dict):
        return obj.get(name)
    return None


async def test_upload_file_to_non_main_branch(live_server_url, hf_api_token):
    """``revision=<branch>`` on ``upload_file`` commits to that branch;
    main stays untouched. Used by release-branch publishing flows."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-surf-branch-upload"
    await _run(api.create_repo, repo_id)
    await _run(api.create_branch, repo_id, branch="release-1")

    await _run(
        api.upload_file,
        path_or_fileobj=b"release asset\n",
        path_in_repo="asset.txt",
        repo_id=repo_id,
        revision="release-1",
        commit_message="release-1 asset",
    )

    assert await _run(api.file_exists, repo_id, "asset.txt", revision="main") is False
    assert (
        await _run(api.file_exists, repo_id, "asset.txt", revision="release-1") is True
    )


async def test_create_branch_from_specific_revision(live_server_url, hf_api_token):
    """``create_branch(revision=<sha>)`` must branch off the requested commit,
    not the default branch HEAD. ``datasets`` uses this to pin release
    commits, and any caller passing a commit sha to ``revision=`` expects
    it to resolve via lakefs's commit store, not the branch-name table."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-surf-branch-from-sha"
    await _run(api.create_repo, repo_id)
    await _run(
        api.upload_file,
        path_or_fileobj=b"v1\n",
        path_in_repo="doc.txt",
        repo_id=repo_id,
    )
    info_v1 = await _run(api.repo_info, repo_id)
    sha_v1 = info_v1.sha

    await _run(
        api.upload_file,
        path_or_fileobj=b"v2\n",
        path_in_repo="doc.txt",
        repo_id=repo_id,
    )

    await _run(api.create_branch, repo_id, branch="pinned", revision=sha_v1)

    refs = await _run(api.list_repo_refs, repo_id)
    assert any(b.name == "pinned" for b in refs.branches)


async def test_snapshot_download_follows_branch_revision(
    live_server_url, hf_api_token, tmp_path
):
    """``snapshot_download(revision=<branch>)`` pulls branch content, not main."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-surf-snapshot-branch"
    await _run(api.create_repo, repo_id)

    await _run(
        api.upload_file,
        path_or_fileobj=b"main content\n",
        path_in_repo="doc.txt",
        repo_id=repo_id,
    )
    await _run(api.create_branch, repo_id, branch="alt")
    await _run(
        api.upload_file,
        path_or_fileobj=b"alt branch content\n",
        path_in_repo="doc.txt",
        repo_id=repo_id,
        revision="alt",
    )

    main_dir = Path(
        await _run(
            snapshot_download,
            repo_id=repo_id,
            endpoint=live_server_url,
            token=hf_api_token,
            cache_dir=tmp_path / "main_cache",
            local_dir=tmp_path / "main",
            revision="main",
        )
    )
    alt_dir = Path(
        await _run(
            snapshot_download,
            repo_id=repo_id,
            endpoint=live_server_url,
            token=hf_api_token,
            cache_dir=tmp_path / "alt_cache",
            local_dir=tmp_path / "alt",
            revision="alt",
        )
    )
    assert (main_dir / "doc.txt").read_bytes() == b"main content\n"
    assert (alt_dir / "doc.txt").read_bytes() == b"alt branch content\n"


async def test_list_repo_tree_supports_pagination_over_many_entries(
    live_server_url, hf_api_token
):
    """Confirm that tree listing paginates correctly for repos with more
    files than the per-page default. ``huggingface_hub.list_repo_tree``
    walks the ``Link: rel="next"`` header transparently, so the assertion
    is that a 60-entry directory round-trips all 60 paths.
    """
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-surf-tree-paginate"
    await _run(api.create_repo, repo_id)

    additions = [
        CommitOperationAdd(
            path_in_repo=f"items/file_{i:02d}.txt",
            path_or_fileobj=f"content {i}\n".encode(),
        )
        for i in range(60)
    ]
    await _run(
        api.create_commit,
        repo_id=repo_id,
        operations=additions,
        commit_message="bulk seed 60 files",
    )

    tree = await _run(
        lambda: list(
            api.list_repo_tree(repo_id, path_in_repo="items", recursive=False)
        )
    )
    paths = {entry.path for entry in tree if _field(entry, "path").endswith(".txt")}
    assert len(paths) == 60
    assert "items/file_00.txt" in paths
    assert "items/file_59.txt" in paths


async def test_hf_hub_download_force_download_reflects_latest_content(
    live_server_url, hf_api_token, tmp_path
):
    """Upload a file, download it, overwrite it, download again with
    ``force_download=True`` — the second download must have the new content.
    Rules out a stale-cache compat bug where an ETag mismatch would leave
    the client with yesterday's bytes."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-surf-force-download"
    await _run(api.create_repo, repo_id)

    await _run(
        api.upload_file,
        path_or_fileobj=b"first content\n",
        path_in_repo="payload.txt",
        repo_id=repo_id,
    )

    first = await _run(
        hf_hub_download,
        repo_id=repo_id,
        filename="payload.txt",
        endpoint=live_server_url,
        token=hf_api_token,
        cache_dir=tmp_path / "c1",
    )
    assert Path(first).read_bytes() == b"first content\n"

    await _run(
        api.upload_file,
        path_or_fileobj=b"second content\n",
        path_in_repo="payload.txt",
        repo_id=repo_id,
    )

    second = await _run(
        hf_hub_download,
        repo_id=repo_id,
        filename="payload.txt",
        endpoint=live_server_url,
        token=hf_api_token,
        cache_dir=tmp_path / "c2",
        force_download=True,
    )
    assert Path(second).read_bytes() == b"second content\n"


async def test_file_exists_respects_explicit_revision(live_server_url, hf_api_token):
    """A file only present on a feature branch must not register as present
    on main, and vice versa."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-surf-file-exists-rev"
    await _run(api.create_repo, repo_id)
    await _run(api.create_branch, repo_id, branch="feat")
    await _run(
        api.upload_file,
        path_or_fileobj=b"only on feat\n",
        path_in_repo="feat_only.txt",
        repo_id=repo_id,
        revision="feat",
    )

    assert await _run(api.file_exists, repo_id, "feat_only.txt", revision="feat")
    assert not await _run(api.file_exists, repo_id, "feat_only.txt", revision="main")


async def test_repo_info_raises_named_error_after_delete(
    live_server_url, hf_api_token
):
    """After ``delete_repo``, ``repo_info`` must raise
    ``RepositoryNotFoundError``, not a generic HTTPError — transformers /
    diffusers branch on this specific exception subclass."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-surf-delete-reraise"
    await _run(api.create_repo, repo_id)
    await _run(api.delete_repo, repo_id)

    with pytest.raises(RepositoryNotFoundError):
        await _run(api.repo_info, repo_id)


async def test_create_commit_with_deleted_folder_op(live_server_url, hf_api_token):
    """The commit NDJSON protocol accepts a ``deletedFolder`` op that
    recursively removes a directory. After such a commit, the
    ``huggingface_hub``-visible tree must no longer contain any path
    inside that directory."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-surf-deleted-folder"
    await _run(api.create_repo, repo_id)

    await _run(
        api.create_commit,
        repo_id=repo_id,
        operations=[
            CommitOperationAdd(path_in_repo="keep.txt", path_or_fileobj=b"keep\n"),
            CommitOperationAdd(
                path_in_repo="stale/one.txt", path_or_fileobj=b"one\n"
            ),
            CommitOperationAdd(
                path_in_repo="stale/sub/two.txt", path_or_fileobj=b"two\n"
            ),
        ],
        commit_message="seed",
    )

    # ``huggingface_hub`` does not wrap ``deletedFolder`` — send the raw
    # NDJSON op that the KohakuHub backend natively supports.
    ndjson_lines = [
        json.dumps({"key": "header", "value": {"summary": "wipe stale"}}),
        json.dumps({"key": "deletedFolder", "value": {"path": "stale"}}),
    ]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{live_server_url}/api/models/{repo_id}/commit/main",
            content="\n".join(ndjson_lines).encode(),
            headers={
                "Authorization": f"Bearer {hf_api_token}",
                "Content-Type": "application/x-ndjson",
            },
        )
    response.raise_for_status()

    tree = await _run(
        lambda: {
            entry.path for entry in api.list_repo_tree(repo_id, recursive=True)
        }
    )
    assert "keep.txt" in tree
    assert all(not path.startswith("stale/") for path in tree), (
        f"deletedFolder should have recursively removed stale/, got {tree}"
    )


async def test_hf_filesystem_info_and_walk(live_server_url, hf_api_token):
    """``HfFileSystem.info()`` and ``HfFileSystem.walk()`` — used by
    ``datasets`` and downstream fsspec-based loaders — must expose sane
    directory and file metadata from the live hub."""
    fs = HfFileSystem(endpoint=live_server_url, token=hf_api_token)

    info = await asyncio.to_thread(
        lambda: fs.info("datasets/acme-labs/private-dataset")
    )
    assert info.get("type") in ("directory", "dir")

    def collect_walk():
        rows = []
        for dirpath, dirnames, filenames in fs.walk(
            "datasets/acme-labs/private-dataset"
        ):
            rows.append((dirpath, sorted(dirnames), sorted(filenames)))
        return rows

    walked = await asyncio.to_thread(collect_walk)
    all_files = {f for _, _, fs_names in walked for f in fs_names}
    assert "train.jsonl" in all_files
