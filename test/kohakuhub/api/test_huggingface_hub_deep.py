"""Deep huggingface_hub compatibility coverage (P0 / P1 / P2).

Complements ``test_huggingface_hub_compat.py``. Each test exercises a specific
compatibility surface flagged in the PR #18 survey of what the 2025 releases of
``transformers`` / ``diffusers`` / ``datasets`` / ``timm`` / ``sentence-transformers``
/ ``peft`` / ``trl`` / ``accelerate`` / ``gradio`` / ``evaluate`` actually call.

Conventions used by this module:

* scratch repos live under ``owner/hf-deep-*`` — a suffix keeps tests isolated.
* `asyncio.to_thread` wraps every blocking ``huggingface_hub`` call so the
  async test runner does not block.
* assertions are pinned to the shape the downstream library depends on, not
  to KohakuHub's internal field names.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from huggingface_hub import (
    CommitOperationAdd,
    CommitOperationDelete,
    HfApi,
    HfFileSystem,
    hf_hub_download,
    hf_hub_url,
    snapshot_download,
)

# `huggingface_hub.errors` landed around v0.22; v0.20.3 (still in the CI matrix)
# keeps these exceptions under `huggingface_hub.utils`. The utils path is the
# version-portable import that works against every client version we target.
from huggingface_hub.utils import (
    EntryNotFoundError,
    HfHubHTTPError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)

# `CommitOperationCopy` was added in v0.21.0 — older matrix pins do not ship it.
try:
    from huggingface_hub import CommitOperationCopy  # type: ignore[attr-defined]
except ImportError:  # v0.20.3 and earlier
    CommitOperationCopy = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _api(live_server_url: str, token: str) -> HfApi:
    """Build an HfApi client pointing at the live test server."""
    return HfApi(endpoint=live_server_url, token=token)


async def _run(func, *args, **kwargs):
    """Execute a blocking HfApi method on a worker thread."""
    return await asyncio.to_thread(lambda: func(*args, **kwargs))


async def _create_hf_token(client, name: str) -> str:
    response = await client.post("/api/auth/tokens/create", json={"name": name})
    response.raise_for_status()
    return response.json()["token"]


@pytest.fixture
async def outsider_hf_api_token(outsider_client):
    return await _create_hf_token(outsider_client, "hf-deep-outsider")


@pytest.fixture
async def member_hf_api_token(member_client):
    return await _create_hf_token(member_client, "hf-deep-member")


# ---------------------------------------------------------------------------
# P0 / P1 read path
# ---------------------------------------------------------------------------


async def test_whoami_returns_gradio_shape(live_server_url, hf_api_token):
    """gradio/oauth, gradio/cli/commands/deploy_space.py:281 read
    ``whoami["auth"]["accessToken"]["role"]`` — assert that payload shape
    is stable so those CLIs do not KeyError.
    """
    api = _api(live_server_url, hf_api_token)
    whoami = await _run(api.whoami)

    assert whoami["type"] == "user"
    assert whoami["name"] == "owner"
    assert isinstance(whoami.get("orgs"), list)
    assert any(org["name"] == "acme-labs" for org in whoami["orgs"])
    # gradio deploy reads this:
    auth_block = whoami["auth"]["accessToken"]
    assert auth_block["role"] in {"write", "read", "admin"}


async def test_repo_info_exposes_sha_and_lfs_sibling_metadata(
    live_server_url, hf_api_token
):
    """transformers / diffusers / datasets all use ``repo_info(files_metadata=True)``
    for revision pinning and shard verification. Siblings must carry
    ``size`` + ``lfs={sha256,size}`` for LFS entries.
    """
    api = _api(live_server_url, hf_api_token)
    info = await _run(api.repo_info, "owner/demo-model", files_metadata=True)

    assert info.id == "owner/demo-model"
    assert info.sha, "repo_info must pin a revision sha"

    siblings_by_path = {s.rfilename: s for s in info.siblings}
    readme_sibling = siblings_by_path["README.md"]
    weights_sibling = siblings_by_path["weights/model.safetensors"]

    # Regular file still carries size metadata.
    assert readme_sibling.size is not None and readme_sibling.size > 0

    # LFS sibling exposes sha256 + size.
    lfs_block = weights_sibling.lfs
    assert lfs_block is not None
    # huggingface_hub maps this as a BlobLfsInfo dataclass-ish object.
    assert lfs_block.sha256
    assert lfs_block.size == len(b"safe tensor payload")


async def test_repo_info_respects_explicit_revision(live_server_url, hf_api_token):
    """``repo_info(revision=...)`` must resolve branch / tag / commit sha."""
    api = _api(live_server_url, hf_api_token)
    main_info = await _run(api.repo_info, "owner/demo-model", revision="main")

    # Commit sha should round-trip.
    by_sha = await _run(
        api.repo_info, "owner/demo-model", revision=main_info.sha
    )
    assert by_sha.sha == main_info.sha


async def test_model_dataset_space_info_method_shortcuts(
    live_server_url, hf_api_token, member_hf_api_token
):
    """Each library tends to call ``HfApi.model_info`` / ``dataset_info`` /
    ``space_info`` specifically — not just generic ``repo_info``. The
    backend should resolve these without 404 as long as the repo type matches.
    """
    owner_api = _api(live_server_url, hf_api_token)
    member_api = _api(live_server_url, member_hf_api_token)

    model = await _run(owner_api.model_info, "owner/demo-model")
    assert model.id == "owner/demo-model"

    dataset = await _run(
        member_api.dataset_info, "acme-labs/private-dataset"
    )
    assert dataset.id == "acme-labs/private-dataset"
    assert dataset.private is True

    space_repo = "owner/hf-deep-space-info"
    await _run(owner_api.create_repo, space_repo, repo_type="space", space_sdk="static")
    space = await _run(owner_api.space_info, space_repo)
    assert space.id == space_repo


async def test_list_repo_tree_supports_path_in_repo_non_recursive(
    live_server_url, hf_api_token
):
    """``transformers/utils/hub.py:134`` and
    ``tokenization_utils_base.py:3423`` call
    ``list_repo_tree(path_in_repo="<dir>/", recursive=False)`` to discover
    chat-template files. KohakuHub must honor both a subpath prefix and
    ``recursive=False``.
    """
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-deep-tree-subpath"
    await _run(api.create_repo, repo_id)

    # Create two nested files under a directory.
    await _run(
        api.upload_file,
        path_or_fileobj=b"root\n",
        path_in_repo="root.txt",
        repo_id=repo_id,
    )
    await _run(
        api.upload_file,
        path_or_fileobj=b"alpha\n",
        path_in_repo="templates/alpha.txt",
        repo_id=repo_id,
    )
    await _run(
        api.upload_file,
        path_or_fileobj=b"nested\n",
        path_in_repo="templates/inner/beta.txt",
        repo_id=repo_id,
    )

    # Non-recursive listing of a subpath should only return immediate children.
    entries = await _run(
        lambda: list(
            api.list_repo_tree(
                repo_id,
                path_in_repo="templates",
                recursive=False,
            )
        )
    )
    paths = {entry.path for entry in entries}
    assert "templates/alpha.txt" in paths
    assert "templates/inner" in paths
    assert "templates/inner/beta.txt" not in paths
    # Root-level files must not leak into the subpath listing.
    assert "root.txt" not in paths


async def test_list_repo_files_returns_flat_filename_list(
    live_server_url, hf_api_token
):
    """``transformers/tokenization_utils_base.py:1682``,
    ``sentence-transformers/backend/utils.py:95``, and
    ``datasets/hub.py:94`` all call ``list_repo_files`` — a thin wrapper
    around the tree endpoint that must return a flat list of file paths.
    """
    api = _api(live_server_url, hf_api_token)
    files = await _run(api.list_repo_files, "owner/demo-model")

    assert "README.md" in files
    assert "weights/model.safetensors" in files
    # No directory entries in the flat list.
    assert all(not f.endswith("/") for f in files)


async def test_hf_hub_download_follows_explicit_revision(
    live_server_url, hf_api_token, tmp_path
):
    """``hf_hub_download(revision=<branch>)`` must resolve to the branch's
    content, not the repo's default branch."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-deep-download-rev"
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
        path_or_fileobj=b"alt content\n",
        path_in_repo="doc.txt",
        repo_id=repo_id,
        revision="alt",
    )

    main_path = await _run(
        hf_hub_download,
        repo_id=repo_id,
        filename="doc.txt",
        revision="main",
        endpoint=live_server_url,
        token=hf_api_token,
        cache_dir=tmp_path / "main",
    )
    alt_path = await _run(
        hf_hub_download,
        repo_id=repo_id,
        filename="doc.txt",
        revision="alt",
        endpoint=live_server_url,
        token=hf_api_token,
        cache_dir=tmp_path / "alt",
    )
    assert Path(main_path).read_bytes() == b"main content\n"
    assert Path(alt_path).read_bytes() == b"alt content\n"


