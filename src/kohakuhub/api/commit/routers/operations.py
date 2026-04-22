"""Commit creation endpoint - Refactored version with smaller functions."""

from datetime import datetime, timezone
from enum import Enum
import asyncio
import base64
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from kohakuhub.config import cfg
from kohakuhub.db import File, Repository, User
from kohakuhub.db_operations import (
    create_commit,
    create_file,
    delete_file,
    get_effective_lfs_threshold,
    get_file,
    get_organization,
    should_use_lfs,
    update_file,
)
from kohakuhub.logger import get_logger
from kohakuhub.auth.dependencies import get_current_user
from kohakuhub.auth.permissions import check_repo_write_permission
from kohakuhub.utils.lakefs import get_lakefs_client, lakefs_repo_name
from kohakuhub.utils.s3 import get_object_metadata, object_exists
from kohakuhub.api.quota.util import update_namespace_storage, update_repository_storage
from kohakuhub.api.repo.utils.gc import run_gc_for_file, track_lfs_object
from kohakuhub.api.repo.utils.hf import HFErrorCode

logger = get_logger("FILE")
router = APIRouter()


class RepoType(str, Enum):
    """Repository type enumeration."""

    model = "model"
    dataset = "dataset"
    space = "space"


def build_public_repo_path(repo_type: RepoType, repo_id: str) -> str:
    """Return the HF-compatible repository path used in commit responses."""
    return f"{repo_type.value}s/{repo_id}"


def calculate_git_blob_sha1(content: bytes) -> str:
    """Calculate SHA1 hash in git blob format.

    Git uses: sha1(f'blob {size}\\0' + content)

    Args:
        content: File content bytes

    Returns:
        SHA1 hex digest
    """
    size = len(content)
    sha = hashlib.sha1()
    sha.update(f"blob {size}\0".encode("utf-8"))
    sha.update(content)
    return sha.hexdigest()


async def process_regular_file(
    path: str,
    content_b64: str,
    encoding: str,
    repo: Repository,
    lakefs_repo: str,
    revision: str,
) -> bool:
    """Process regular file with inline base64 content.

    IMPORTANT: This should only be used for small files (< LFS threshold).
    Large files MUST use the lfsFile operation to avoid duplication.

    Args:
        path: File path in repository
        content_b64: Base64 encoded content
        encoding: Content encoding
        repo: Repository object
        lakefs_repo: LakeFS repository name
        revision: Branch name

    Returns:
        True if file was changed, False if unchanged

    Raises:
        HTTPException: If processing fails or file is too large for standard commit
    """
    if not encoding.startswith("base64"):
        raise HTTPException(400, detail={"error": f"Invalid file operation for {path}"})

    # Decode content
    try:
        data = base64.b64decode(content_b64)
    except Exception as e:
        raise HTTPException(400, detail={"error": f"Failed to decode base64: {e}"})

    # Check file size against LFS threshold (use repo-specific settings)
    file_size = len(data)
    lfs_threshold = get_effective_lfs_threshold(repo)

    # Also check if file suffix requires LFS
    if should_use_lfs(repo, path, file_size):
        # File should use LFS (either by size or suffix rule)
        raise HTTPException(
            400,
            detail={
                "error": f"File {path} should use LFS (size: {file_size} bytes, threshold: {lfs_threshold} bytes). "
                f"Files >= {lfs_threshold} bytes or matching LFS suffix rules must be uploaded through Git LFS. "
                f"Use 'lfsFile' operation instead of 'file' operation.",
                "file_size": file_size,
                "lfs_threshold": lfs_threshold,
                "suggested_operation": "lfsFile",
            },
        )

    # Calculate git blob SHA1 for non-LFS files (HuggingFace format)
    git_blob_sha1 = calculate_git_blob_sha1(data)

    # Check if file unchanged (deduplication)
    existing = get_file(repo, path)
    if existing and existing.sha256 == git_blob_sha1 and existing.size == len(data):
        if existing.is_deleted:
            # File was deleted, now being restored - need to re-upload to LakeFS
            logger.info(
                f"Restoring deleted non-LFS file: {path} (sha256={git_blob_sha1[:8]}, size={file_size:,})"
            )
        else:
            # File unchanged and active, skip
            logger.info(f"Skipping unchanged file: {path}")
            return False

    # File changed or needs restoration
    if existing and existing.is_deleted:
        logger.info(f"Uploading to restore non-LFS file: {path} ({file_size} bytes)")
    else:
        logger.info(f"Uploading regular file: {path} ({file_size} bytes)")

    # Upload to LakeFS
    try:
        client = get_lakefs_client()
        await client.upload_object(
            repository=lakefs_repo,
            branch=revision,
            path=path,
            content=data,
        )
    except Exception as e:
        raise HTTPException(500, detail={"error": f"Failed to upload {path}: {e}"})

    # Update database - store git blob SHA1 in sha256 column for non-LFS files
    File.insert(
        repository=repo,
        path_in_repo=path,
        size=len(data),
        sha256=git_blob_sha1,
        lfs=False,
        is_deleted=False,
        owner=repo.owner,
    ).on_conflict(
        conflict_target=(File.repository, File.path_in_repo),
        update={
            File.sha256: git_blob_sha1,
            File.size: len(data),
            File.lfs: False,  # Explicitly set to False
            File.is_deleted: False,  # File is active (un-delete if previously deleted)
            File.updated_at: datetime.now(timezone.utc),
        },
    ).execute()

    return True


