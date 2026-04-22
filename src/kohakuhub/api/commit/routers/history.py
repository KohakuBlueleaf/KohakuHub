"""Commit history API endpoints."""

import asyncio
import difflib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from kohakuhub.config import cfg
from kohakuhub.db import User
from kohakuhub.db_operations import get_commit, get_repository, should_use_lfs
from kohakuhub.lakefs_rest_client import get_lakefs_rest_client
from kohakuhub.logger import get_logger
from kohakuhub.auth.dependencies import get_optional_user
from kohakuhub.auth.permissions import check_repo_read_permission
from kohakuhub.utils.lakefs import lakefs_repo_name
from kohakuhub.api.repo.utils.hf import (
    format_hf_datetime,
    hf_repo_not_found,
    hf_server_error,
)

logger = get_logger("COMMITS")
router = APIRouter()


def _to_hf_commit_response(commit: dict, include_formatted: bool) -> dict:
    """Adapt the legacy commit payload to the format expected by `huggingface_hub`."""
    message = commit.get("message") or ""
    title = (commit.get("title") or message).splitlines()[0] if message else ""
    created_at = commit.get("date")

    if isinstance(created_at, (int, float)):
        created_at = format_hf_datetime(
            datetime.fromtimestamp(created_at, tz=timezone.utc)
        )

    payload = {
        "id": commit["id"],
        "title": title,
        "message": message,
        "date": created_at,
        "authors": [{"user": commit.get("author") or "unknown"}],
    }
    if include_formatted:
        payload["formatted"] = {
            "title": title,
            "message": message,
        }
    return payload


@router.get("/{repo_type}s/{namespace}/{name}/commits/{branch}")
async def list_commits(
    repo_type: str,
    namespace: str,
    name: str,
    branch: str = "main",
    request: Request = None,
    limit: int = Query(20, ge=1, le=100),
    after: Optional[str] = None,
    user: User | None = Depends(get_optional_user),
):
    """List commits for a repository branch.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        branch: Branch name (default: main)
        limit: Number of commits to return (max 100)
        after: Pagination cursor (commit ID to start after)
        user: Current authenticated user (optional)

    Returns:
        List of commits with metadata
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check read permission
    check_repo_read_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)

    try:
        # Get commits from LakeFS using REST API
        client = get_lakefs_rest_client()

        logger.debug(
            f"Fetching commits for {lakefs_repo}/{branch}, limit={limit}, after={after}"
        )

        log_result = await client.log_commits(
            repository=lakefs_repo,
            ref=branch,
            amount=limit,
            after=after,
        )

        logger.debug(
            f"Got {len(log_result.get('results', [])) if log_result else 0} commits"
        )

        # Format commits for HuggingFace Hub compatibility
        commits = []

        if not log_result or not log_result.get("results"):
            logger.warning(f"No commits found for {lakefs_repo}/{branch}")
            return JSONResponse(content=[])

        # Get all commit IDs to fetch user info from our database
        commit_ids = [c["id"] for c in log_result["results"]]

        # Fetch our commit records using backref (with actual user info)
        # Use repository FK object to query commits
        our_commits = {
            c.commit_id: c
            for c in repo_row.commits.select().where(
                repo_row.commits.model.commit_id.in_(commit_ids)
            )
        }

        for commit in log_result["results"]:
            try:
                # Try to get user info from our database
                our_commit = our_commits.get(commit["id"])
                # Use author.username via ForeignKey relationship
                author = (
                    our_commit.author.username
                    if our_commit
                    else (commit.get("committer") or "unknown")
                )

                commits.append(
                    {
                        "id": commit["id"],
                        "oid": commit["id"],
                        "title": commit.get("message", ""),
                        "message": commit.get("message", ""),
                        "date": commit.get("creation_date"),
                        "author": author,
                        "email": commit.get("metadata", {}).get("email", ""),
                        "parents": commit.get("parents", []),
                    }
                )
            except Exception as ex:
                logger.warning(
                    f"Failed to parse commit {commit.get('id', 'unknown')}: {str(ex)}"
                )
                continue

        pagination = log_result.get("pagination", {})
        logger.success(f"Returned {len(commits)} commits for {repo_id}/{branch}")
        headers = {}
        next_cursor = pagination.get("next_offset") if pagination.get("has_more") else None
        if pagination.get("has_more") and next_cursor and request is not None:
            next_url = str(request.url.include_query_params(after=next_cursor))
            headers["Link"] = f"<{next_url}>; rel=\"next\""

        include_formatted = (
            request is not None and "formatted" in request.query_params.getlist("expand[]")
        )
        return JSONResponse(
            content=[
                _to_hf_commit_response(commit, include_formatted)
                for commit in commits
            ],
            headers=headers,
        )

    except Exception as e:
        logger.exception(f"Failed to list commits for {repo_id}/{branch}", e)
        return hf_server_error(f"Failed to list commits: {str(e)}")


@router.get("/{repo_type}s/{namespace}/{name}/commit/{commit_id}")
async def get_commit_detail(
    repo_type: str,
    namespace: str,
    name: str,
    commit_id: str,
    user: User | None = Depends(get_optional_user),
):
    """Get detailed commit information including user who made it.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        commit_id: Commit ID (SHA)
        user: Current authenticated user (optional)

    Returns:
        Detailed commit information with user data
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check read permission
    check_repo_read_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)

    try:
        # Get commit from LakeFS
        client = get_lakefs_rest_client()
        lakefs_commit = await client.get_commit(
            repository=lakefs_repo, commit_id=commit_id
        )

        # Get our commit record (with actual user info) using repository FK
        our_commit = get_commit(commit_id, repo_row)

        # Build response
        response = {
            "id": lakefs_commit["id"],
            "oid": lakefs_commit["id"],
            "title": lakefs_commit.get("message", ""),
            "message": lakefs_commit.get("message", ""),
            "date": lakefs_commit.get("creation_date"),
            "parents": lakefs_commit.get("parents", []),
            "metadata": lakefs_commit.get("metadata", {}),
        }

        # Add user info if we have it in our database
        if our_commit:
            response["author"] = our_commit.author.username  # Use FK relationship
            response["user_id"] = our_commit.author.id  # Use FK relationship
            response["description"] = our_commit.description
            response["committed_at"] = our_commit.created_at.isoformat()
        else:
            response["author"] = lakefs_commit.get("committer") or "unknown"

        logger.debug(f"Retrieved commit {commit_id[:8]} for {repo_id}")
        return response

    except Exception as e:
        logger.exception(f"Failed to get commit {commit_id} for {repo_id}", e)
        return hf_server_error(f"Failed to get commit: {str(e)}")


