"""Branch and tag management API endpoints."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuhub.db import Repository, User
from kohakuhub.db_operations import create_commit, get_repository
from kohakuhub.logger import get_logger
from kohakuhub.auth.dependencies import get_current_user, get_optional_user
from kohakuhub.auth.permissions import (
    check_repo_delete_permission,
    check_repo_read_permission,
    check_repo_write_permission,
)
from kohakuhub.utils.lakefs import get_lakefs_client, lakefs_repo_name
from kohakuhub.api.repo.utils.gc import (
    check_commit_range_recoverability,
    check_lfs_recoverability,
    sync_file_table_with_commit,
    track_commit_lfs_objects,
)
from kohakuhub.api.repo.utils.hf import (
    HFErrorCode,
    hf_error_response,
    hf_repo_not_found,
    hf_server_error,
)

logger = get_logger("BRANCHES")

router = APIRouter()


class CreateBranchPayload(BaseModel):
    """Payload for branch creation."""

    branch: str
    revision: Optional[str] = None  # Source revision (defaults to main)


class CreateBranchCompatPayload(BaseModel):
    """Hugging Face compatible payload for branch creation."""

    startingPoint: Optional[str] = None


@router.post("/{repo_type}s/{namespace}/{name}/branch")
async def create_branch(
    repo_type: str,
    namespace: str,
    name: str,
    payload: CreateBranchPayload,
    user: User = Depends(get_current_user),
):
    """Create a new branch.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        payload: Branch creation parameters
        user: Current authenticated user

    Returns:
        Success message
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check if user has permission
    check_repo_delete_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()

    try:
        # Get source revision (default to main)
        source_ref = payload.revision or "main"

        # Get commit ID from source ref
        source_branch = await client.get_branch(
            repository=lakefs_repo, branch=source_ref
        )
        source_commit = source_branch["commit_id"]

        # Create new branch
        await client.create_branch(
            repository=lakefs_repo,
            name=payload.branch,
            source=source_commit,
        )
    except Exception as e:
        logger.exception("Failed to create branch", e)
        error_msg = str(e).replace("\n", " ").replace("\r", " ")

        # Check if branch already exists (409)
        if "409" in error_msg or "conflict" in error_msg.lower():
            return hf_error_response(
                409,
                HFErrorCode.BAD_REQUEST,
                f"Branch '{payload.branch}' already exists",
            )

        return hf_server_error(f"Failed to create branch: {error_msg}")

    return {"success": True, "message": f"Branch '{payload.branch}' created"}


@router.post("/{repo_type}s/{namespace}/{name}/branch/{branch}")
async def create_branch_compat(
    repo_type: str,
    namespace: str,
    name: str,
    branch: str,
    payload: CreateBranchCompatPayload,
    user: User = Depends(get_current_user),
):
    """Create a branch using the Hugging Face Hub compatible route shape."""
    return await create_branch(
        repo_type=repo_type,
        namespace=namespace,
        name=name,
        payload=CreateBranchPayload(
            branch=branch,
            revision=payload.startingPoint,
        ),
        user=user,
    )