async def test_hf_hub_download_returns_lfs_object_bytes(
    live_server_url, hf_api_token, tmp_path
):
    """LFS download must yield the same bytes that were seeded via the
    NDJSON commit + S3 blob. transformers / diffusers both rely on this
    for safetensors-based weights."""
    downloaded = await _run(
        hf_hub_download,
        repo_id="owner/demo-model",
        filename="weights/model.safetensors",
        endpoint=live_server_url,
        token=hf_api_token,
        cache_dir=tmp_path,
    )
    assert Path(downloaded).read_bytes() == b"safe tensor payload"


async def test_snapshot_download_allow_and_ignore_patterns(
    live_server_url, hf_api_token, tmp_path
):
    """Both ``allow_patterns`` and ``ignore_patterns`` are heavily used by
    transformers (single-file checkpoints), sentence-transformers (backend
    exports), and diffusers pipelines."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-deep-snapshot-filters"
    await _run(api.create_repo, repo_id)

    for path, body in (
        ("keep.safetensors", b"keep\n"),
        ("skip.bin", b"skip\n"),
        ("docs/README.md", b"# Docs\n"),
    ):
        await _run(
            api.upload_file,
            path_or_fileobj=body,
            path_in_repo=path,
            repo_id=repo_id,
        )

    allow_dir = await _run(
        snapshot_download,
        repo_id=repo_id,
        endpoint=live_server_url,
        token=hf_api_token,
        cache_dir=tmp_path / "allow_cache",
        local_dir=tmp_path / "allow",
        allow_patterns=["*.safetensors"],
    )
    allow_root = Path(allow_dir)
    assert (allow_root / "keep.safetensors").exists()
    assert not (allow_root / "skip.bin").exists()
    assert not (allow_root / "docs" / "README.md").exists()

    ignore_dir = await _run(
        snapshot_download,
        repo_id=repo_id,
        endpoint=live_server_url,
        token=hf_api_token,
        cache_dir=tmp_path / "ignore_cache",
        local_dir=tmp_path / "ignore",
        ignore_patterns=["*.bin"],
    )
    ignore_root = Path(ignore_dir)
    assert (ignore_root / "keep.safetensors").exists()
    assert (ignore_root / "docs" / "README.md").exists()
    assert not (ignore_root / "skip.bin").exists()


async def test_raw_head_on_resolve_url_matches_hf_semantics(
    live_server_url, hf_api_token, outsider_hf_api_token
):
    """``transformers/utils/hub.py:588-593`` does a raw
    ``get_session().head(hf_hub_url(...), follow_redirects=False)`` in
    place of ``HfApi.file_exists``. The resolve route must answer HEAD
    with proper HF status codes and X-Error-Code headers.
    """

    def head(url: str, token: str | None):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        with httpx.Client(follow_redirects=False, timeout=10) as client:
            return client.head(url, headers=headers)

    public_url = hf_hub_url(
        repo_id="owner/demo-model",
        filename="README.md",
        endpoint=live_server_url,
    )
    r = await asyncio.to_thread(head, public_url, hf_api_token)
    # Existing file should either 200 or redirect to storage — never 404/401.
    assert r.status_code in (200, 302, 307)

    missing_url = hf_hub_url(
        repo_id="owner/demo-model",
        filename="does-not-exist.txt",
        endpoint=live_server_url,
    )
    r_missing = await asyncio.to_thread(head, missing_url, hf_api_token)
    assert r_missing.status_code == 404
    assert r_missing.headers.get("x-error-code") == "EntryNotFound"

    private_url = hf_hub_url(
        repo_id="acme-labs/private-dataset",
        filename="data/train.jsonl",
        repo_type="dataset",
        endpoint=live_server_url,
    )
    r_hidden = await asyncio.to_thread(head, private_url, outsider_hf_api_token)
    # Hidden-private semantics: outsider must see 404, not 401/403.
    assert r_hidden.status_code == 404


async def test_hf_filesystem_supports_ls_and_glob_and_open(
    live_server_url, hf_api_token, tmp_path
):
    """``datasets`` (and optionally ``peft`` XLora) uses ``HfFileSystem``
    for streaming / glob / resolve. The three fsspec primitives
    ``ls`` / ``glob`` / ``open`` must work end-to-end.
    """
    fs = HfFileSystem(endpoint=live_server_url, token=hf_api_token)

    def fs_ls(path):
        return fs.ls(path, detail=False)

    entries = await asyncio.to_thread(fs_ls, "datasets/acme-labs/private-dataset")
    # Should enumerate the top-level entry (the `data/` directory in seed).
    assert any("data" in e for e in entries)

    def fs_glob(pattern):
        return fs.glob(pattern)

    globbed = await asyncio.to_thread(
        fs_glob, "datasets/acme-labs/private-dataset/data/*.jsonl"
    )
    assert any(path.endswith("train.jsonl") for path in globbed)

    def fs_read():
        with fs.open(
            "datasets/acme-labs/private-dataset/data/train.jsonl", "rb"
        ) as handle:
            return handle.read()

    data = await asyncio.to_thread(fs_read)
    assert data == b'{"text":"hello"}\n'


# ---------------------------------------------------------------------------
# P0 write path
# ---------------------------------------------------------------------------


async def test_create_repo_exist_ok_true_returns_without_error(
    live_server_url, hf_api_token
):
    """``create_repo(exist_ok=True)`` on an already-existing repo must not
    raise — every ecosystem push_to_hub path passes this flag. Requires
    the backend to answer with 409 (not 400) so huggingface_hub's client
    shortcut applies.
    """
    api = _api(live_server_url, hf_api_token)
    # owner/demo-model is created by the baseline seed — hit the exist_ok path.
    result = await _run(
        api.create_repo, "owner/demo-model", exist_ok=True
    )
    # When exist_ok suppresses the error, huggingface_hub still returns a
    # RepoUrl (string-like).
    assert result is not None


async def test_create_repo_exist_ok_false_raises_hf_hub_http_error(
    live_server_url, hf_api_token
):
    """Without exist_ok, the same call must raise — but it must raise a
    recognizable ``HfHubHTTPError`` subclass, not an opaque 500."""
    api = _api(live_server_url, hf_api_token)
    with pytest.raises(HfHubHTTPError) as exc:
        await _run(api.create_repo, "owner/demo-model", exist_ok=False)
    # Status must be 409 (HF client-side behavior depends on this).
    assert exc.value.response.status_code == 409


async def test_create_branch_exist_ok_true_does_not_raise(
    live_server_url, hf_api_token
):
    """``sentence-transformers/base/model.py:865`` and
    ``datasets/arrow_dataset.py:6176`` call ``create_branch(exist_ok=True)``.
    """
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-deep-branch-exist-ok"
    await _run(api.create_repo, repo_id)

    await _run(api.create_branch, repo_id, branch="release")
    # Second call must succeed silently with exist_ok=True.
    await _run(api.create_branch, repo_id, branch="release", exist_ok=True)


async def test_upload_folder_allow_and_ignore_patterns(
    live_server_url, hf_api_token, tmp_path
):
    """Pattern filters on upload_folder match what transformers /
    diffusers / sentence-transformers use for shard-limited pushes."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-deep-upload-patterns"
    await _run(api.create_repo, repo_id)

    folder = tmp_path / "bundle"
    (folder / "artifacts").mkdir(parents=True)
    (folder / "artifacts" / "keep.safetensors").write_bytes(b"K\n")
    (folder / "artifacts" / "skip.bin").write_bytes(b"S\n")
    (folder / "README.md").write_text("# Bundle\n", encoding="utf-8")

    await _run(
        api.upload_folder,
        repo_id=repo_id,
        folder_path=folder,
        allow_patterns=["artifacts/*.safetensors", "README.md"],
        commit_message="allow patterns only",
    )
    tree = {
        entry.path
        for entry in await _run(
            lambda: list(api.list_repo_tree(repo_id, recursive=True))
        )
    }
    assert "artifacts/keep.safetensors" in tree
    assert "README.md" in tree
    assert "artifacts/skip.bin" not in tree

    # Flip to ignore pattern; re-adding the previously-skipped file.
    (folder / "artifacts" / "skip.bin").write_bytes(b"actually skip\n")
    await _run(
        api.upload_folder,
        repo_id=repo_id,
        folder_path=folder,
        ignore_patterns=["artifacts/*.bin"],
        commit_message="ignore *.bin",
    )
    tree_after = {
        entry.path
        for entry in await _run(
            lambda: list(api.list_repo_tree(repo_id, recursive=True))
        )
    }
    assert "artifacts/keep.safetensors" in tree_after
    assert "artifacts/skip.bin" not in tree_after