async def process_lfs_file(
    path: str,
    oid: str,
    size: int,
    algo: str,
    repo: Repository,
    lakefs_repo: str,
    revision: str,
) -> tuple[bool, dict | None]:
    """Process LFS file that was uploaded to S3.

    Args:
        path: File path in repository
        oid: Object ID (SHA256 hash)
        size: File size in bytes
        algo: Hash algorithm (default: sha256)
        repo: Repository object
        lakefs_repo: LakeFS repository name
        revision: Branch name

    Returns:
        Tuple of (changed: bool, lfs_tracking_info: dict | None)

    Raises:
        HTTPException: If processing fails
    """
    if not oid:
        raise HTTPException(400, detail={"error": f"Missing OID for LFS file {path}"})

    # Check for existing file (including deleted files to detect re-upload)
    existing = File.get_or_none((File.repository == repo) & (File.path_in_repo == path))

    # Track old LFS object for potential deletion
    old_lfs_oid = None
    if existing and existing.lfs and existing.sha256 != oid:
        old_lfs_oid = existing.sha256
        logger.info(f"File {path} will be replaced: {old_lfs_oid} → {oid}")

    # Check if same content (including deleted files)
    # If same sha256+size, DON'T create new LFSObjectHistory
    same_content = existing and existing.sha256 == oid and existing.size == size

    if same_content:
        if existing.is_deleted:
            logger.info(
                f"[PROCESS_LFS_FILE] Re-uploading deleted file: {path} (sha256={oid[:8]}, size={size:,}) "
                f"- RESTORING in LakeFS (reusing existing LFSObjectHistory)"
            )
            # File was deleted, now being restored
            # Need to link physical address in LakeFS to restore the file
            # But DON'T create new LFSObjectHistory (already exists)

            # Construct S3 physical address
            lfs_key = f"lfs/{oid[:2]}/{oid[2:4]}/{oid}"
            physical_address = f"s3://{cfg.s3.bucket}/{lfs_key}"

            # Link the physical S3 object to LakeFS to restore
            try:
                staging_metadata = {
                    "staging": {
                        "physical_address": physical_address,
                    },
                    "checksum": f"{algo}:{oid}",
                    "size_bytes": size,
                }

                client = get_lakefs_client()
                await client.link_physical_address(
                    repository=lakefs_repo,
                    branch=revision,
                    path=path,
                    staging_metadata=staging_metadata,
                )

                logger.success(
                    f"Successfully restored LFS file in LakeFS: {path} "
                    f"(oid: {oid[:8]}, size: {size}, physical: {physical_address})"
                )

            except Exception as e:
                logger.exception(
                    f"Failed to restore LFS file in LakeFS: {path} "
                    f"(oid: {oid[:8]}, repo: {lakefs_repo}, branch: {revision})",
                    e,
                )
                raise HTTPException(
                    500,
                    detail={
                        "error": f"Failed to restore LFS file {path} in LakeFS: {str(e)}"
                    },
                )

            # Update database to mark as not deleted
            File.update(is_deleted=False, updated_at=datetime.now(timezone.utc)).where(
                File.id == existing.id
            ).execute()
            logger.success(f"Restored deleted file in DB: {path} (unmarked is_deleted)")

            # Return tracking info for new commit (reusing existing LFS object)
            return True, {
                "path": path,
                "sha256": oid,
                "size": size,
                "old_sha256": None,  # No old version (same content, just restoring)
            }
        else:
            logger.info(
                f"[PROCESS_LFS_FILE] File unchanged: {path} (sha256={oid[:8]}, size={size:,}) "
                f"- WILL TRACK in LFSObjectHistory"
            )
            # File exists and is active - normal case
            # Still return tracking info for new commit
            return False, {
                "path": path,
                "sha256": oid,
                "size": size,
                "old_sha256": None,  # No old version (file unchanged)
            }

    # File changed or new
    logger.info(f"Linking LFS file: {path}")

    # Construct S3 physical address
    lfs_key = f"lfs/{oid[:2]}/{oid[2:4]}/{oid}"
    physical_address = f"s3://{cfg.s3.bucket}/{lfs_key}"

    # Verify object exists in S3
    try:
        exists = await object_exists(cfg.s3.bucket, lfs_key)
        if not exists:
            logger.error(
                f"LFS object not found in S3: {oid[:8]} "
                f"(path: {path}, bucket: {cfg.s3.bucket}, key: {lfs_key})"
            )
            raise HTTPException(
                400,
                detail={
                    "error": f"LFS object {oid} not found in storage. "
                    f"Upload to S3 may have failed. Path: {lfs_key}"
                },
            )
    except HTTPException:
        raise  # Re-raise HTTPException as-is
    except Exception as e:
        logger.exception(
            f"Failed to check S3 existence for LFS object {oid[:8]} "
            f"(path: {path}, bucket: {cfg.s3.bucket}, key: {lfs_key})",
            e,
        )
        raise HTTPException(
            500, detail={"error": f"Failed to verify LFS object in S3: {str(e)}"}
        )

    # Get actual size from S3 to verify
    try:
        s3_metadata = await get_object_metadata(cfg.s3.bucket, lfs_key)
        actual_size = s3_metadata["size"]

        if actual_size != size:
            logger.warning(
                f"Size mismatch for {path}. Expected: {size}, Got: {actual_size} "
                f"(oid: {oid[:8]}, key: {lfs_key})"
            )
            size = actual_size
    except Exception as e:
        logger.exception(
            f"Failed to get S3 metadata for LFS object {oid[:8]} "
            f"(path: {path}, bucket: {cfg.s3.bucket}, key: {lfs_key})",
            e,
        )
        logger.warning(
            f"Could not verify S3 object metadata, continuing without size check"
        )

    # Link the physical S3 object to LakeFS
    try:
        staging_metadata = {
            "staging": {
                "physical_address": physical_address,
            },
            "checksum": f"{algo}:{oid}",
            "size_bytes": size,
        }

        client = get_lakefs_client()
        await client.link_physical_address(
            repository=lakefs_repo,
            branch=revision,
            path=path,
            staging_metadata=staging_metadata,
        )

        logger.success(
            f"Successfully linked LFS file in LakeFS: {path} "
            f"(oid: {oid[:8]}, size: {size}, physical: {physical_address})"
        )

    except Exception as e:
        logger.exception(
            f"Failed to link LFS file in LakeFS: {path} "
            f"(oid: {oid[:8]}, repo: {lakefs_repo}, branch: {revision}, "
            f"physical_address: {physical_address})",
            e,
        )
        raise HTTPException(
            500,
            detail={"error": f"Failed to link LFS file {path} in LakeFS: {str(e)}"},
        )

    # Update database
    File.insert(
        repository=repo,
        path_in_repo=path,
        size=size,
        sha256=oid,
        lfs=True,
        is_deleted=False,
        owner=repo.owner,
    ).on_conflict(
        conflict_target=(File.repository, File.path_in_repo),
        update={
            File.sha256: oid,
            File.size: size,
            File.lfs: True,
            File.is_deleted: False,  # File is active (un-delete if previously deleted)
            File.updated_at: datetime.now(timezone.utc),
        },
    ).execute()

    logger.success(f"Updated database record for LFS file: {path}")

    # Return tracking info for GC
    tracking_info = {
        "path": path,
        "sha256": oid,
        "size": size,
        "old_sha256": old_lfs_oid,
    }

    logger.info(
        f"[PROCESS_LFS_FILE] File changed/new: {path} (sha256={oid[:8]}, size={size:,}) "
        f"- WILL TRACK in LFSObjectHistory"
    )

    return True, tracking_info


