"""Repository CRUD operations (create, delete, move)."""

import asyncio
import json
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from kohakuhub.config import cfg
from kohakuhub.db import (
    File,
    Repository,
    StagingUpload,
    User,
    db,
    init_db,
)
from kohakuhub.db_operations import (
    get_file,
    get_organization,
    get_repository,
    should_use_lfs,
)
from kohakuhub.logger import get_logger
from kohakuhub.auth.dependencies import get_current_user, get_current_user_or_admin
from kohakuhub.auth.permissions import (
    check_namespace_permission,
    check_repo_delete_permission,
)
from kohakuhub.utils.lakefs import get_lakefs_client, lakefs_repo_name
from kohakuhub.utils.s3 import copy_s3_folder, delete_objects_with_prefix
from kohakuhub.lakefs_rest_client import StagingLocation, StagingMetadata
from kohakuhub.api.repo.utils.hf import (
    HFErrorCode,
    hf_error_response,
    hf_repo_not_found,
    hf_server_error,
    is_lakefs_not_found_error,
)
from kohakuhub.api.quota.util import (
    calculate_repository_storage,
    check_quota,
    increment_storage,
    update_repository_storage,
)
from kohakuhub.api.repo.utils.gc import cleanup_repository_storage
from kohakuhub.api.validation import normalize_name

logger = get_logger("REPO")
router = APIRouter()
init_db()

RepoType = Literal["model", "dataset", "space"]


def _repo_exists_response(
    repo_type: str,
    full_id: str,
    *,
    message: Optional[str] = None,
) -> Response:
    """Build the 409 response emitted when a repo already exists.

    `huggingface_hub.HfApi.create_repo(..., exist_ok=True)` swallows the 409 but
    immediately calls `r.json()["url"]` to build the returned `RepoUrl`, so the
    response body must be a JSON object containing a `url` field. The X-Error-*
    headers are preserved so clients following HF's header-based error protocol
    still get a structured error code.
    """
    body_message = message or f"Repository {full_id} already exists"
    body = json.dumps(
        {
            "url": f"{cfg.app.base_url}/{repo_type}s/{full_id}",
            "repo_id": full_id,
            "error": body_message,
        }
    )
    return Response(
        status_code=409,
        content=body,
        media_type="application/json",
        headers={
            "X-Error-Code": HFErrorCode.REPO_EXISTS,
            "X-Error-Message": body_message,
        },
    )


class CreateRepoPayload(BaseModel):
    """Payload for repository creation."""

    type: RepoType = "model"
    name: str
    organization: Optional[str] = None
    private: bool = False
    sdk: Optional[str] = None


@router.post("/repos/create")
async def create_repo(
    payload: CreateRepoPayload, user: User = Depends(get_current_user)
):
    """Create a new repository.

    Args:
        payload: Repository creation parameters
        user: Current authenticated user

    Returns:
        Created repository information
    """
    logger.info(
        f"Creating repository: {payload.organization or user.username}/{payload.name}"
    )
    namespace = payload.organization or user.username

    # Check if user has permission to use this namespace
    check_namespace_permission(namespace, user)

    full_id = f"{namespace}/{payload.name}"
    lakefs_repo = lakefs_repo_name(payload.type, full_id)

    # Check for exact match.
    # `huggingface_hub` only honors `exist_ok=True` when the server returns 409 (see
    # HfApi.create_repo in huggingface_hub/hf_api.py). Additionally, after the 409 is
    # caught the client unconditionally parses the response body as JSON to build the
    # returned RepoUrl (`d = r.json(); RepoUrl(d["url"], ...)`), so the body cannot be
    # empty — it must include a `url` field even though the error info also lives in
    # X-Error-* headers per HF's header-based error protocol.
    existing_repo = get_repository(payload.type, namespace, payload.name)
    if existing_repo:
        return _repo_exists_response(payload.type, full_id)

    # Check for normalized name conflicts
    normalized = normalize_name(payload.name)
    all_repos = Repository.select().where(
        (Repository.repo_type == payload.type) & (Repository.namespace == namespace)
    )
    for repo in all_repos:
        if normalize_name(repo.name) == normalized:
            conflict_full_id = f"{namespace}/{repo.name}"
            return _repo_exists_response(
                payload.type,
                conflict_full_id,
                message=f"Repository name conflicts with existing repository: {repo.name}",
            )

    # Create LakeFS repository
    client = get_lakefs_client()
    storage_namespace = f"s3://{cfg.s3.bucket}/{lakefs_repo}"

    try:
        await client.create_repository(
            name=lakefs_repo,
            storage_namespace=storage_namespace,
            default_branch="main",
        )
    except Exception as e:
        logger.exception(f"LakeFS repository creation failed for {full_id}", e)
        return hf_server_error(f"LakeFS repository creation failed: {str(e)}")

    # Store in database for listing/metadata
    Repository.get_or_create(
        repo_type=payload.type,
        namespace=namespace,
        name=payload.name,
        full_id=full_id,
        defaults={"private": payload.private, "owner": user},
    )

    return {
        "url": f"{cfg.app.base_url}/{payload.type}s/{full_id}",
        "repo_id": full_id,
    }