async def test_datasets_style_preupload_then_create_commit(
    live_server_url, hf_api_token
):
    """``datasets.Dataset.push_to_hub`` does explicit ``preupload_lfs_files``
    then splits ``create_commit`` into per-shard chunks. Confirm both
    halves of the protocol work.
    """
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-deep-datasets-pattern"
    await _run(api.create_repo, repo_id, repo_type="dataset")

    shard_a = CommitOperationAdd(
        path_in_repo="data/train-00000-of-00002.parquet",
        path_or_fileobj=b"PAR1" + b"\x00" * 16 + b"PAR1",
    )
    shard_b = CommitOperationAdd(
        path_in_repo="data/train-00001-of-00002.parquet",
        path_or_fileobj=b"PAR1" + b"\xff" * 16 + b"PAR1",
    )

    # preupload step
    await _run(
        api.preupload_lfs_files,
        repo_id=repo_id,
        additions=[shard_a, shard_b],
        repo_type="dataset",
    )

    # split commit into two calls (per datasets' incremental flow).
    first = await _run(
        api.create_commit,
        repo_id=repo_id,
        repo_type="dataset",
        operations=[shard_a],
        commit_message="shard 0",
    )
    second = await _run(
        api.create_commit,
        repo_id=repo_id,
        repo_type="dataset",
        operations=[shard_b],
        commit_message="shard 1",
    )
    assert first.oid
    assert second.oid and second.oid != first.oid

    tree = {
        entry.path
        for entry in await _run(
            lambda: list(
                api.list_repo_tree(
                    repo_id, repo_type="dataset", recursive=True
                )
            )
        )
    }
    assert "data/train-00000-of-00002.parquet" in tree
    assert "data/train-00001-of-00002.parquet" in tree


