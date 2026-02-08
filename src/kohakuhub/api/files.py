"""File upload/download API endpoints (preupload, revision, download)."""

import asyncio
import base64
import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from kohakuhub.config import cfg
from kohakuhub.db import File, Repository, User
from kohakuhub.db_operations import (
    get_effective_lfs_threshold,
    get_file,
    get_organization,
    get_repository,
    should_use_lfs,
)
from kohakuhub.logger import get_logger
from kohakuhub.utils.datetime_utils import safe_strftime
from kohakuhub.auth.dependencies import get_current_user, get_optional_user
from kohakuhub.auth.permissions import (
    check_repo_read_permission,
    check_repo_write_permission,
)
from kohakuhub.utils.lakefs import get_lakefs_client, lakefs_repo_name, resolve_revision
from kohakuhub.utils.s3 import generate_download_presigned_url, parse_s3_uri
from kohakuhub.api.fallback import with_repo_fallback
from kohakuhub.api.xet import XET_ENABLE
from kohakuhub.api.quota.util import check_quota
from kohakuhub.api.utils.downloads import (
    get_or_create_tracking_cookie,
    track_download_async,
)
from kohakuhub.api.repo.utils.hf import (
    hf_repo_not_found,
    hf_revision_not_found,
    hf_server_error,
)

logger = get_logger("FILE")
router = APIRouter()


class RepoType(str, Enum):
    """Repository type enumeration."""

    model = "model"
    dataset = "dataset"
    space = "space"


# ========== Preupload Endpoint ==========


async def check_file_by_sha256(
    repo: Repository, path: str, sha256: str, size: int
) -> bool:
    """Check if file with same SHA256 and size already exists.

    Args:
        repo: Repository object
        path: File path
        sha256: SHA256 hash
        size: File size

    Returns:
        True if file should be ignored (already exists), False otherwise
    """
    existing = get_file(repo, path)
    if existing and existing.sha256 == sha256 and existing.size == size:
        return True
    return False


async def check_file_by_sample(
    repo_id: str, path: str, sample: str, size: int, lakefs_repo: str, revision: str
) -> bool:
    """Check if file with same content already exists by comparing sample.

    Args:
        repo_id: Repository ID
        path: File path
        sample: Base64 encoded sample content
        size: File size
        lakefs_repo: LakeFS repository name
        revision: Branch name

    Returns:
        True if file should be ignored (already exists), False otherwise
    """
    try:
        # Decode the sample
        sample_data = base64.b64decode(sample)
        sample_sha256 = hashlib.sha256(sample_data).hexdigest()

        # Try to get existing file from LakeFS
        try:
            client = get_lakefs_client()
            obj_stat = await client.stat_object(
                repository=lakefs_repo, ref=revision, path=path
            )

            # If file exists and size matches, download and compare
            if obj_stat["size_bytes"] == size:
                # Get the actual content to compare
                try:
                    obj_content = await client.get_object(
                        repository=lakefs_repo, ref=revision, path=path
                    )
                    # Read the content
                    existing_data = obj_content
                    existing_sha256 = hashlib.sha256(existing_data).hexdigest()

                    # If content matches, skip upload
                    if existing_sha256 == sample_sha256:
                        return True
                except Exception:
                    # If can't read content, assume changed
                    pass
        except Exception:
            # File doesn't exist, need to upload
            pass
    except Exception as e:
        logger.warning(f"Failed to decode sample for {path}: {e}")

    return False