class DeleteRepoPayload(BaseModel):
    """Payload for repository deletion."""

    type: RepoType = "model"
    name: str
    organization: Optional[str] = None
    sdk: Optional[str] = None


@router.delete("/repos/delete")
async def delete_repo(
    payload: DeleteRepoPayload,
    auth: tuple[User | None, bool] = Depends(get_current_user_or_admin),
):
    """Delete a repository. (NOTE: This is IRREVERSIBLE)

    Accepts both user authentication and admin token (X-Admin-Token header).

    Args:
        name: Repository name.
        organization: Organization name (optional, defaults to user namespace).
        type: Repository type.
        auth: Tuple of (user, is_admin) from authentication

    Returns:
        Success message or error response.
    """
    user, is_admin = auth
    repo_type = payload.type

    # Determine namespace
    if is_admin:
        # Admin must specify organization (no default namespace)
        if not payload.organization:
            raise HTTPException(400, detail="Admin must specify organization parameter")
        namespace = payload.organization
    else:
        namespace = payload.organization or user.username

    full_id = f"{namespace}/{payload.name}"
    lakefs_repo = lakefs_repo_name(repo_type, full_id)

    # 1. Check if repository exists in database
    repo_row = get_repository(repo_type, namespace, payload.name)

    if not repo_row:
        return hf_repo_not_found(full_id, repo_type)

    # 2. Check if user has permission to delete this repository (admin bypasses)
    check_repo_delete_permission(repo_row, user, is_admin=is_admin)

    # 3. Clean up S3 storage FIRST (before deleting DB, so we can access repo FK)
    try:
        cleanup_stats = await cleanup_repository_storage(
            repo_type=repo_type,
            namespace=namespace,
            name=payload.name,
            lakefs_repo=lakefs_repo,
        )
        logger.info(
            f"S3 cleanup for {full_id}: "
            f"{cleanup_stats['repo_objects_deleted']} repo objects, "
            f"{cleanup_stats['lfs_objects_deleted']} LFS objects, "
            f"{cleanup_stats['lfs_history_deleted']} history records deleted"
        )
    except Exception as e:
        # S3 cleanup failure is non-fatal - we'll continue with deletion
        logger.warning(f"S3 cleanup failed for {full_id} (non-fatal): {e}")

    # 4. Delete LakeFS repository
    client = get_lakefs_client()
    try:
        # Note: Deleting a LakeFS repo is generally fast as it only deletes metadata
        await client.delete_repository(repository=lakefs_repo)
        logger.success(f"Successfully deleted LakeFS repository: {lakefs_repo}")
    except Exception as e:
        # LakeFS returns 404 if repo doesn't exist, which is fine
        if not is_lakefs_not_found_error(e):
            # If LakeFS deletion fails for other reasons, fail the whole operation
            logger.exception(f"LakeFS repository deletion failed for {lakefs_repo}", e)
            return hf_server_error(f"LakeFS repository deletion failed: {str(e)}")
        logger.info(f"LakeFS repository {lakefs_repo} not found/already deleted (OK)")

    # 5. Delete related metadata from database (CASCADE will handle related records)
    try:
        with db.atomic():
            # ForeignKey CASCADE will automatically delete:
            # - All files (File.repository)
            # - All commits (Commit.repository)
            # - All staging uploads (StagingUpload.repository)
            # - All LFS history (LFSObjectHistory.repository)
            repo_row.delete_instance()
        logger.success(f"Successfully deleted database records for: {full_id}")
    except Exception as e:
        logger.exception(f"Database deletion failed for {full_id}", e)
        return hf_server_error(f"Database deletion failed for {full_id}: {str(e)}")

    # 6. Return success response (200 OK with a simple message)
    # HuggingFace Hub delete_repo returns a simple 200 OK.
    return {"message": f"Repository '{full_id}' of type '{repo_type}' deleted."}