async def test_create_commit_supports_copy_operation(
    live_server_url, hf_api_token
):
    """``CommitOperationCopy`` has zero call-sites in the 10 libs surveyed,
    but the backend does accept a ``copyFile`` operation (see
    ``api/commit/routers/operations.py:813``). Make sure the server-side
    code path is reachable via the huggingface_hub client shape.
    """
    if CommitOperationCopy is None:
        pytest.skip(
            "CommitOperationCopy was added in huggingface_hub 0.21; the "
            "installed client is older."
        )
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-deep-copy-op"
    await _run(api.create_repo, repo_id)

    # Seed the source file.
    await _run(
        api.upload_file,
        path_or_fileobj=b"copy me\n",
        path_in_repo="source.txt",
        repo_id=repo_id,
    )
    src_info = await _run(api.repo_info, repo_id)

    try:
        await _run(
            api.create_commit,
            repo_id=repo_id,
            operations=[
                CommitOperationCopy(
                    src_path_in_repo="source.txt",
                    path_in_repo="copies/duplicate.txt",
                    src_revision=src_info.sha,
                )
            ],
            commit_message="copy operation",
        )
    except HfHubHTTPError as exc:
        # If the server explicitly rejects, it must be a clean 4xx — not 500.
        assert 400 <= exc.response.status_code < 500
        return

    tree = {
        entry.path
        for entry in await _run(
            lambda: list(api.list_repo_tree(repo_id, recursive=True))
        )
    }
    assert "source.txt" in tree
    assert "copies/duplicate.txt" in tree


