"""Repository tree listing and path information endpoints."""

import asyncio
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import JSONResponse

from kohakuhub.config import cfg
from kohakuhub.auth.dependencies import get_optional_user
from kohakuhub.auth.permissions import check_repo_read_permission
from kohakuhub.constants import DATETIME_FORMAT_ISO
from kohakuhub.db import File, Repository, User
from kohakuhub.db_operations import get_repository, should_use_lfs
from kohakuhub.lakefs_rest_client import get_lakefs_rest_client
from kohakuhub.logger import get_logger
from kohakuhub.utils.lakefs import (
    get_lakefs_client,
    lakefs_repo_name,
    resolve_revision,
)
from kohakuhub.api.fallback import with_repo_fallback
from kohakuhub.api.repo.utils.hf import (
    hf_bad_request,
    hf_entry_not_found,
    hf_repo_not_found,
    hf_revision_not_found,
    hf_server_error,
    is_lakefs_not_found_error,
    is_lakefs_revision_error,
)

logger = get_logger("REPO")
router = APIRouter()

RepoType = Literal["model", "dataset", "space"]

TREE_PAGE_SIZE = 1000
TREE_EXPAND_PAGE_SIZE = 50
PATHS_INFO_MAX_PATHS = 1000
PATHS_INFO_CONCURRENCY = 16
# Concurrency cap for the per-target ``logCommits`` calls in
# ``resolve_last_commits_for_paths``. Each call is independent so we can
# fan them out under a shared async client; 16 mirrors the existing
# PATHS_INFO_CONCURRENCY budget and stays well under common LakeFS
# connection-pool limits.
LAST_COMMIT_LOOKUP_CONCURRENCY = 16
NAME_PREFIX_MAX_LENGTH = 256


def _normalize_repo_path(path: str) -> str:
    """Normalize a repository-relative path."""
    return path.lstrip("/").rstrip("/")


def _normalize_name_prefix(value: str | None) -> str | None:
    """Normalize the optional same-level name-prefix filter.

    Returns the trimmed prefix, or ``None`` when the caller did not
    supply one. Empty strings (or whitespace-only) are treated as
    omitted so the response stays byte-identical to the unfiltered
    listing — that matters for the HF-compat invariant in §5.1.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _format_last_modified(mtime: float | int | None) -> str | None:
    """Format LakeFS mtime for HF-compatible responses."""
    if not mtime:
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(DATETIME_FORMAT_ISO)


def _format_commit_date(value) -> str | None:
    """Normalize commit dates to the HF datetime wire format."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime(
            DATETIME_FORMAT_ISO
        )
    return value


def _serialize_last_commit(commit: dict) -> dict:
    """Convert a LakeFS commit payload to the HF wire format."""
    return {
        "id": commit["id"],
        "title": commit.get("message", ""),
        "date": _format_commit_date(commit.get("creation_date")),
    }


def _build_lfs_payload(checksum: str, size_bytes: int) -> dict:
    """Build the HF LFS metadata payload."""
    return {
        "oid": checksum,
        "size": size_bytes,
        "pointerSize": 134,
    }


def _build_public_link(
    request: Request,
    *,
    limit: int,
    cursor: str,
) -> str:
    """Build a public-facing pagination link using the configured base URL."""
    query_params = dict(request.query_params)
    query_params["limit"] = str(limit)
    query_params["cursor"] = cursor
    return f"{cfg.app.base_url.rstrip('/')}{request.url.path}?{urlencode(query_params)}"


def _build_file_record_map(
    repository: Repository, paths: list[str]
) -> dict[str, File]:
    """Fetch file records in one query for the provided paths."""
    if not paths:
        return {}

    query = File.select().where(
        (File.repository == repository)
        & (File.path_in_repo.in_(paths))
        & (File.is_deleted == False)
    )
    return {file.path_in_repo: file for file in query}