class MoveRepoPayload(BaseModel):
    """Payload for repository move/rename."""

    fromRepo: str  # format: "namespace/repo-name"
    toRepo: str  # format: "namespace/repo-name"
    type: str = "model"


class SquashRepoPayload(BaseModel):
    """Payload for repository squashing (clear history)."""

    repo: str  # format: "namespace/repo-name"
    type: str = "model"


async def _migrate_lakefs_repository(repo_type: str, from_id: str, to_id: str) -> None:
    """Migrate LakeFS repository with proper LFS handling using File table.

    Strategy:
    1. Get list of all objects with metadata from old repo
    2. Query File table to determine LFS status (source of truth)
    3. Create new LakeFS repo
    4. For each object:
       - LFS files (File.lfs=True): Link to SAME global lfs/ address (no duplication)
       - Regular files (File.lfs=False): Download and re-upload to new repo
    5. Commit all staged/uploaded objects
    6. Delete old LakeFS repo and old S3 folder

    This prevents LFS duplication and handles dynamic LFS rules correctly.

    Args:
        repo_type: Repository type (model/dataset/space)
        from_id: Source repository ID (namespace/name)
        to_id: Target repository ID (namespace/name)

    Raises:
        HTTPException: If migration fails
    """
    from_lakefs_repo = lakefs_repo_name(repo_type, from_id)
    to_lakefs_repo = lakefs_repo_name(repo_type, to_id)

    if from_lakefs_repo == to_lakefs_repo:
        # No migration needed (e.g., just renaming within namespace)
        return

    # Get source repository object for File table queries
    from_parts = from_id.split("/", 1)
    from_namespace, from_name = from_parts
    from_repo = get_repository(repo_type, from_namespace, from_name)

    if not from_repo:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Source repository {from_id} not found"},
        )

    client = get_lakefs_client()
    from_s3_prefix = f"{from_lakefs_repo}/"

    try:
        # 1. Get list of all objects with metadata from old repo
        logger.info(f"Listing objects in {from_lakefs_repo}")
        objects_to_migrate = []
        after = ""
        has_more = True

        while has_more:
            result = await client.list_objects(
                repository=from_lakefs_repo,
                ref="main",
                delimiter="",
                amount=1000,
                after=after,
            )

            for obj in result["results"]:
                if obj["path_type"] == "object":
                    objects_to_migrate.append(
                        {
                            "path": obj["path"],
                            "size_bytes": obj.get("size_bytes", 0),
                            "checksum": obj.get("checksum", ""),
                            "physical_address": obj.get("physical_address", ""),
                        }
                    )

            if result.get("pagination") and result["pagination"].get("has_more"):
                after = result["pagination"]["next_offset"]
                has_more = True
            else:
                has_more = False

        logger.info(f"Found {len(objects_to_migrate)} object(s) to migrate")

        # 2. Create new LakeFS repository
        storage_namespace = f"s3://{cfg.s3.bucket}/{to_lakefs_repo}"
        await client.create_repository(
            name=to_lakefs_repo,
            storage_namespace=storage_namespace,
            default_branch="main",
        )
        logger.info(f"Created new LakeFS repository: {to_lakefs_repo}")

        # 3. Process each object using File table to determine LFS status
        lfs_count = 0
        regular_count = 0

        for obj in objects_to_migrate:
            obj_path = obj["path"]
            size_bytes = obj["size_bytes"]

            # Query File table to get LFS status (source of truth)
            # This handles dynamic LFS rules and ensures consistency
            file_record = get_file(from_repo, obj_path)

            # Determine if file is LFS:
            # - Primary: Use File table record if exists (handles dynamic rules)
            # - Fallback: Use repo-specific LFS rules (size + suffix)
            if file_record:
                is_lfs = file_record.lfs
                logger.debug(
                    f"File {obj_path}: LFS={is_lfs} from File table "
                    f"(size={size_bytes}, db_lfs={file_record.lfs})"
                )
            else:
                # Fallback to repo-specific LFS rules if File record doesn't exist
                is_lfs = should_use_lfs(from_repo, obj_path, size_bytes)
                logger.warning(
                    f"File {obj_path}: No File record, using repo LFS rules "
                    f"(size={size_bytes}, is_lfs={is_lfs})"
                )

            try:
                if is_lfs:
                    # LFS file: Link to SAME global lfs/ address (no copy/upload)
                    # LFS files are stored at: s3://bucket/lfs/{sha[:2]}/{sha[2:4]}/{sha}
                    # They are shared across ALL repositories
                    staging_metadata = StagingMetadata(
                        staging=StagingLocation(
                            physical_address=obj["physical_address"]
                        ),
                        checksum=obj["checksum"],
                        size_bytes=size_bytes,
                    )

                    await client.link_physical_address(
                        repository=to_lakefs_repo,
                        branch="main",
                        path=obj_path,
                        staging_metadata=staging_metadata,
                    )
                    lfs_count += 1
                    logger.debug(f"Linked LFS file: {obj_path} ({size_bytes} bytes)")

                else:
                    # Regular file: Download and re-upload to new repo's data folder
                    # Each repo has its own copy in: s3://bucket/{repo}/data/...
                    content = await client.get_object(
                        repository=from_lakefs_repo,
                        ref="main",
                        path=obj_path,
                    )

                    await client.upload_object(
                        repository=to_lakefs_repo,
                        branch="main",
                        path=obj_path,
                        content=content,
                        force=True,
                    )
                    regular_count += 1
                    logger.debug(
                        f"Uploaded regular file: {obj_path} ({size_bytes} bytes)"
                    )

                if (lfs_count + regular_count) % 10 == 0:
                    logger.info(
                        f"Migrated {lfs_count + regular_count}/{len(objects_to_migrate)} objects "
                        f"({lfs_count} LFS, {regular_count} regular)..."
                    )

            except Exception as e:
                logger.warning(f"Failed to migrate object {obj_path}: {e}")
                # Continue with other objects

        logger.success(
            f"Migrated {lfs_count + regular_count} object(s): "
            f"{lfs_count} LFS linked, {regular_count} regular uploaded"
        )

        # 4. Commit all staged/uploaded objects
        if lfs_count + regular_count > 0:
            await client.commit(
                repository=to_lakefs_repo,
                branch="main",
                message=f"Repository moved from {from_id} to {to_id}",
            )
            logger.success(f"Committed all objects to new repository")

        # 5. Delete old LakeFS repository
        try:
            await client.delete_repository(repository=from_lakefs_repo)
            logger.info(f"Deleted old LakeFS repository: {from_lakefs_repo}")
        except Exception as e:
            if not is_lakefs_not_found_error(e):
                logger.warning(f"Failed to delete old LakeFS repo: {e}")

        # 6. Delete old S3 folder to free up the name
        deleted_count = await delete_objects_with_prefix(cfg.s3.bucket, from_s3_prefix)
        logger.success(f"Deleted {deleted_count} object(s) from old S3 prefix")

        logger.success(
            f"Successfully migrated repository from {from_lakefs_repo} to {to_lakefs_repo}"
        )

    except Exception as e:
        # Clean up on failure
        logger.exception(f"LakeFS repository migration failed: {e}")

        # Try to delete new LakeFS repo if it was created
        try:
            await client.delete_repository(repository=to_lakefs_repo)
            logger.info(f"Cleaned up new LakeFS repo: {to_lakefs_repo}")
        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Failed to migrate repository: {str(e)}",
            },
        )