# ---------------------------------------------------------------------------
# P1 / P2 edge cases
# ---------------------------------------------------------------------------


async def test_hidden_private_repo_is_invisible_to_outsider(
    live_server_url, hf_api_token, outsider_hf_api_token
):
    """``repo_exists`` and ``file_exists`` must both return ``False`` for
    an unauthenticated outsider against a private repo — matching HF's
    hidden-private semantics (no existence leak via 401 vs 404)."""
    outsider_api = _api(live_server_url, outsider_hf_api_token)

    assert await _run(
        outsider_api.repo_exists,
        "acme-labs/private-dataset",
        repo_type="dataset",
    ) is False
    assert await _run(
        outsider_api.file_exists,
        "acme-labs/private-dataset",
        "data/train.jsonl",
        repo_type="dataset",
    ) is False

    # Direct repo_info must raise RepositoryNotFoundError (404), not a 401/403.
    with pytest.raises(RepositoryNotFoundError):
        await _run(
            outsider_api.dataset_info,
            "acme-labs/private-dataset",
        )


async def test_get_paths_info_returns_size_and_lfs_metadata(
    live_server_url, hf_api_token
):
    """``HfApi.get_paths_info`` is the compact-metadata sibling to
    ``list_repo_tree`` used by huggingface_hub internally for batch file
    lookups. The payload must include size (and lfs info for LFS files).
    """
    api = _api(live_server_url, hf_api_token)

    entries = await _run(
        api.get_paths_info,
        repo_id="owner/demo-model",
        paths=["README.md", "weights/model.safetensors"],
    )
    by_path = {e.path: e for e in entries}
    assert "README.md" in by_path
    readme = by_path["README.md"]
    assert readme.size is not None and readme.size > 0

    weights = by_path["weights/model.safetensors"]
    lfs = getattr(weights, "lfs", None)
    assert lfs is not None, "LFS object must carry LFS metadata in paths-info"
    assert lfs.size == len(b"safe tensor payload")