async def fetch_lakefs_objects_page(
    lakefs_repo: str,
    revision: str,
    prefix: str,
    recursive: bool,
    amount: int,
    after: str | None = None,
) -> dict:
    """Fetch one page of objects from LakeFS."""
    client = get_lakefs_client()
    return await client.list_objects(
        repository=lakefs_repo,
        ref=revision,
        prefix=prefix,
        delimiter="" if recursive else "/",
        amount=amount,
        after=after or "",
    )


async def _calculate_directory_stats(
    lakefs_repo: str,
    revision: str,
    directory_path: str,
    first_page: dict | None = None,
) -> tuple[int, float | None]:
    """Calculate recursive directory size and latest file mtime."""
    client = get_lakefs_client()
    total_size = 0
    latest_mtime = None
    prefix = f"{directory_path}/"

    def consume_page(page: dict) -> None:
        nonlocal total_size, latest_mtime

        for child_obj in page.get("results", []):
            if child_obj.get("path_type") != "object":
                continue

            total_size += child_obj.get("size_bytes") or 0

            child_mtime = child_obj.get("mtime")
            if child_mtime and (latest_mtime is None or child_mtime > latest_mtime):
                latest_mtime = child_mtime

    page = first_page
    if page is None:
        page = await client.list_objects(
            repository=lakefs_repo,
            ref=revision,
            prefix=prefix,
            amount=1000,
            delimiter="",
        )

    consume_page(page)

    pagination = page.get("pagination", {})
    has_more = pagination.get("has_more", False)
    after = pagination.get("next_offset", "")

    while has_more:
        page = await client.list_objects(
            repository=lakefs_repo,
            ref=revision,
            prefix=prefix,
            amount=1000,
            after=after,
            delimiter="",
        )
        consume_page(page)

        pagination = page.get("pagination", {})
        has_more = pagination.get("has_more", False)
        after = pagination.get("next_offset", "")

    return total_size, latest_mtime


def _make_tree_item(
    obj: dict,
    repository: Repository,
    file_records: dict[str, File],
    expand: bool,
    last_commit: dict | None = None,
) -> dict:
    """Convert a LakeFS object/common_prefix to the HF tree wire format."""
    path = _normalize_repo_path(obj["path"])
    item_type = obj["path_type"]

    if item_type == "object":
        size_bytes = obj.get("size_bytes") or 0
        file_record = file_records.get(path)
        checksum = (
            file_record.sha256 if file_record and file_record.sha256 else obj.get("checksum", "")
        )
        is_lfs = (
            file_record.lfs
            if file_record is not None
            else should_use_lfs(repository, path, size_bytes)
        )

        file_obj = {
            "type": "file",
            "oid": checksum,
            "size": size_bytes,
            "path": path,
        }

        last_modified = _format_last_modified(obj.get("mtime"))
        if last_modified:
            file_obj["lastModified"] = last_modified

        if is_lfs:
            file_obj["lfs"] = _build_lfs_payload(checksum, size_bytes)

        if expand:
            file_obj["lastCommit"] = last_commit
            file_obj["securityFileStatus"] = None

        return file_obj

    dir_obj = {
        "type": "directory",
        "oid": obj.get("checksum", ""),
        "size": 0,
        "path": path,
    }

    last_modified = _format_last_modified(obj.get("mtime"))
    if last_modified:
        dir_obj["lastModified"] = last_modified

    if expand:
        dir_obj["lastCommit"] = last_commit

    return dir_obj