async def process_preupload_file(
    file_info: dict,
    repo: Repository,
    repo_id: str,
    lakefs_repo: str,
    revision: str,
    threshold: int,  # Kept for backward compatibility, not used
) -> dict:
    """Process single file for preupload check.

    Args:
        file_info: File metadata dict
        repo: Repository object
        repo_id: Repository ID (for check_file_by_sample)
        lakefs_repo: LakeFS repository name
        revision: Branch name
        threshold: Deprecated - use repo.lfs_threshold_bytes instead

    Returns:
        Preupload result dict with path, uploadMode, shouldIgnore
    """
    path = file_info.get("path") or file_info.get("path_in_repo")
    size = int(file_info.get("size") or 0)
    sha256 = file_info.get("sha256", "")
    sample = file_info.get("sample", "")

    # Determine upload mode using repo-specific LFS rules (size AND/OR suffix)
    upload_mode = "lfs" if should_use_lfs(repo, path, size) else "regular"
    should_ignore = False

    # Check for existing file with same content
    if sha256:
        # If sha256 provided, use it for comparison (most reliable)
        should_ignore = await check_file_by_sha256(repo, path, sha256, size)
    elif sample and upload_mode == "regular":
        # For small files, compare sample content if no sha256 provided
        should_ignore = await check_file_by_sample(
            repo_id, path, sample, size, lakefs_repo, revision
        )

    return {
        "path": path,
        "uploadMode": upload_mode,
        "shouldIgnore": should_ignore,
    }