async def test_file_exists_for_missing_file_returns_false_not_exception(
    live_server_url, hf_api_token
):
    """``transformers/safetensors_conversion.py:105`` and
    ``timm/models/_hub.py:420`` both expect a plain bool return for a
    missing filename — not an exception."""
    api = _api(live_server_url, hf_api_token)
    exists = await _run(
        api.file_exists,
        "owner/demo-model",
        "definitely-not-here.txt",
    )
    assert exists is False


async def test_repo_info_revision_not_found_raises_named_error(
    live_server_url, hf_api_token
):
    """transformers / diffusers catch ``RevisionNotFoundError`` specifically
    to provide a better error message. The backend must surface
    ``X-Error-Code: RevisionNotFound`` so huggingface_hub maps it."""
    api = _api(live_server_url, hf_api_token)
    with pytest.raises(RevisionNotFoundError):
        await _run(api.repo_info, "owner/demo-model", revision="no-such-revision")


async def test_entry_not_found_raises_named_error_on_download(
    live_server_url, hf_api_token, tmp_path
):
    """``hf_hub_download`` of a missing file must raise ``EntryNotFoundError``
    — libraries use this to fall back to alternate filenames
    (timm ``_hub.py:225-246`` safetensors → .bin fallback)."""
    with pytest.raises(EntryNotFoundError):
        await _run(
            hf_hub_download,
            repo_id="owner/demo-model",
            filename="missing-asset.bin",
            endpoint=live_server_url,
            token=hf_api_token,
            cache_dir=tmp_path,
        )


async def test_update_repo_settings_visibility_field_is_honored(
    live_server_url, hf_api_token
):
    """``huggingface_hub>=1.x`` sends ``visibility="private"`` via
    ``update_repo_settings`` instead of the legacy ``private=True``.
    The backend must accept both shapes (see PR commit 19c2a5c)."""
    api = _api(live_server_url, hf_api_token)
    repo_id = "owner/hf-deep-visibility"
    await _run(api.create_repo, repo_id)

    # Legacy `private=` path.
    if callable(getattr(api, "update_repo_settings", None)):
        await _run(api.update_repo_settings, repo_id, private=True)
    else:
        pytest.skip("update_repo_settings not available on this hf client")

    info_private = await _run(api.repo_info, repo_id)
    assert info_private.private is True

    # New `visibility=` path (use raw HTTP because not every hf client
    # version exposes it).
    async def _set_visibility(value: str):
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.put(
                f"{live_server_url}/api/models/{repo_id}/settings",
                json={"visibility": value},
                headers={"Authorization": f"Bearer {hf_api_token}"},
            )
            response.raise_for_status()

    await _set_visibility("public")
    info_public = await _run(api.repo_info, repo_id)
    assert info_public.private is False


async def test_whoami_rejects_missing_token(live_server_url):
    """Unauthenticated ``/api/whoami-v2`` must 401 so huggingface_hub raises
    a proper auth error rather than treating the caller as anonymous with
    write privileges."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{live_server_url}/api/whoami-v2")
    assert response.status_code == 401


async def test_create_commit_create_pr_flag_does_not_pollute_main():
    """``create_commit(create_pr=True)`` opens a PR branch on real HF Hub;
    KohakuHub does not yet implement PR branches, so the backend must
    either (a) commit to an isolated ref or (b) reject cleanly. Silent
    commits to ``main`` are a compat-breaking surprise.

    Until PR infrastructure lands, this test is a placeholder expressing
    the expected post-support behavior.
    """
    pytest.skip(
        "create_commit(create_pr=True) not yet supported by KohakuHub — "
        "tracked separately. The current commit endpoint accepts the flag "
        "silently; a follow-up PR must implement refs/pr/<N> isolation."
    )