async def resolve_last_commits_for_paths(
    lakefs_repo: str,
    revision: str,
    targets: list[dict[str, str]],
) -> dict[str, dict | None]:
    """Resolve the latest commit touching each target path.

    For every entry in ``targets`` (each ``{path: ..., type: 'file'|'directory'}``)
    issue a ``logCommits`` call with the matching ``objects=[path]`` (file) or
    ``prefixes=[path/]`` (directory) filter and ``amount=1, limit=true`` so
    LakeFS returns at most the most recent qualifying commit.

    Why this is fast: LakeFS implements path-filtered log via its
    content-addressed metarange tree (``checkPathListInCommit`` in
    ``pkg/catalog/catalog.go``) — when a path's containing range hash matches
    between two commits, LakeFS short-circuits without fetching diff bodies.
    Each call is single-digit milliseconds regardless of how deep the path
    sits in the commit log. Earlier revisions of this function reproduced the
    walk client-side via per-commit ``diff_refs`` calls; that pattern was
    O(commits-walked) and dominated ``/tree?expand=true`` latency on
    WAN-deployed instances. See issue #59 for the measured ~60× speedup and
    the LakeFS-source pointer.

    Note: ``logCommits`` skips merge commits by default, matching what the
    previous diff-walk produced (it inspected only single-parent diffs as
    well). If a future caller needs first-parent merge traversal they can
    invoke ``log_commits(..., first_parent=True)`` directly.

    LakeFS version requirement: the ``objects=`` / ``prefixes=`` / ``limit=``
    parameters used here were introduced in LakeFS v0.54.0 (released
    2021-11-08). KohakuHub's docker bundle pins ``treeverse/lakefs:latest``
    so default deployments are always compatible; operators self-deploying
    against older LakeFS servers must upgrade to v0.54.0 or newer.
    """
    if not targets:
        return {}

    client = get_lakefs_rest_client()
    sem = asyncio.Semaphore(LAST_COMMIT_LOOKUP_CONCURRENCY)

    async def fetch_one(target: dict[str, str]) -> tuple[str, dict | None]:
        path = target.get("path")
        if not path:
            return "", None
        kind = target.get("type")
        # ``objects`` for files, ``prefixes`` for directories. The directory
        # filter must end with ``/`` so LakeFS treats it as a strict prefix,
        # otherwise paths sharing a basename leading edge would qualify.
        if kind == "directory":
            kwargs = {"prefixes": [f"{path}/"]}
        else:
            kwargs = {"objects": [path]}
        async with sem:
            try:
                page = await client.log_commits(
                    repository=lakefs_repo,
                    ref=revision,
                    amount=1,
                    limit=True,
                    **kwargs,
                )
            except Exception as error:
                logger.debug(
                    f"log_commits for {kind or 'file'}={path!r} on {lakefs_repo}@{revision}: {error}"
                )
                return path, None

        results = page.get("results") or []
        return path, _serialize_last_commit(results[0]) if results else None

    pairs = await asyncio.gather(*(fetch_one(target) for target in targets))
    return {path: commit for path, commit in pairs if path}


async def _process_single_path(
    lakefs_repo: str,
    revision: str,
    repository: Repository,
    clean_path: str,
    file_records: dict[str, File],
    semaphore: asyncio.Semaphore,
    expand: bool,
) -> dict | None:
    """Resolve one path to either a file or directory entry."""
    client = get_lakefs_client()

    async with semaphore:
        try:
            obj_stats = await client.stat_object(
                repository=lakefs_repo,
                ref=revision,
                path=clean_path,
            )

            file_record = file_records.get(clean_path)
            checksum = (
                file_record.sha256
                if file_record and file_record.sha256
                else obj_stats.get("checksum", "")
            )
            size_bytes = obj_stats.get("size_bytes") or 0
            is_lfs = (
                file_record.lfs
                if file_record is not None
                else should_use_lfs(repository, clean_path, size_bytes)
            )

            file_info = {
                "type": "file",
                "path": clean_path,
                "size": size_bytes,
                "oid": checksum,
            }

            last_modified = _format_last_modified(obj_stats.get("mtime"))
            if last_modified:
                file_info["lastModified"] = last_modified

            if is_lfs:
                file_info["lfs"] = _build_lfs_payload(checksum, size_bytes)

            return file_info
        except Exception as error:
            if not is_lakefs_not_found_error(error):
                logger.debug(f"Failed to stat path {clean_path}: {error}")
                return None

        try:
            list_result = await client.list_objects(
                repository=lakefs_repo,
                ref=revision,
                prefix=f"{clean_path}/",
                amount=1,
            )
        except Exception as error:
            if not is_lakefs_not_found_error(error):
                logger.debug(f"Failed to inspect directory path {clean_path}: {error}")
            return None

        if not list_result.get("results"):
            return None

        first_result = list_result["results"][0]
        dir_info = {
            "type": "directory",
            "path": clean_path,
            "oid": first_result.get("checksum", ""),
            "size": 0,
        }

        last_modified = _format_last_modified(first_result.get("mtime"))

        if expand:
            try:
                dir_size, latest_mtime = await _calculate_directory_stats(
                    lakefs_repo=lakefs_repo,
                    revision=revision,
                    directory_path=clean_path,
                    first_page=list_result,
                )
                dir_info["size"] = dir_size
                last_modified = _format_last_modified(latest_mtime) or last_modified
            except Exception as error:
                if not is_lakefs_not_found_error(error):
                    logger.debug(
                        f"Failed to calculate directory stats for {clean_path}: {error}"
                    )

        if last_modified:
            dir_info["lastModified"] = last_modified

        return dir_info


