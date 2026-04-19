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
TREE_DIFF_PAGE_SIZE = 1000
TREE_COMMIT_SCAN_PAGE_SIZE = 100
PATHS_INFO_MAX_PATHS = 1000
PATHS_INFO_CONCURRENCY = 16


def _normalize_repo_path(path: str) -> str:
    """Normalize a repository-relative path."""
    return path.lstrip("/").rstrip("/")


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


def _apply_changed_path(
    changed_path: str,
    unresolved_files: set[str],
    unresolved_directories: set[str],
    resolved: dict[str, dict | None],
    commit_info: dict,
) -> None:
    """Resolve file and ancestor directory targets touched by a diff path."""
    normalized_path = _normalize_repo_path(changed_path)
    if not normalized_path:
        return

    if normalized_path in unresolved_files:
        unresolved_files.remove(normalized_path)
        resolved[normalized_path] = commit_info

    if normalized_path in unresolved_directories:
        unresolved_directories.remove(normalized_path)
        resolved[normalized_path] = commit_info

    ancestor = normalized_path
    while "/" in ancestor and unresolved_directories:
        ancestor = ancestor.rsplit("/", 1)[0]
        if ancestor in unresolved_directories:
            unresolved_directories.remove(ancestor)
            resolved[ancestor] = commit_info


async def resolve_last_commits_for_paths(
    lakefs_repo: str,
    revision: str,
    targets: list[dict[str, str]],
) -> dict[str, dict | None]:
    """Resolve the latest commit touching each target path."""
    unresolved_files = {
        target["path"] for target in targets if target["type"] == "file" and target["path"]
    }
    unresolved_directories = {
        target["path"]
        for target in targets
        if target["type"] == "directory" and target["path"]
    }
    if not unresolved_files and not unresolved_directories:
        return {}

    client = get_lakefs_rest_client()
    resolved: dict[str, dict | None] = {}
    commit_cursor: str | None = None

    while unresolved_files or unresolved_directories:
        log_result = await client.log_commits(
            repository=lakefs_repo,
            ref=revision,
            after=commit_cursor,
            amount=TREE_COMMIT_SCAN_PAGE_SIZE,
        )
        commits = log_result.get("results", [])
        if not commits:
            break

        for commit in commits:
            commit_info = _serialize_last_commit(commit)
            parent_ids = commit.get("parents") or []
            parent_id = parent_ids[0] if parent_ids else None

            if not parent_id:
                for path in unresolved_files:
                    resolved[path] = commit_info
                for path in unresolved_directories:
                    resolved[path] = commit_info
                unresolved_files.clear()
                unresolved_directories.clear()
                break

            diff_cursor: str | None = None
            while unresolved_files or unresolved_directories:
                diff_result = await client.diff_refs(
                    repository=lakefs_repo,
                    left_ref=parent_id,
                    right_ref=commit["id"],
                    after=diff_cursor,
                    amount=TREE_DIFF_PAGE_SIZE,
                )

                for entry in diff_result.get("results", []):
                    diff_path = entry.get("path")
                    if diff_path:
                        _apply_changed_path(
                            diff_path,
                            unresolved_files,
                            unresolved_directories,
                            resolved,
                            commit_info,
                        )
                    if not unresolved_files and not unresolved_directories:
                        break

                pagination = diff_result.get("pagination") or {}
                if (
                    not pagination.get("has_more")
                    or (not unresolved_files and not unresolved_directories)
                ):
                    break
                diff_cursor = pagination.get("next_offset")

            if not unresolved_files and not unresolved_directories:
                break

        pagination = log_result.get("pagination") or {}
        if not pagination.get("has_more") or (
            not unresolved_files and not unresolved_directories
        ):
            break
        commit_cursor = pagination.get("next_offset")

    return resolved


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
    prefix = f"{clean_path}/" if clean_path else ""
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
            prefix=prefix,
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
    if clean_path and not page_results:
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