async def process_deleted_file(
    path: str, repo: Repository, lakefs_repo: str, revision: str
) -> bool:
    """Process file deletion.

    Marks file as deleted (soft delete) instead of removing from database.
    This preserves LFSObjectHistory FK references for quota tracking.

    Args:
        path: File path to delete
        repo: Repository object
        lakefs_repo: LakeFS repository name
        revision: Branch name

    Returns:
        True (always changes repository)
    """
    logger.info(f"Deleting file: {path}")

    try:
        client = get_lakefs_client()
        await client.delete_object(repository=lakefs_repo, branch=revision, path=path)
        logger.success(f"Successfully deleted file from LakeFS: {path}")
    except Exception as e:
        # File might not exist, log warning but continue
        logger.warning(f"Failed to delete {path} from LakeFS: {e}")

    # Mark as deleted in database (soft delete)
    updated_count = (
        File.update(is_deleted=True, updated_at=datetime.now(timezone.utc))
        .where((File.repository == repo) & (File.path_in_repo == path))
        .execute()
    )

    if updated_count > 0:
        logger.success(f"Marked {path} as deleted in database (soft delete)")
    else:
        logger.info(f"File {path} was not in database")

    return True


async def process_deleted_folder(
    path: str, repo: Repository, lakefs_repo: str, revision: str
) -> bool:
    """Process folder deletion.

    Args:
        path: Folder path to delete
        repo: Repository object
        lakefs_repo: LakeFS repository name
        revision: Branch name

    Returns:
        True (always changes repository)
    """
    # Normalize folder path
    folder_path = path if path.endswith("/") else f"{path}/"
    logger.info(f"Deleting folder: {folder_path}")

    try:
        client = get_lakefs_client()

        # List all objects in the folder with pagination
        all_folder_objects = []
        after = ""
        has_more = True

        while has_more:
            objects = await client.list_objects(
                repository=lakefs_repo,
                ref=revision,
                prefix=folder_path,
                delimiter="",
                amount=1000,
                after=after,
            )

            all_folder_objects.extend(objects["results"])

            if objects.get("pagination") and objects["pagination"].get("has_more"):
                after = objects["pagination"]["next_offset"]
                has_more = True
            else:
                has_more = False

        # Delete each file concurrently
        file_objects = [
            obj for obj in all_folder_objects if obj["path_type"] == "object"
        ]

        async def delete_file_obj(obj):
            try:
                await client.delete_object(
                    repository=lakefs_repo, branch=revision, path=obj["path"]
                )
                logger.info(f"  Deleted: {obj['path']}")
                return obj["path"]
            except Exception as e:
                logger.warning(f"  Failed to delete {obj['path']}: {e}")
                return None

        results = await asyncio.gather(*[delete_file_obj(obj) for obj in file_objects])
        deleted_files = [path for path in results if path is not None]

        logger.success(f"Deleted {len(deleted_files)} files from folder {folder_path}")

        # Mark as deleted in database (soft delete)
        if deleted_files:
            updated_count = (
                File.update(is_deleted=True, updated_at=datetime.now(timezone.utc))
                .where(
                    (File.repository == repo)
                    & (File.path_in_repo.startswith(folder_path))
                )
                .execute()
            )
            logger.success(
                f"Marked {updated_count} file(s) as deleted in database (soft delete)"
            )

    except Exception as e:
        logger.warning(f"Error deleting folder {folder_path}: {e}")

    return True