@router.get("/{repo_type}s/{namespace}/{repo_name}/tree/{revision}{path:path}")
@with_repo_fallback("tree")
async def list_repo_tree(
    repo_type: RepoType,
    namespace: str,
    repo_name: str,
    request: Request,
    revision: str = "main",
    path: str = "",
    recursive: bool = False,
    expand: bool = False,
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = None,
    name_prefix: str | None = None,
    fallback: bool = True,
    user: User | None = Depends(get_optional_user),
):
    """List repository file tree."""
    repo_id = f"{namespace}/{repo_name}"
    repo_row = get_repository(repo_type, namespace, repo_name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    check_repo_read_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    clean_path = _normalize_repo_path(path)
    base_prefix = f"{clean_path}/" if clean_path else ""

    # Same-level basename-prefix filter pushed straight to LakeFS via its
    # `prefix` arg; `delimiter='/'` is preserved when not recursive so
    # directory rows still surface as `common_prefix` entries (see #54).
    normalized_prefix = _normalize_name_prefix(name_prefix)
    if normalized_prefix is not None:
        if "/" in normalized_prefix:
            return hf_bad_request("name_prefix must not contain '/'")
        if len(normalized_prefix) > NAME_PREFIX_MAX_LENGTH:
            return hf_bad_request(
                f"name_prefix is too long (max {NAME_PREFIX_MAX_LENGTH} chars)"
            )
        lakefs_prefix = f"{base_prefix}{normalized_prefix}"
    else:
        lakefs_prefix = base_prefix

    default_limit = TREE_EXPAND_PAGE_SIZE if expand else TREE_PAGE_SIZE
    page_size = min(limit or default_limit, default_limit)

    try:
        resolved_revision, _ = await resolve_revision(
            get_lakefs_client(), lakefs_repo, revision
        )
    except Exception:
        return hf_revision_not_found(repo_id, revision)

    try:
        page = await fetch_lakefs_objects_page(
            lakefs_repo=lakefs_repo,
            revision=resolved_revision,
            prefix=lakefs_prefix,
            recursive=recursive,
            amount=page_size,
            after=cursor,
        )
    except Exception as error:
        if is_lakefs_not_found_error(error):
            if is_lakefs_revision_error(error):
                return hf_revision_not_found(repo_id, revision)
            return hf_entry_not_found(repo_id, clean_path or "/", revision)

        logger.exception(f"Failed to list objects for {repo_id}", error)
        return hf_server_error(f"Failed to list objects: {str(error)}")

    page_results = page.get("results", [])
    # "Directory exists but the prefix matched nothing" must stay 200 +
    # empty list — only treat empty results as 404 when the caller did
    # not narrow with name_prefix and is not paging into a known dir.
    if (
        clean_path
        and not page_results
        and normalized_prefix is None
        and not cursor
    ):
        return hf_entry_not_found(repo_id, clean_path, revision)

    file_paths = [
        _normalize_repo_path(obj["path"])
        for obj in page_results
        if obj.get("path_type") == "object"
    ]
    file_records = _build_file_record_map(repo_row, file_paths)

    last_commit_map: dict[str, dict | None] = {}
    if expand and page_results:
        targets = [
            {
                "path": _normalize_repo_path(obj["path"]),
                "type": "file"
                if obj.get("path_type") == "object"
                else "directory",
            }
            for obj in page_results
        ]
        try:
            last_commit_map = await resolve_last_commits_for_paths(
                lakefs_repo=lakefs_repo,
                revision=resolved_revision,
                targets=targets,
            )
        except Exception as error:
            if is_lakefs_not_found_error(error) and is_lakefs_revision_error(error):
                return hf_revision_not_found(repo_id, revision)
            logger.warning(
                f"Failed to expand last commit data for {repo_id}/{revision}: {error}"
            )

    result_list = [
        _make_tree_item(
            obj=obj,
            repository=repo_row,
            file_records=file_records,
            expand=expand,
            last_commit=last_commit_map.get(_normalize_repo_path(obj["path"])),
        )
        for obj in page_results
    ]

    headers = {}
    pagination = page.get("pagination") or {}
    if pagination.get("has_more") and pagination.get("next_offset"):
        next_url = _build_public_link(
            request=request,
            limit=page_size,
            cursor=pagination["next_offset"],
        )
        headers["Link"] = f'<{next_url}>; rel="next"'

    return JSONResponse(content=result_list, headers=headers)


@router.post("/{repo_type}s/{namespace}/{repo_name}/paths-info/{revision}")
@with_repo_fallback("paths_info")
async def get_paths_info(
    repo_type: str,
    namespace: str,
    repo_name: str,
    revision: str,
    request: Request,
    paths: list[str] = Form(...),
    expand: bool = Form(False),
    fallback: bool = True,
    user: User | None = Depends(get_optional_user),
):
    """Get information about specific paths in a repository."""
    repo_id = f"{namespace}/{repo_name}"
    repo_row = get_repository(repo_type, namespace, repo_name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    check_repo_read_permission(repo_row, user)

    normalized_paths = [_normalize_repo_path(path) for path in paths if path]
    if len(normalized_paths) > PATHS_INFO_MAX_PATHS:
        return hf_bad_request(
            f"Too many paths requested. Maximum supported paths per request is {PATHS_INFO_MAX_PATHS}."
        )

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    try:
        resolved_revision, _ = await resolve_revision(
            get_lakefs_client(), lakefs_repo, revision
        )
    except Exception:
        return hf_revision_not_found(repo_id, revision)

    file_records = _build_file_record_map(repo_row, normalized_paths)
    semaphore = asyncio.Semaphore(PATHS_INFO_CONCURRENCY)

    try:
        results = await asyncio.gather(
            *[
                _process_single_path(
                    lakefs_repo=lakefs_repo,
                    revision=resolved_revision,
                    repository=repo_row,
                    clean_path=clean_path,
                    file_records=file_records,
                    semaphore=semaphore,
                    expand=expand,
                )
                for clean_path in normalized_paths
            ]
        )
    except Exception as error:
        if is_lakefs_not_found_error(error):
            if is_lakefs_revision_error(error):
                return hf_revision_not_found(repo_id, revision)
            return []
        logger.exception(f"Failed to fetch paths info for {repo_id}", error)
        return hf_server_error(f"Failed to fetch paths info: {str(error)}")

    existing_entries = [entry for entry in results if entry is not None]

    if expand and existing_entries:
        targets = [
            {"path": entry["path"], "type": entry["type"]} for entry in existing_entries
        ]
        try:
            last_commit_map = await resolve_last_commits_for_paths(
                lakefs_repo=lakefs_repo,
                revision=resolved_revision,
                targets=targets,
            )
        except Exception as error:
            if is_lakefs_not_found_error(error) and is_lakefs_revision_error(error):
                return hf_revision_not_found(repo_id, revision)
            logger.warning(
                f"Failed to expand paths-info commit data for {repo_id}/{revision}: {error}"
            )
            last_commit_map = {}

        for entry in existing_entries:
            entry["lastCommit"] = last_commit_map.get(entry["path"])
            if entry["type"] == "file":
                entry["securityFileStatus"] = None

    return existing_entries