@router.get("/{repo_type}s/{namespace}/{name}/commit/{commit_id}/diff")
async def get_commit_diff(
    repo_type: str,
    namespace: str,
    name: str,
    commit_id: str,
    user: User | None = Depends(get_optional_user),
):
    """Get commit diff showing files changed.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        commit_id: Commit ID (SHA)
        user: Current authenticated user (optional)

    Returns:
        List of file changes with before/after info
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check read permission
    check_repo_read_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)

    try:
        # Get commit from LakeFS
        client = get_lakefs_rest_client()
        lakefs_commit = await client.get_commit(
            repository=lakefs_repo, commit_id=commit_id
        )

        # Get parent commit ID (compare with parent to see what changed)
        parent_id = (
            lakefs_commit.get("parents", [])[0]
            if lakefs_commit.get("parents")
            else None
        )

        if not parent_id:
            # First commit - return empty diff
            logger.info(f"Commit {commit_id[:8]} is the first commit (no parent)")
            return {"files": [], "parent_commit": None}

        # Get diff between parent and this commit using REST API
        diff_result = await client.diff_refs(
            repository=lakefs_repo,
            left_ref=parent_id,
            right_ref=commit_id,
        )

        # Get all file paths from diff to check LFS status from our database
        diff_results = diff_result.get("results", []) if diff_result else []
        file_paths = [d["path"] for d in diff_results]

        # Fetch File records for LFS status using repository FK and backref
        if file_paths:
            file_records = {
                f.path_in_repo: f
                for f in repo_row.files.select().where(
                    repo_row.files.model.path_in_repo.in_(file_paths)
                )
            }
        else:
            file_records = {}

        # Process diff entries concurrently
        async def process_diff_entry(diff_entry):
            """Process a single diff entry (file change) - runs in parallel."""
            path = diff_entry["path"]

            # Check if file is LFS from our database or repo-specific rules
            file_record = file_records.get(path)
            is_lfs = (
                file_record.lfs
                if file_record
                else (
                    diff_entry.get("size_bytes")
                    and should_use_lfs(repo_row, path, diff_entry.get("size_bytes"))
                )
            )

            file_info = {
                "path": path,
                "type": diff_entry.get("type"),  # added, removed, changed
                "path_type": diff_entry.get("path_type"),  # object, common_prefix
                "size_bytes": diff_entry.get("size_bytes"),
                "is_lfs": is_lfs,
            }

            # Get current file size and SHA256
            current_size = None
            current_sha256 = None
            previous_size = None
            previous_sha256 = None

            if diff_entry.get("type") in ["changed", "added"]:
                try:
                    obj_stat = await client.stat_object(
                        repository=lakefs_repo, ref=commit_id, path=path
                    )
                    current_size = obj_stat.get("size_bytes")
                    checksum = obj_stat.get("checksum", "")
                    if checksum and ":" in checksum:
                        current_sha256 = checksum.split(":", 1)[1]
                    else:
                        current_sha256 = checksum
                except Exception as e:
                    logger.debug(f"Failed to get current file stat for {path}: {e}")

            # Get previous file info for changed/removed
            if diff_entry.get("type") in ["changed", "removed"] and parent_id:
                try:
                    parent_obj_stat = await client.stat_object(
                        repository=lakefs_repo, ref=parent_id, path=path
                    )
                    previous_size = parent_obj_stat.get("size_bytes")
                    checksum = parent_obj_stat.get("checksum", "")
                    if checksum and ":" in checksum:
                        previous_sha256 = checksum.split(":", 1)[1]
                    else:
                        previous_sha256 = checksum
                except Exception as e:
                    logger.debug(f"Failed to get previous file stat for {path}: {e}")

            # Add size and SHA256 info
            if current_size is not None:
                file_info["size_bytes"] = current_size
            if current_sha256:
                file_info["sha256"] = current_sha256
            if previous_size is not None:
                file_info["previous_size"] = previous_size
            if previous_sha256:
                file_info["previous_sha256"] = previous_sha256

            # For non-LFS files, fetch actual diff (let frontend decide if renderable)
            # Skip diff for very large files (>1MB) to avoid memory issues
            max_diff_size = 1000 * 1000  # 1MB

            if not is_lfs and diff_entry.get("type") in ["changed", "added", "removed"]:
                # Check size constraints
                skip_diff = False
                if current_size and current_size >= max_diff_size:
                    skip_diff = True
                    logger.debug(
                        f"Skipping diff for {path}: current size {current_size} >= {max_diff_size}"
                    )
                if previous_size and previous_size >= max_diff_size:
                    skip_diff = True
                    logger.debug(
                        f"Skipping diff for {path}: previous size {previous_size} >= {max_diff_size}"
                    )

                if not skip_diff:
                    # Get file content and generate diff
                    try:
                        logger.debug(
                            f"Generating diff for {path} (type={diff_entry.get('type')}, is_lfs={is_lfs})"
                        )

                        # Get current content (for added/changed)
                        current_lines = []
                        if diff_entry.get("type") in ["changed", "added"]:
                            current_bytes = await client.get_object(
                                repository=lakefs_repo, ref=commit_id, path=path
                            )
                            current_content = current_bytes.decode(
                                "utf-8", errors="ignore"
                            )
                            current_lines = current_content.splitlines(keepends=True)
                            logger.debug(
                                f"Fetched current content for {path}: {len(current_content)} chars, {len(current_lines)} lines"
                            )

                        # Get previous content (for changed/removed)
                        previous_lines = []
                        if (
                            diff_entry.get("type") in ["changed", "removed"]
                            and parent_id
                        ):
                            previous_bytes = await client.get_object(
                                repository=lakefs_repo, ref=parent_id, path=path
                            )
                            previous_content = previous_bytes.decode(
                                "utf-8", errors="ignore"
                            )
                            previous_lines = previous_content.splitlines(keepends=True)
                            logger.debug(
                                f"Fetched previous content for {path}: {len(previous_content)} chars, {len(previous_lines)} lines"
                            )

                        # Generate unified diff
                        diff_lines = list(
                            difflib.unified_diff(
                                previous_lines,
                                current_lines,
                                fromfile=f"a/{path}",
                                tofile=f"b/{path}",
                                lineterm="\n",
                            )
                        )
                        # Join with newlines (lineterm="" means no automatic newlines)
                        diff_text = "".join(diff_lines)
                        # Keep diff even if empty string (don't convert to None)
                        file_info["diff"] = diff_text
                        logger.info(
                            f"Generated diff for {path}: {len(diff_text)} chars, {len(diff_lines)} lines, type={diff_entry.get('type')}"
                        )
                    except Exception as e:
                        logger.exception(f"Failed to generate diff for {path}", e)
                        file_info["diff"] = None
                else:
                    logger.info(
                        f"Skipping diff for {path}: file too large (current={current_size}, prev={previous_size})"
                    )
                    file_info["diff"] = None

            return file_info

        # Process all diff entries in parallel
        files = await asyncio.gather(
            *[process_diff_entry(entry) for entry in diff_results]
        )

        # Get our commit record for user info using repository FK
        our_commit = get_commit(commit_id, repo_row)

        response = {
            "commit_id": commit_id,
            "parent_commit": parent_id,
            "message": lakefs_commit.get("message", ""),
            "author": (
                our_commit.author.username  # Use FK relationship
                if our_commit
                else (lakefs_commit.get("committer") or "unknown")
            ),
            "date": lakefs_commit.get("creation_date"),
            "files": files,
        }

        logger.debug(f"Retrieved diff for commit {commit_id[:8]}: {len(files)} files")
        return response

    except Exception as e:
        logger.exception(f"Failed to get commit diff for {repo_id}/{commit_id}", e)
        return hf_server_error(f"Failed to get commit diff: {str(e)}")