def _update_repository_database_records(
    repo_row: Repository,
    from_id: str,
    to_id: str,
    from_namespace: str,
    to_namespace: str,
    to_name: str,
    moving_namespace: bool,
    repo_size: int,
    preserve_quota: bool = True,
) -> None:
    """Update database records for repository move (must be called within db.atomic()).

    Args:
        repo_row: Repository database record
        from_id: Source repository ID
        to_id: Target repository ID
        from_namespace: Source namespace
        to_namespace: Target namespace
        to_name: Target repository name
        moving_namespace: Whether namespace is changing
        repo_size: Repository size in bytes
        preserve_quota: Whether to preserve repository quota settings (default: True)
    """
    # Preserve current quota settings before update
    # NOTE: When moving to different namespace, reset quota to inherit from new namespace
    # When staying in same namespace (rename/squash), preserve quota settings
    if preserve_quota and not moving_namespace:
        current_quota_bytes = repo_row.quota_bytes
        current_used_bytes = repo_row.used_bytes
    else:
        # Reset quota when moving to different namespace
        current_quota_bytes = None
        current_used_bytes = repo_row.used_bytes  # Keep usage tracking

    # Update repository record
    Repository.update(
        namespace=to_namespace,
        name=to_name,
        full_id=to_id,
        quota_bytes=current_quota_bytes,
        used_bytes=current_used_bytes,
    ).where(Repository.id == repo_row.id).execute()

    # NOTE: File and StagingUpload records don't need updating!
    # They use ForeignKey to Repository.id (which doesn't change on move).
    # Only Repository.namespace, Repository.name, Repository.full_id change.

    # Update storage quotas if namespace changed
    if moving_namespace and repo_size > 0:
        # Check if source namespace is an organization
        source_org = get_organization(from_namespace)
        is_source_org = source_org is not None

        # Check if target namespace is an organization
        target_org = get_organization(to_namespace)
        is_target_org = target_org is not None

        # Decrement from source namespace
        increment_storage(
            namespace=from_namespace,
            bytes_delta=-repo_size,
            is_private=repo_row.private,
            is_org=is_source_org,
        )

        # Increment to target namespace
        increment_storage(
            namespace=to_namespace,
            bytes_delta=repo_size,
            is_private=repo_row.private,
            is_org=is_target_org,
        )

        logger.info(
            f"Updated storage quotas: {from_namespace} -{repo_size:,} bytes, "
            f"{to_namespace} +{repo_size:,} bytes"
        )