async def process_copy_file(
    dest_path: str,
    src_path: str,
    src_revision: str,
    repo: Repository,
    lakefs_repo: str,
    revision: str,
) -> bool:
    """Process file copy operation.

    Args:
        dest_path: Destination file path
        src_path: Source file path
        src_revision: Source revision
        repo: Repository object
        lakefs_repo: LakeFS repository name
        revision: Branch name

    Returns:
        True (always changes repository)

    Raises:
        HTTPException: If copy fails
    """
    if not src_path:
        raise HTTPException(
            400, detail={"error": f"Missing srcPath for copyFile operation"}
        )

    logger.info(
        f"Copying file: {src_path} -> {dest_path} (from revision: {src_revision})"
    )

    try:
        # Get source file metadata from LakeFS
        client = get_lakefs_client()
        src_obj = await client.stat_object(
            repository=lakefs_repo, ref=src_revision, path=src_path
        )

        # Use LakeFS staging API to link the physical address
        staging_metadata = {
            "staging": {
                "physical_address": src_obj["physical_address"],
            },
            "checksum": src_obj["checksum"],
            "size_bytes": src_obj["size_bytes"],
        }

        await client.link_physical_address(
            repository=lakefs_repo,
            branch=revision,
            path=dest_path,
            staging_metadata=staging_metadata,
        )

        logger.success(
            f"Successfully linked {dest_path} to same physical address as {src_path}"
        )

        # Update database - copy file metadata
        src_file = get_file(repo, src_path)

        if src_file:
            File.insert(
                repository=repo,
                path_in_repo=dest_path,
                size=src_file.size,
                sha256=src_file.sha256,
                lfs=src_file.lfs,
                is_deleted=False,
                owner=repo.owner,
            ).on_conflict(
                conflict_target=(File.repository, File.path_in_repo),
                update={
                    File.sha256: src_file.sha256,
                    File.size: src_file.size,
                    File.lfs: src_file.lfs,
                    File.is_deleted: False,  # File is active
                    File.updated_at: datetime.now(timezone.utc),
                },
            ).execute()
        else:
            # If not in database, create entry based on LakeFS info
            # Use repo-specific LFS settings
            is_lfs = should_use_lfs(repo, dest_path, src_obj["size_bytes"])
            File.insert(
                repository=repo,
                path_in_repo=dest_path,
                size=src_obj["size_bytes"],
                sha256=src_obj["checksum"],
                lfs=is_lfs,
                is_deleted=False,
                owner=repo.owner,
            ).on_conflict(
                conflict_target=(File.repository, File.path_in_repo),
                update={
                    File.sha256: src_obj["checksum"],
                    File.size: src_obj["size_bytes"],
                    File.lfs: is_lfs,
                    File.is_deleted: False,  # File is active
                    File.updated_at: datetime.now(timezone.utc),
                },
            ).execute()

        logger.success(f"Successfully copied {src_path} to {dest_path}")

    except Exception as e:
        raise HTTPException(
            500,
            detail={
                "error": f"Failed to copy file {src_path} to {dest_path}: {str(e)}"
            },
        )

    return True