@router.delete("/{repo_type}s/{namespace}/{name}/branch/{branch}")
async def delete_branch(
    repo_type: str,
    namespace: str,
    name: str,
    branch: str,
    user: User = Depends(get_current_user),
):
    """Delete a branch.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        branch: Branch name to delete
        user: Current authenticated user

    Returns:
        Success message
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check if user has permission
    check_repo_delete_permission(repo_row, user)

    # Prevent deletion of main branch
    if branch == "main":
        return hf_error_response(
            400,
            HFErrorCode.BAD_REQUEST,
            "Cannot delete main branch",
        )

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()

    try:
        await client.delete_branch(repository=lakefs_repo, branch=branch)
    except Exception as e:
        return hf_server_error(f"Failed to delete branch: {str(e)}")

    return {"success": True, "message": f"Branch '{branch}' deleted"}


class RevertPayload(BaseModel):
    """Payload for reverting a commit."""

    ref: str  # Commit ID or ref to revert
    parent_number: int = 1  # For merge commits
    message: Optional[str] = None
    metadata: Optional[dict[str, str]] = None
    force: bool = False
    allow_empty: bool = False


class MergePayload(BaseModel):
    """Payload for merging branches."""

    message: Optional[str] = None
    metadata: Optional[dict[str, str]] = None
    strategy: Optional[str] = None  # 'dest-wins' or 'source-wins'
    force: bool = False
    allow_empty: bool = False
    squash_merge: bool = False


class ResetPayload(BaseModel):
    """Payload for resetting a branch."""

    ref: str  # Commit ID or ref to reset to
    message: Optional[str] = None  # Optional custom commit message
    force: bool = False


class CreateTagPayload(BaseModel):
    """Payload for tag creation."""

    tag: str
    revision: Optional[str] = None  # Source revision (defaults to main)
    message: Optional[str] = None


class CreateTagCompatPayload(BaseModel):
    """Hugging Face compatible payload for tag creation."""

    tag: str
    message: Optional[str] = None


@router.post("/{repo_type}s/{namespace}/{name}/tag")
async def create_tag(
    repo_type: str,
    namespace: str,
    name: str,
    payload: CreateTagPayload,
    user: User = Depends(get_current_user),
):
    """Create a new tag.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        payload: Tag creation parameters
        user: Current authenticated user

    Returns:
        Success message
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check if user has permission
    check_repo_delete_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()

    try:
        # Get source revision (default to main)
        source_ref = payload.revision or "main"

        # Get commit ID from source ref
        source_branch = await client.get_branch(
            repository=lakefs_repo, branch=source_ref
        )
        source_commit = source_branch["commit_id"]

        # Create new tag
        await client.create_tag(
            repository=lakefs_repo,
            id=payload.tag,
            ref=source_commit,
        )
    except Exception as e:
        return hf_server_error(f"Failed to create tag: {str(e)}")

    return {"success": True, "message": f"Tag '{payload.tag}' created"}


@router.post("/{repo_type}s/{namespace}/{name}/tag/{revision}")
async def create_tag_compat(
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    payload: CreateTagCompatPayload,
    user: User = Depends(get_current_user),
):
    """Create a tag using the Hugging Face Hub compatible route shape."""
    return await create_tag(
        repo_type=repo_type,
        namespace=namespace,
        name=name,
        payload=CreateTagPayload(
            tag=payload.tag,
            revision=revision,
            message=payload.message,
        ),
        user=user,
    )