@router.post("/{repo_type}s/{namespace}/{name}/preupload/{revision}")
async def preupload(
    repo_type: RepoType,
    namespace: str,
    name: str,
    revision: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Check files before upload and determine upload mode.

    This endpoint implements HuggingFace's content deduplication mechanism.
    Files are checked against the database by SHA256 hash OR by comparing
    the base64 sample content for small files.

    Args:
        repo_type: Type of repository
        repo_id: Full repository ID
        revision: Branch name
        request: FastAPI request with file metadata
        user: Current authenticated user

    Returns:
        Upload instructions for each file

    Raises:
        HTTPException: If repository not found or invalid payload
    """
    repo_id = f"{namespace}/{name}"
    # Verify repository exists
    repo_row = get_repository(repo_type.value, namespace, name)
    if not repo_row:
        raise HTTPException(404, detail={"error": "Repository not found"})

    # Check write permission
    check_repo_write_permission(repo_row, user)

    # Parse request body
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": f"Invalid JSON: {e}"})

    if cfg.app.debug_log_payloads:
        logger.debug("==== Preupload Payload ====")
        logger.debug(json.dumps(body, indent=2))

    files = body.get("files")
    if not isinstance(files, list):
        raise HTTPException(400, detail={"error": "Missing or invalid 'files' array"})

    # Calculate total upload size
    total_upload_bytes = sum(int(f.get("size", 0)) for f in files)

    # Check if namespace is organization
    org = get_organization(namespace)
    is_org = org is not None

    # Check storage quota before upload (based on repo privacy)
    is_private = repo_row.private
    allowed, error_msg = check_quota(namespace, total_upload_bytes, is_private, is_org)
    if not allowed:
        raise HTTPException(
            status_code=413,  # Payload Too Large
            detail={
                "error": "Storage quota exceeded",
                "message": error_msg,
            },
        )

    # Get LakeFS repository name
    lakefs_repo = lakefs_repo_name(repo_type.value, repo_id)
    # Get effective LFS threshold for this repository
    threshold = get_effective_lfs_threshold(repo_row)

    # Process all files in parallel
    result_files = await asyncio.gather(
        *[
            process_preupload_file(
                f, repo_row, repo_id, lakefs_repo, revision, threshold
            )
            for f in files
        ]
    )

    return {"files": result_files}


# ========== Revision Info Endpoint ==========


@router.get("/{repo_type}s/{namespace}/{name}/revision/{revision}")
@with_repo_fallback("revision")
async def get_revision(
    repo_type: RepoType,
    namespace: str,
    name: str,
    revision: str,
    request: Request,
    expand: Optional[str] = None,
    fallback: bool = True,
    user: User | None = Depends(get_optional_user),
):
    """Get revision information for a repository.

    Args:
        repo_type: Type of repository
        repo_id: Full repository ID
        revision: Branch name or commit hash
        expand: Optional fields to expand
        user: Current authenticated user (optional)

    Returns:
        Revision metadata
    """
    repo_id = f"{namespace}/{name}"
    # Check if repository exists in database first
    repo_row = get_repository(repo_type.value, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type.value)

    # Check read permission for private repos
    check_repo_read_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type.value, repo_id)
    client = get_lakefs_client()

    # Resolve revision (supports both branch names and commit hashes)
    try:
        commit_id, commit_info = await resolve_revision(client, lakefs_repo, revision)
    except ValueError:
        return hf_revision_not_found(repo_id, revision)
    except Exception as e:
        return hf_server_error(f"Failed to resolve revision: {str(e)}")

    # Format last modified date
    last_modified = None
    if commit_info and commit_info.get("creation_date"):
        last_modified = datetime.fromtimestamp(commit_info["creation_date"]).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )

    # Format created_at
    created_at = safe_strftime(repo_row.created_at, "%Y-%m-%dT%H:%M:%S.%fZ")

    return {
        "id": repo_id,
        "author": repo_row.namespace,
        "sha": commit_id,
        "lastModified": last_modified,
        "createdAt": created_at,
        "private": repo_row.private,
        "downloads": repo_row.downloads,
        "likes": repo_row.likes_count,
        "gated": False,
        "files": [],  # Client will call /tree for file list
        "type": repo_type.value,
        "revision": revision,
        "commit": {
            "oid": commit_id,
            "date": commit_info.get("creation_date") if commit_info else None,
        },
        "xetEnabled": False,
    }


# ========== Download Endpoints ==========


async def _get_file_metadata(
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    path: str,
    user: User | None,
):
    """Shared logic to get file metadata for HEAD/GET requests."""
    repo_id = f"{namespace}/{name}"

    # Check repository exists and read permission
    repo_row = get_repository(repo_type, namespace, name)
    if repo_row:
        check_repo_read_permission(repo_row, user)

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()

    try:
        # Get object metadata from LakeFS
        obj_stat = await client.stat_object(
            repository=lakefs_repo, ref=revision, path=path
        )
    except Exception as e:
        raise HTTPException(404, detail={"error": f"File not found: {e}"})

    # Get commit hash for the revision
    try:
        branch = await client.get_branch(repository=lakefs_repo, branch=revision)
        commit_hash = branch["commit_id"]
    except Exception:
        # If not a branch, might be a commit hash already
        commit_hash = revision

    # Parse physical address to get S3 bucket and key
    physical_address = obj_stat["physical_address"]

    if not physical_address.startswith("s3://"):
        raise HTTPException(500, detail={"error": "Unsupported storage backend"})

    bucket, key = parse_s3_uri(physical_address)

    # Generate presigned download URL
    presigned_url = await generate_download_presigned_url(
        bucket=bucket,
        key=key,
        expires_in=86400,  # 1 day
        filename=path.split("/")[-1],  # Just the filename
    )

    # Prepare headers required by HuggingFace client
    file_size = obj_stat["size_bytes"]

    # Get correct checksum from database
    # sha256 column stores: git blob SHA1 for non-LFS, SHA256 for LFS
    file_record = get_file(repo_row, path) if repo_row else None

    # HuggingFace expects plain SHA256 hex (64 characters, unquoted)
    # For non-LFS: use git blob SHA1, for LFS: use SHA256
    etag_value = file_record.sha256 if file_record and file_record.sha256 else ""

    # Extract filename and encode for Content-Disposition header
    # HTTP headers must be ASCII/latin-1, so we use RFC 5987 encoding for Unicode filenames
    filename = path.split("/")[-1] if "/" in path else path
    # Use both filename= (for legacy clients) and filename*= (RFC 5987 for Unicode)
    # ASCII-safe fallback: percent-encode for filename=
    encoded_filename_ascii = quote(filename, safe="")
    # RFC 5987: filename*=UTF-8''encoded_filename
    encoded_filename_utf8 = quote(filename, safe="")

    response_headers = {
        # Critical headers for HuggingFace client
        "X-Repo-Commit": commit_hash or "",
        "X-Linked-Etag": etag_value,  # Plain hex, not quoted
        "X-Linked-Size": str(file_size) if file_size else "0",
        "ETag": etag_value,  # Plain hex, not quoted
        "Content-Length": str(file_size) if file_size else "0",
        "Accept-Ranges": "bytes",  # Support resume
        # Additional useful headers
        "Content-Type": obj_stat.get("content_type") or "application/octet-stream",
        "Last-Modified": (
            datetime.fromtimestamp(obj_stat["mtime"]).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            )
            if obj_stat.get("mtime")
            else ""
        ),
        "Content-Disposition": f"attachment; filename=\"{encoded_filename_ascii}\"; filename*=UTF-8''{encoded_filename_utf8}",
    }

    if file_record and file_record.sha256 and file_record.lfs and XET_ENABLE:
        # Try to enable xet here
        logger.debug(
            f"Enabling xet for {repo_type} {repo_id} {revision} {filename} with hash {file_record.sha256}"
        )
        response_headers["X-Xet-hash"] = file_record.sha256
        response_headers["X-Xet-Refresh-Route"] = (
            f"{cfg.app.base_url}/api/{repo_type}s/{repo_id}/xet-read-token/{revision}/{filename}"
        )

    return presigned_url, response_headers


@router.head("/{repo_type}s/{namespace}/{name}/resolve/{revision}/{path:path}")
@with_repo_fallback("resolve")
async def resolve_file_head(
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    path: str,
    request: Request,
    fallback: bool = True,
    user: User | None = Depends(get_optional_user),
):
    """Get file metadata (HEAD request).

    Returns only headers without body, for clients to check file info.
    """
    _, response_headers = await _get_file_metadata(
        repo_type, namespace, name, revision, path, user
    )

    # Return metadata headers only, NO redirect
    return Response(
        status_code=200,
        headers=response_headers,
    )


@router.get("/{repo_type}s/{namespace}/{name}/resolve/{revision}/{path:path}")
@with_repo_fallback("resolve")
async def resolve_file_get(
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    path: str,
    request: Request,
    fallback: bool = True,
    user: User | None = Depends(get_optional_user),
):
    """Download file (GET request).

    Returns 302 redirect to presigned S3 URL for actual download.
    Also tracks download in background for statistics.
    """
    presigned_url, _ = await _get_file_metadata(
        repo_type, namespace, name, revision, path, user
    )

    # Get repository for download tracking
    repo_row = get_repository(repo_type, namespace, name)

    # Track download asynchronously (don't block redirect)
    if repo_row:
        # Get session ID (auth session or tracking cookie)
        response_cookies = {}

        if user:
            # Authenticated: Use auth session ID
            session_id = request.cookies.get("session_id", "")
        else:
            # Anonymous: Use or create tracking cookie
            session_id = get_or_create_tracking_cookie(
                dict(request.cookies), response_cookies
            )

        # Track download in background (non-blocking)
        tracking_download_task = asyncio.create_task(
            track_download_async(
                repo=repo_row, file_path=path, session_id=session_id, user=user
            )
        )

        # Set tracking cookie if created for anonymous user
        if response_cookies:
            response = RedirectResponse(url=presigned_url, status_code=302)
            cookie_data = response_cookies["hf_download_session"]
            response.set_cookie(
                key="hf_download_session",
                value=cookie_data["value"],
                max_age=cookie_data["max_age"],
                httponly=cookie_data["httponly"],
                samesite=cookie_data["samesite"],
            )
            return response

    # Return 302 redirect to presigned S3 URL
    return RedirectResponse(
        url=presigned_url,
        status_code=302,
    )