@router.post("/repos/move")
async def move_repo(
    payload: MoveRepoPayload,
    auth: tuple[User | None, bool] = Depends(get_current_user_or_admin),
):
    """Move/rename a repository.

    Matches HuggingFace Hub API: POST /api/repos/move
    Accepts both user authentication and admin token (X-Admin-Token header).

    Args:
        payload: Move parameters
        auth: Tuple of (user, is_admin) from authentication

    Returns:
        Success message with new URL
    """
    user, is_admin = auth
    from_id = payload.fromRepo
    to_id = payload.toRepo
    repo_type = payload.type

    # Parse IDs
    from_parts = from_id.split("/", 1)
    to_parts = to_id.split("/", 1)

    if len(from_parts) != 2:
        return hf_error_response(
            400, HFErrorCode.INVALID_REPO_ID, "Invalid source repository ID"
        )
    if len(to_parts) != 2:
        return hf_error_response(
            400, HFErrorCode.INVALID_REPO_ID, "Invalid destination repository ID"
        )

    from_namespace, from_name = from_parts
    to_namespace, to_name = to_parts

    # Check if source repository exists
    repo_row = get_repository(repo_type, from_namespace, from_name)
    if not repo_row:
        return hf_repo_not_found(from_id, repo_type)

    # Check permissions (admin bypasses)
    check_repo_delete_permission(repo_row, user, is_admin=is_admin)
    check_namespace_permission(to_namespace, user, is_admin=is_admin)

    # Check if destination already exists. See `_repo_exists_response` for why the
    # response includes a JSON body as well as X-Error-* headers.
    existing = get_repository(repo_type, to_namespace, to_name)
    if existing:
        return _repo_exists_response(repo_type, to_id)

    # Check storage quota (only for users, admin bypasses)
    repo_size = 0
    moving_namespace = from_namespace != to_namespace

    if moving_namespace and not is_admin:
        logger.info(
            f"Checking storage quota for moving {from_id} to {to_namespace} namespace"
        )

        # Calculate repository storage size
        repo_storage = await calculate_repository_storage(repo_row)
        repo_size = repo_storage["total_bytes"]

        # Check if target namespace is an organization
        target_org = get_organization(to_namespace)
        is_target_org = target_org is not None

        # Check quota for the target namespace based on repository privacy
        allowed, error_msg = check_quota(
            namespace=to_namespace,
            additional_bytes=repo_size,
            is_private=repo_row.private,
            is_org=is_target_org,
        )

        if not allowed:
            logger.warning(
                f"Quota check failed for moving {from_id} to {to_namespace}: {error_msg}"
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": error_msg,
                    "repo_size_bytes": repo_size,
                },
            )

        logger.info(
            f"Quota check passed for moving {from_id} to {to_namespace} "
            f"(repo size: {repo_size:,} bytes)"
        )

    # Migrate LakeFS repository FIRST (before updating DB)
    # This ensures File table queries use correct from_id
    from_lakefs_repo = lakefs_repo_name(repo_type, from_id)

    await _migrate_lakefs_repository(
        repo_type=repo_type,
        from_id=from_id,
        to_id=to_id,
    )

    # Update database records AFTER successful LakeFS migration
    with db.atomic():
        _update_repository_database_records(
            repo_row=repo_row,
            from_id=from_id,
            to_id=to_id,
            from_namespace=from_namespace,
            to_namespace=to_namespace,
            to_name=to_name,
            moving_namespace=moving_namespace,
            repo_size=repo_size,
        )

    # Clean up old S3 storage after successful migration
    # Note: Migration already deleted the old LakeFS repo, but S3 data remains
    # We need to clean up the S3 folder and unreferenced LFS objects
    try:
        cleanup_stats = await cleanup_repository_storage(
            repo_type=repo_type,
            namespace=from_namespace,
            name=from_name,
            lakefs_repo=from_lakefs_repo,
        )
        logger.info(
            f"S3 cleanup for moved repo {from_id}: "
            f"{cleanup_stats['repo_objects_deleted']} repo objects, "
            f"{cleanup_stats['lfs_objects_deleted']} LFS objects deleted"
        )
    except Exception as e:
        # S3 cleanup failure is non-fatal - repository is already moved successfully
        logger.warning(f"S3 cleanup failed for {from_id} (non-fatal): {e}")

    return {
        "success": True,
        "url": f"{cfg.app.base_url}/{repo_type}s/{to_id}",
        "message": f"Repository moved from {from_id} to {to_id}",
    }