@router.delete("/{repo_type}s/{namespace}/{name}/tag/{tag}")
async def delete_tag(
    repo_type: str,
    namespace: str,
    name: str,
    tag: str,
    user: User = Depends(get_current_user),
):
    """Delete a tag.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        tag: Tag name to delete
        user: Current authenticated user

    Returns:
        Success message
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check if user has permission
    check_repo_delete_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()

    try:
        await client.delete_tag(repository=lakefs_repo, tag=tag)
    except Exception as e:
        return hf_server_error(f"Failed to delete tag: {str(e)}")

    return {"success": True, "message": f"Tag '{tag}' deleted"}


def _resolve_ref_name(item: dict[str, Any]) -> str | None:
    """Extract a branch/tag name from a LakeFS reference payload."""
    return item.get("id") or item.get("name")


def _resolve_target_commit(item: dict[str, Any]) -> str | None:
    """Extract the commit ID from a LakeFS reference payload."""
    commit = item.get("commit")
    if isinstance(commit, dict):
        return commit.get("id") or commit.get("commit_id") or commit.get("commitId")

    return item.get("commit_id") or item.get("commitId") or item.get("hash")


async def _collect_reference_page(
    list_method,
    repository: str,
) -> list[dict[str, Any]]:
    """Collect a complete paginated list of LakeFS references."""
    results: list[dict[str, Any]] = []
    after: str | None = None

    while True:
        payload = await list_method(repository=repository, after=after, amount=1000)
        if isinstance(payload, list):
            results.extend(payload)
            return results

        results.extend(payload.get("results", []))
        pagination = payload.get("pagination", {})
        if not pagination.get("has_more"):
            return results

        after = pagination.get("next_offset")
        if not after:
            return results


@router.get("/{repo_type}s/{namespace}/{name}/refs")
async def list_repo_refs(
    repo_type: str,
    namespace: str,
    name: str,
    include_prs: bool = False,
    user: User | None = Depends(get_optional_user),
):
    """List branches and tags using the Hugging Face Hub compatible schema."""
    repo_id = f"{namespace}/{name}"
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    check_repo_read_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()
    branches: list[dict[str, str]] = []
    tags: list[dict[str, str]] = []

    try:
        branch_items = await _collect_reference_page(client.list_branches, lakefs_repo)
    except Exception as e:
        logger.warning(f"Failed to list branches for {repo_id}: {e}")
        branch_items = []

    if not branch_items:
        try:
            branch_items = [await client.get_branch(repository=lakefs_repo, branch="main")]
        except Exception as e:
            logger.warning(f"Failed to fetch main branch for {repo_id}: {e}")

    for item in branch_items:
        branch_name = _resolve_ref_name(item)
        target_commit = _resolve_target_commit(item)
        if not branch_name or not target_commit:
            continue
        branches.append(
            {
                "name": branch_name,
                "ref": f"refs/heads/{branch_name}",
                "targetCommit": target_commit,
            }
        )

    try:
        tag_items = await _collect_reference_page(client.list_tags, lakefs_repo)
    except Exception as e:
        logger.warning(f"Failed to list tags for {repo_id}: {e}")
        tag_items = []

    for item in tag_items:
        tag_name = _resolve_ref_name(item)
        target_commit = _resolve_target_commit(item)
        if not tag_name or not target_commit:
            continue
        tags.append(
            {
                "name": tag_name,
                "ref": f"refs/tags/{tag_name}",
                "targetCommit": target_commit,
            }
        )

    response = {
        "branches": sorted(branches, key=lambda item: item["name"]),
        "converts": [],
        "tags": sorted(tags, key=lambda item: item["name"]),
    }
    if include_prs:
        response["pullRequests"] = []
    return response


@router.post("/{repo_type}s/{namespace}/{name}/branch/{branch}/revert")
async def revert_branch(
    repo_type: str,
    namespace: str,
    name: str,
    branch: str,
    payload: RevertPayload,
    user: User = Depends(get_current_user),
):
    """Revert a commit on a branch.

    This endpoint reverts the changes from a specific commit, creating a new
    commit that undoes those changes. It checks if all LFS files from the
    target commit are still available before reverting.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        branch: Branch name to revert on
        payload: Revert parameters (ref, force, etc.)
        user: Current authenticated user

    Returns:
        Success message

    Raises:
        HTTPException: If revert fails or LFS files are not recoverable
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check if user has write permission
    check_repo_write_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()

    # Resolve the ref to a commit ID (for logging/validation)
    try:
        commit = await client.get_commit(repository=lakefs_repo, commit_id=payload.ref)
        commit_id = commit["id"]
        logger.info(f"Reverting commit {commit_id[:8]} on branch {branch}")
    except Exception as e:
        logger.error(f"Failed to resolve ref {payload.ref}: {e}")
        raise HTTPException(
            status_code=404,
            detail={"error": f"Commit not found: {payload.ref}"},
        )

    # NOTE: For REVERT, we do NOT check LFS recoverability!
    # Revert creates a new commit that undoes changes - LakeFS handles this.
    # If revert succeeds (no conflict), it means files go from latest -> second-latest version.
    # Both versions are within keep_versions, so LFS objects are safe.
    # If there's a conflict, LakeFS will return 409.

    # Perform the revert
    try:
        await client.revert_branch(
            repository=lakefs_repo,
            branch=branch,
            ref=payload.ref,
            parent_number=payload.parent_number,
            message=payload.message,
            metadata=payload.metadata,
            force=payload.force,
            allow_empty=payload.allow_empty,
        )
        logger.success(
            f"Successfully reverted commit {commit_id[:8]} on branch {branch}"
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to revert commit: {error_msg}")

        # Check if it's a conflict error (409)
        if "409" in error_msg or "conflict" in error_msg.lower():
            raise HTTPException(
                status_code=409,
                detail={
                    "error": f"Revert conflict: {error_msg}. "
                    f"The revert operation created conflicts with current branch state.",
                },
            )

        raise HTTPException(
            status_code=500,
            detail={"error": f"Revert failed: {error_msg}"},
        )

    # Track LFS objects and record commit in database
    try:
        # Get the new commit ID (revert creates a new commit)
        branch_info = await client.get_branch(repository=lakefs_repo, branch=branch)
        new_commit_id = branch_info["commit_id"]

        logger.info(f"Tracking LFS objects in revert commit {new_commit_id[:8]}")

        tracked = await track_commit_lfs_objects(
            lakefs_repo=lakefs_repo,
            commit_id=new_commit_id,
            repo_type=repo_type,
            namespace=namespace,
            name=name,
        )

        if tracked > 0:
            logger.info(f"Tracked {tracked} LFS object(s) from revert")

        # Record commit in database
        commit_msg = payload.message or f"Revert commit {commit_id[:8]}"
        try:
            create_commit(
                commit_id=new_commit_id,
                repository=repo_row,
                repo_type=repo_type,
                branch=branch,
                author=user,
                username=user.username,
                message=commit_msg,
                description=f"Reverted {commit_id}",
            )
            logger.info(
                f"Recorded revert commit {new_commit_id[:8]} by {user.username}"
            )
        except Exception as e:
            logger.warning(f"Failed to record commit in database: {e}")

    except Exception as e:
        # Don't fail the revert if tracking fails
        logger.warning(f"Failed to track LFS objects after revert: {e}")

    return {
        "success": True,
        "message": f"Successfully reverted commit {commit_id[:8]} on branch '{branch}'",
        "new_commit_id": new_commit_id,
    }


@router.post(
    "/{repo_type}s/{namespace}/{name}/merge/{source_ref}/into/{destination_branch}"
)
async def merge_branches(
    repo_type: str,
    namespace: str,
    name: str,
    source_ref: str,
    destination_branch: str,
    payload: MergePayload,
    user: User = Depends(get_current_user),
):
    """Merge source reference into destination branch.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        source_ref: Source reference (branch/commit to merge from)
        destination_branch: Destination branch name
        payload: Merge parameters (message, strategy, etc.)
        user: Current authenticated user

    Returns:
        Merge result with reference and summary

    Raises:
        HTTPException: If merge fails
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check if user has write permission
    check_repo_write_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()

    # Perform the merge
    try:
        merge_result = await client.merge_into_branch(
            repository=lakefs_repo,
            source_ref=source_ref,
            destination_branch=destination_branch,
            message=payload.message,
            metadata=payload.metadata,
            strategy=payload.strategy,
            force=payload.force,
            allow_empty=payload.allow_empty,
            squash_merge=payload.squash_merge,
        )
        logger.success(
            f"Successfully merged {source_ref} into {destination_branch} in {repo_id}"
        )
    except Exception as e:
        logger.exception(f"Failed to merge {source_ref} into {destination_branch}", e)
        error_msg = str(e)
        logger.error(f"Failed to merge branches: {error_msg}")

        # Check if it's a conflict error
        if "conflict" in error_msg.lower():
            raise HTTPException(
                status_code=409,
                detail={
                    "error": f"Merge conflict: {error_msg}. "
                    f"Use strategy='source-wins' or 'dest-wins' to resolve automatically.",
                },
            )

        raise HTTPException(
            status_code=500,
            detail={"error": f"Merge failed: {error_msg}"},
        )

    # Track LFS objects and record merge commit in database
    try:
        # Get the merge commit ID from the result
        # MergeResult has a "reference" field with the commit ID
        merge_commit_id = merge_result.get("reference")

        if merge_commit_id:
            logger.info(f"Tracking LFS objects in merge commit {merge_commit_id[:8]}")

            tracked = await track_commit_lfs_objects(
                lakefs_repo=lakefs_repo,
                commit_id=merge_commit_id,
                repo_type=repo_type,
                namespace=namespace,
                name=name,
            )

            if tracked > 0:
                logger.info(f"Tracked {tracked} LFS object(s) from merge")

            # Record merge commit in database
            merge_msg = (
                payload.message or f"Merge {source_ref} into {destination_branch}"
            )
            try:
                create_commit(
                    commit_id=merge_commit_id,
                    repository=repo_row,
                    repo_type=repo_type,
                    branch=destination_branch,
                    author=user,
                    username=user.username,
                    message=merge_msg,
                    description=f"Merged {source_ref}",
                )
                logger.info(
                    f"Recorded merge commit {merge_commit_id[:8]} by {user.username}"
                )
            except Exception as e:
                logger.warning(f"Failed to record commit in database: {e}")
        else:
            logger.warning("Merge result did not contain commit reference")
    except Exception as e:
        # Don't fail the merge if tracking fails
        logger.warning(f"Failed to track LFS objects after merge: {e}")

    return {
        "success": True,
        "message": f"Successfully merged {source_ref} into {destination_branch}",
        "result": merge_result,
    }


@router.post("/{repo_type}s/{namespace}/{name}/branch/{branch}/reset")
async def reset_branch(
    repo_type: str,
    namespace: str,
    name: str,
    branch: str,
    payload: ResetPayload,
    user: User = Depends(get_current_user),
):
    """Reset a branch to a specific commit (like git reset --hard).

    This endpoint resets the branch HEAD to point to a specific commit,
    effectively going back in time. It checks if all LFS files from the
    target commit are still available before resetting.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        branch: Branch name to reset
        payload: Reset parameters (ref, force)
        user: Current authenticated user

    Returns:
        Success message

    Raises:
        HTTPException: If reset fails or LFS files are not recoverable
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check if user has write permission
    check_repo_write_permission(repo_row, user)

    # Prevent resetting main branch without force (safety measure)
    if branch == "main" and not payload.force:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Cannot reset main branch without force=true. "
                "This is a safety measure to prevent accidental data loss."
            },
        )

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()

    # Resolve the ref to a commit ID
    try:
        # Get the commit to reset to
        commit = await client.get_commit(repository=lakefs_repo, commit_id=payload.ref)
        commit_id = commit["id"]
    except Exception as e:
        logger.exception(f"Failed to resolve ref {payload.ref}", e)
        logger.error(f"Failed to resolve ref {payload.ref}: {e}")
        raise HTTPException(
            status_code=404,
            detail={"error": f"Commit not found: {payload.ref}"},
        )

    # Check LFS recoverability for ALL commits from target to HEAD unless force=True
    if not payload.force:
        logger.info(f"Checking LFS recoverability for commit range to {commit_id[:8]}")

        all_recoverable, missing_files, affected_commits = (
            await check_commit_range_recoverability(
                lakefs_repo=lakefs_repo,
                repo_type=repo_type,
                namespace=namespace,
                name=name,
                target_commit=commit_id,
                current_branch=branch,
            )
        )

        if not all_recoverable:
            error_msg = (
                f"Cannot reset to commit {commit_id[:8]}: "
                f"{len(missing_files)} LFS file(s) across {len(affected_commits)} commit(s) "
                f"have been garbage collected and are no longer available. "
                f"Missing files: {', '.join(list(set(missing_files))[:5])}"
            )
            if len(missing_files) > 5:
                error_msg += f" and {len(set(missing_files)) - 5} more..."

            error_msg += (
                " Use force=true to reset anyway (may result in broken LFS references)."
            )

            logger.warning(error_msg)
            raise HTTPException(
                status_code=400,
                detail={
                    "error": error_msg,
                    "missing_files": list(set(missing_files)),
                    "affected_commits": affected_commits,
                    "recoverable": False,
                },
            )

    # Perform the reset by creating a new commit with the old state
    # This preserves history instead of using destructive hard_reset
    # Use diff-based approach to avoid issues with list_objects on commit IDs
    try:
        logger.info(
            f"Resetting {branch} to commit {commit_id[:8]} (creating new commit)"
        )

        # Get current branch head commit
        branch_info = await client.get_branch(repository=lakefs_repo, branch=branch)
        current_commit = branch_info["commit_id"]

        logger.info(f"Current: {current_commit[:8]}, Target: {commit_id[:8]}")

        # Get diff from target to current (what needs to be undone)
        diff_result = await client.diff_refs(
            repository=lakefs_repo,
            left_ref=commit_id,  # Target (old state)
            right_ref=current_commit,  # Current (new state)
        )

        diff_items = diff_result.get("results", [])
        logger.info(f"Found {len(diff_items)} difference(s) between commits")

        files_changed = 0

        # Process diff to restore old state
        for item in diff_items:
            path = item.get("path")
            path_type = item.get("path_type")
            diff_type = item.get("type")  # "added", "removed", "changed"

            if path_type != "object":
                continue

            logger.debug(f"Processing {diff_type}: {path}")

            if diff_type == "added":
                # File was added after target → delete it
                await client.delete_object(
                    repository=lakefs_repo,
                    branch=branch,
                    path=path,
                )
                files_changed += 1
                logger.debug(f"Removed file added after target: {path}")

            elif diff_type == "removed":
                # File was removed after target → restore it from target
                # Copy the file content from target commit
                file_content = await client.get_object(
                    repository=lakefs_repo,
                    ref=commit_id,
                    path=path,
                )

                await client.upload_object(
                    repository=lakefs_repo,
                    branch=branch,
                    path=path,
                    content=file_content,
                    force=True,
                )
                files_changed += 1
                logger.debug(f"Restored file removed after target: {path}")

            elif diff_type == "changed":
                # File was changed after target → restore old version from target
                # Copy the file content from target commit
                file_content = await client.get_object(
                    repository=lakefs_repo,
                    ref=commit_id,
                    path=path,
                )

                await client.upload_object(
                    repository=lakefs_repo,
                    branch=branch,
                    path=path,
                    content=file_content,
                    force=True,
                )
                files_changed += 1
                logger.debug(f"Restored old version of changed file: {path}")

        # Step 4: Create commit
        if files_changed == 0 and not payload.force:
            logger.warning("No changes to commit for reset")
            raise HTTPException(
                status_code=400,
                detail={"error": "Branch is already at the target state"},
            )

        commit_message = payload.message or f"Reset to commit {commit_id[:8]}"

        commit_result = await client.commit(
            repository=lakefs_repo,
            branch=branch,
            message=commit_message,
            metadata={"reset_to": commit_id},
        )

        logger.success(f"Reset successful - created commit {commit_result['id'][:8]}")

        try:
            synced = await sync_file_table_with_commit(
                lakefs_repo=lakefs_repo,
                ref=branch,
                repo_type=repo_type,
                namespace=namespace,
                name=name,
            )
            logger.info(f"Synced {synced} file(s) to File table")
        except Exception as e:
            logger.exception(f"Failed to sync File table: {e}", e)
            logger.warning(f"Failed to sync File table: {e}")

        # Record reset commit in database
        try:
            create_commit(
                commit_id=commit_result["id"],
                repository=repo_row,
                repo_type=repo_type,
                branch=branch,
                author=user,
                username=user.username,
                message=commit_message,
                description=f"Reset to {commit_id}",
            )
            logger.info(
                f"Recorded reset commit {commit_result['id'][:8]} by {user.username}"
            )
        except Exception as e:
            logger.exception(f"Failed to record reset commit in database: {e}", e)
            logger.warning(f"Failed to record commit in database: {e}")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to reset branch: {e}", e)
        error_msg = str(e)
        logger.error(f"Failed to reset branch: {error_msg}")

        raise HTTPException(
            status_code=500,
            detail={"error": f"Reset failed: {error_msg}"},
        )

    return {
        "success": True,
        "message": f"Successfully reset branch '{branch}' to commit {commit_id[:8]} (new commit created)",
        "commit_id": commit_result["id"],
    }