@router.post("/{repo_type}s/{namespace}/{name}/commit/{revision}")
async def commit(
    repo_type: RepoType,
    namespace: str,
    name: str,
    revision: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Create atomic commit with multiple file operations.

    Accepts NDJSON payload with header and file operations.
    Supports inline base64 content for small files and LFS references for large files.

    Args:
        repo_type: Type of repository
        namespace: Repository namespace
        name: Repository name
        revision: Branch name
        request: FastAPI request with NDJSON payload
        user: Current authenticated user

    Returns:
        Commit result with OID and URL

    Raises:
        HTTPException: If commit fails
    """
    repo_id = f"{namespace}/{name}"

    # `create_pr=True` sends ``?create_pr=1`` on the commit endpoint. KohakuHub
    # does not implement the discussions / pull-request workflow, and silently
    # dropping the flag (as the old handler did) means the commit lands on the
    # target branch instead of an isolated ``refs/pr/<N>`` — a compat-breaking
    # surprise. Reject it up front with a HuggingFace-compatible 501 so the
    # client surfaces a clear HfHubHTTPError carrying our X-Error-Message.
    if request.query_params.get("create_pr") in ("1", "true", "True"):
        raise HTTPException(
            status_code=501,
            detail={
                "error": (
                    "create_commit(create_pr=True) is not supported by KohakuHub. "
                    "Commit to a branch directly instead; the discussions / "
                    "pull-request workflow is not implemented."
                )
            },
            headers={
                "X-Error-Code": HFErrorCode.NOT_IMPLEMENTED,
                "X-Error-Message": (
                    "create_commit(create_pr=True) is not supported by KohakuHub. "
                    "Commit to a branch directly instead; the discussions / "
                    "pull-request workflow is not implemented."
                ),
            },
        )

    # Check repository exists and write permission
    repo_row = Repository.get_or_none(
        (Repository.full_id == repo_id) & (Repository.repo_type == repo_type.value)
    )
    if not repo_row:
        raise HTTPException(404, detail={"error": "Repository not found"})

    check_repo_write_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type.value, repo_id)
    client = get_lakefs_client()

    # Parse NDJSON payload
    raw = await request.body()
    lines = raw.decode("utf-8").splitlines()

    if cfg.app.debug_log_payloads:
        logger.debug("==== Commit Payload ====")
        for line in lines:
            logger.debug(line)

    # Parse header and operations
    header = None
    operations = []

    for line in lines:
        if not line.strip():
            continue

        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise HTTPException(400, detail={"error": f"Invalid JSON line: {e}"})

        key = obj.get("key")
        value = obj.get("value", {})

        if key == "header":
            header = value
        elif key in ("file", "lfsFile", "deletedFile", "deletedFolder", "copyFile"):
            operations.append({"key": key, "value": value})

    if header is None:
        raise HTTPException(400, detail={"error": "Missing commit header"})

    # Process operations using match-case
    files_changed = False
    pending_lfs_tracking = []

    for op in operations:
        key = op["key"]
        value = op["value"]
        path = value.get("path")
        logger.info(f"Processing {key}: {path}")

        match key:
            case "file":
                # Regular file with inline content
                changed = await process_regular_file(
                    path=path,
                    content_b64=value.get("content"),
                    encoding=(value.get("encoding") or "").lower(),
                    repo=repo_row,
                    lakefs_repo=lakefs_repo,
                    revision=revision,
                )
                files_changed = files_changed or changed

            case "lfsFile":
                # LFS file already in S3
                changed, lfs_info = await process_lfs_file(
                    path=path,
                    oid=value.get("oid"),
                    size=value.get("size"),
                    algo=value.get("algo", "sha256"),
                    repo=repo_row,
                    lakefs_repo=lakefs_repo,
                    revision=revision,
                )
                files_changed = files_changed or changed
                if lfs_info:
                    logger.debug(
                        f"[COMMIT_OP] Adding LFS file to tracking queue: {path} "
                        f"(sha256={lfs_info['sha256'][:8]}, size={lfs_info['size']:,})"
                    )
                    pending_lfs_tracking.append(lfs_info)
                else:
                    logger.warning(
                        f"[COMMIT_OP] process_lfs_file returned NO tracking info for: {path} "
                        f"(oid={value.get('oid', 'MISSING')[:8]})"
                    )

            case "deletedFile":
                # Delete single file
                changed = await process_deleted_file(
                    path=path,
                    repo=repo_row,
                    lakefs_repo=lakefs_repo,
                    revision=revision,
                )
                files_changed = files_changed or changed

            case "deletedFolder":
                # Delete folder recursively
                changed = await process_deleted_folder(
                    path=path,
                    repo=repo_row,
                    lakefs_repo=lakefs_repo,
                    revision=revision,
                )
                files_changed = files_changed or changed

            case "copyFile":
                # Copy file
                changed = await process_copy_file(
                    dest_path=path,
                    src_path=value.get("srcPath"),
                    src_revision=value.get("srcRevision", revision),
                    repo=repo_row,
                    lakefs_repo=lakefs_repo,
                    revision=revision,
                )
                files_changed = files_changed or changed

    # If no files changed, return early
    if not files_changed:
        try:
            branch = await client.get_branch(repository=lakefs_repo, branch=revision)
            commit_id = branch["commit_id"]
        except Exception:
            commit_id = "no-changes"

        commit_url = f"{build_public_repo_path(repo_type, repo_id)}/commit/{commit_id}"

        return {
            "commitUrl": commit_url,
            "commitOid": commit_id,
            "pullRequestUrl": None,
        }

    # Create commit in LakeFS
    commit_msg = header.get("summary", "Commit via API")
    commit_desc = header.get("description", "")
    logger.info(f"Commit message: {commit_msg}")

    try:
        commit_result = await client.commit(
            repository=lakefs_repo,
            branch=revision,
            message=commit_msg,
            metadata={"description": commit_desc} if commit_desc else None,
        )
    except Exception as e:
        raise HTTPException(500, detail={"error": f"Commit failed: {str(e)}"})

    # Poll to verify commit is accessible (LakeFS needs time to process large commits)
    commit_id = commit_result["id"]
    logger.info(f"Verifying commit {commit_id[:8]} is accessible...")
    max_attempts = 120
    for attempt in range(max_attempts):
        try:
            await client.get_commit(repository=lakefs_repo, commit_id=commit_id)
            logger.debug(
                f"Commit {commit_id[:8]} verified after {attempt + 1} attempts"
            )
            break
        except Exception as e:
            if attempt < max_attempts - 1:
                logger.debug(
                    f"Commit not ready yet (attempt {attempt + 1}/{max_attempts}), waiting..."
                )
                await asyncio.sleep(0.5)  # Wait 500ms before retry
            else:
                logger.warning(
                    f"Commit {commit_id[:8]} not accessible after {max_attempts} attempts, but continuing..."
                )
                break

    # Record commit in our database (track the actual user)
    try:
        create_commit(
            commit_id=commit_result["id"],
            repository=repo_row,
            repo_type=repo_type.value,
            branch=revision,
            author=user,
            username=user.username,
            message=commit_msg,
            description=commit_desc,
        )
        logger.info(f"Recorded commit {commit_result['id'][:8]} by {user.username}")
    except Exception as e:
        logger.warning(f"Failed to record commit in database: {e}")
        # Don't fail the commit if DB recording fails

    # Generate commit URL
    commit_url = (
        f"{build_public_repo_path(repo_type, repo_id)}/commit/{commit_result['id']}"
    )
    logger.success(f"Commit URL: {commit_url}")

    # Track LFS objects and run GC
    if pending_lfs_tracking:
        logger.info(
            f"[COMMIT_LFS_TRACKING] Processing {len(pending_lfs_tracking)} LFS file(s) "
            f"for commit {commit_result['id'][:8]}"
        )
        for lfs_info in pending_lfs_tracking:
            logger.debug(
                f"  - {lfs_info['path']}: sha256={lfs_info['sha256'][:8]}, size={lfs_info['size']:,}"
            )

            track_lfs_object(
                repo_type=repo_type.value,
                namespace=namespace,
                name=name,
                path_in_repo=lfs_info["path"],
                sha256=lfs_info["sha256"],
                size=lfs_info["size"],
                commit_id=commit_result["id"],
            )

            if cfg.app.lfs_auto_gc and lfs_info.get("old_sha256"):
                deleted_count = run_gc_for_file(
                    repo_type=repo_type.value,
                    namespace=namespace,
                    name=name,
                    path_in_repo=lfs_info["path"],
                    current_commit_id=commit_result["id"],
                )
                if deleted_count > 0:
                    logger.info(
                        f"GC: Cleaned up {deleted_count} old version(s) of {lfs_info['path']}"
                    )
    else:
        logger.warning(
            f"[COMMIT_LFS_TRACKING] No LFS files to track for commit {commit_result['id'][:8]}"
        )

    # Update storage usage for namespace and repository after successful commit
    try:
        # Recalculate repository storage (keeps repo.used_bytes accurate)
        await update_repository_storage(repo_row)
        logger.debug(
            f"Updated repository storage for {repo_id}: {repo_row.used_bytes:,} bytes"
        )

        # Check if namespace is organization (User with is_org=True)
        org = get_organization(namespace)
        is_org = org is not None

        # Recalculate namespace storage usage
        await update_namespace_storage(namespace, is_org)
        logger.debug(
            f"Updated storage usage for {'org' if is_org else 'user'} {namespace}"
        )
    except Exception as e:
        # Log error but don't fail the commit
        logger.warning(f"Failed to update storage usage for {namespace}: {e}")

    return {
        "commitUrl": commit_url,
        "commitOid": commit_result["id"],
        "pullRequestUrl": None,
    }