@router.post("/repos/squash")
async def squash_repo(
    payload: SquashRepoPayload,
    auth: tuple[User | None, bool] = Depends(get_current_user_or_admin),
):
    """Squash repository to clear all commit history and compress storage.

    This operation:
    1. Moves repository to temporary name
    2. Moves back to original name
    3. Result: All commit history cleared, only current state preserved

    Accepts both user authentication and admin token (X-Admin-Token header).

    Args:
        payload: Squash parameters
        auth: Tuple of (user, is_admin) from authentication

    Returns:
        Success message

    Raises:
        HTTPException: If operation fails
    """
    user, is_admin = auth
    repo_id = payload.repo
    repo_type = payload.type

    # Parse repository ID
    parts = repo_id.split("/", 1)
    if len(parts) != 2:
        return hf_error_response(
            400, HFErrorCode.INVALID_REPO_ID, "Invalid repository ID"
        )

    namespace, name = parts

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)
    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check if user has permission (admin bypasses)
    check_repo_delete_permission(repo_row, user, is_admin=is_admin)

    # Generate temporary repository name
    temp_suffix = uuid.uuid4().hex[:8]
    temp_name = f"{name}-squash-{temp_suffix}"
    temp_id = f"{namespace}/{temp_name}"

    logger.info(f"Squashing repository {repo_id} via temporary name {temp_id}")

    try:
        # Step 1: Move to temporary name
        logger.info(f"Step 1: Moving {repo_id} to temporary {temp_id}")

        # Use internal move logic
        from_lakefs_repo = lakefs_repo_name(repo_type, repo_id)

        # Migrate LakeFS FIRST (before updating DB)
        await _migrate_lakefs_repository(
            repo_type=repo_type, from_id=repo_id, to_id=temp_id
        )

        # Update DB AFTER successful migration
        with db.atomic():
            _update_repository_database_records(
                repo_row=repo_row,
                from_id=repo_id,
                to_id=temp_id,
                from_namespace=namespace,
                to_namespace=namespace,
                to_name=temp_name,
                moving_namespace=False,  # Same namespace
                repo_size=0,  # No quota change
            )

        # Clean up old storage
        await cleanup_repository_storage(
            repo_type=repo_type,
            namespace=namespace,
            name=name,
            lakefs_repo=from_lakefs_repo,
        )

        logger.success(f"Moved to temporary repository: {temp_id}")

        # Wait for old LakeFS repo to be fully deleted (with exponential backoff)
        # This ensures we can reuse the name immediately
        client = get_lakefs_client()
        old_deleted = False
        max_attempts = 20
        for attempt in range(max_attempts):
            if not await client.repository_exists(from_lakefs_repo):
                old_deleted = True
                logger.info(
                    f"Confirmed old repository {from_lakefs_repo} deleted after {attempt + 1} check(s)"
                )
                break
            # Exponential backoff: 0.05s, 0.1s, 0.2s, 0.4s, ...
            wait_time = 0.05 * (2 ** min(attempt, 5))
            logger.debug(
                f"Old repository still exists, waiting {wait_time:.2f}s... (attempt {attempt + 1}/{max_attempts})"
            )
            await asyncio.sleep(wait_time)

        if not old_deleted:
            logger.warning(
                f"Old repository {from_lakefs_repo} still exists after {max_attempts} checks"
            )
            # Continue anyway - the 409 error will be caught and handled

        # Step 2: Move back to original name
        logger.info(f"Step 2: Moving {temp_id} back to {repo_id}")

        # Reload repo row (it was updated to temp name)
        repo_row = get_repository(repo_type, namespace, temp_name)

        # Migrate LakeFS FIRST (before updating DB)
        await _migrate_lakefs_repository(
            repo_type=repo_type, from_id=temp_id, to_id=repo_id
        )

        # Update DB AFTER successful migration
        with db.atomic():
            _update_repository_database_records(
                repo_row=repo_row,
                from_id=temp_id,
                to_id=repo_id,
                from_namespace=namespace,
                to_namespace=namespace,
                to_name=name,
                moving_namespace=False,  # Same namespace
                repo_size=0,  # No quota change
            )

        # Clean up temp storage
        temp_lakefs_repo = lakefs_repo_name(repo_type, temp_id)
        await cleanup_repository_storage(
            repo_type=repo_type,
            namespace=namespace,
            name=temp_name,
            lakefs_repo=temp_lakefs_repo,
        )

        # Wait for temp LakeFS repo to be fully deleted
        temp_deleted = False
        for attempt in range(20):
            if not await client.repository_exists(temp_lakefs_repo):
                temp_deleted = True
                logger.info(
                    f"Confirmed temp repository {temp_lakefs_repo} deleted after {attempt + 1} check(s)"
                )
                break
            wait_time = 0.05 * (2 ** min(attempt, 5))
            await asyncio.sleep(wait_time)

        if not temp_deleted:
            logger.warning(
                f"Temp repository {temp_lakefs_repo} still exists after cleanup"
            )

        # Step 3: Recalculate repository storage after squashing
        # Storage might have changed after clearing history
        final_repo = get_repository(repo_type, namespace, name)
        if final_repo:
            logger.info(f"Recalculating storage for squashed repository {repo_id}")
            try:
                await update_repository_storage(final_repo)
                logger.info(
                    f"Storage recalculated for {repo_id}: {final_repo.used_bytes:,} bytes"
                )
            except Exception as e:
                logger.warning(f"Failed to recalculate storage for {repo_id}: {e}")

        logger.success(f"Repository squashed successfully: {repo_id}")

        return {
            "success": True,
            "message": f"Repository {repo_id} squashed successfully. All commit history has been cleared.",
        }

    except Exception as e:
        logger.exception(f"Repository squash failed for {repo_id}: {e}")

        # Try to recover by moving back from temp if it exists
        try:
            temp_repo = get_repository(repo_type, namespace, temp_name)
            if temp_repo:
                logger.info(f"Attempting to recover from temp repository: {temp_id}")
                # Move back from temp
                with db.atomic():
                    _update_repository_database_records(
                        repo_row=temp_repo,
                        from_id=temp_id,
                        to_id=repo_id,
                        from_namespace=namespace,
                        to_namespace=namespace,
                        to_name=name,
                        moving_namespace=False,
                        repo_size=0,
                    )
                logger.info("Recovery attempt completed")
        except Exception as recovery_error:
            logger.exception(f"Recovery failed: {recovery_error}")

        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Failed to squash repository: {str(e)}",
            },
        )
