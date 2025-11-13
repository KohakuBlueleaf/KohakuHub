"""Repository information and listing endpoints."""

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request

from kohakuhub.config import cfg
from kohakuhub.constants import DATETIME_FORMAT_ISO
from kohakuhub.db import Repository, User, UserOrganization
from kohakuhub.db_operations import (
    get_file,
    get_organization,
    get_repository,
    get_user_by_username,
    should_use_lfs,
)
from kohakuhub.logger import get_logger
from kohakuhub.auth.dependencies import get_optional_user
from kohakuhub.auth.permissions import (
    check_repo_read_permission,
    check_repo_write_permission,
)
from kohakuhub.utils.lakefs import get_lakefs_client, lakefs_repo_name
from kohakuhub.api.fallback import (
    with_list_aggregation,
    with_repo_fallback,
    with_user_fallback,
)
from kohakuhub.api.quota.util import get_repo_storage_info
from kohakuhub.utils.datetime_utils import safe_strftime
from kohakuhub.api.repo.utils.hf import (
    HFErrorCode,
    format_hf_datetime,
    hf_error_response,
    hf_repo_not_found,
)

logger = get_logger("REPO")
router = APIRouter()

RepoType = Literal["model", "dataset", "space"]


@router.get("/models/{namespace}/{repo_name}")
@router.get("/datasets/{namespace}/{repo_name}")
@router.get("/spaces/{namespace}/{repo_name}")
@with_repo_fallback("info")
async def get_repo_info(
    namespace: str,
    repo_name: str,
    request: Request,
    fallback: bool = True,
    user: User | None = Depends(get_optional_user),
):
    """Get repository information (without revision).

    This endpoint matches HuggingFace Hub API format:
    - /api/models/{namespace}/{repo_name}
    - /api/datasets/{namespace}/{repo_name}
    - /api/spaces/{namespace}/{repo_name}

    Note: For revision-specific info, use /{repo_type}s/{namespace}/{repo_name}/revision/{revision}
          which is handled in files.py

    Args:
        namespace: Repository namespace (user or organization)
        repo_name: Repository name
        request: FastAPI request object
        user: Current authenticated user (optional)

    Returns:
        Repository metadata or error response with headers
    """
    # Construct full repo ID
    repo_id = f"{namespace}/{repo_name}"

    # Determine repo type from path
    path = request.url.path
    match path:
        case _ if "/models/" in path:
            repo_type = "model"
        case _ if "/datasets/" in path:
            repo_type = "dataset"
        case _ if "/spaces/" in path:
            repo_type = "space"
        case _:
            return hf_error_response(
                404,
                HFErrorCode.INVALID_REPO_TYPE,
                "Invalid repository type",
            )

    # Check if repository exists in database
    repo_row = get_repository(repo_type, namespace, repo_name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check read permission for private repos
    check_repo_read_permission(repo_row, user)

    # Get LakeFS info for default branch
    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()

    # Get default branch info
    commit_id = None
    last_modified = None
    siblings = []

    try:
        branch = await client.get_branch(repository=lakefs_repo, branch="main")
        commit_id = branch["commit_id"]

        # Get commit details if available
        if commit_id:
            try:
                commit_info = await client.get_commit(
                    repository=lakefs_repo, commit_id=commit_id
                )
                if commit_info and commit_info.get("creation_date"):
                    last_modified = datetime.fromtimestamp(
                        commit_info["creation_date"]
                    ).strftime(DATETIME_FORMAT_ISO)
            except Exception as ex:
                logger.debug(f"Could not get commit info: {str(ex)}")

        # Get all files in the repository for siblings field
        # This is needed for transformers/diffusers with trust_remote_code
        try:
            all_results = []
            after = ""
            has_more = True

            # Fetch all files recursively from root
            while has_more:
                result = await client.list_objects(
                    repository=lakefs_repo,
                    ref="main",
                    prefix="",
                    delimiter="",  # No delimiter = recursive
                    amount=1000,
                    after=after,
                )

                all_results.extend(result["results"])

                # Check pagination
                if result.get("pagination") and result["pagination"].get("has_more"):
                    after = result["pagination"]["next_offset"]
                    has_more = True
                else:
                    has_more = False

            # Filter only file objects
            file_objects = [obj for obj in all_results if obj["path_type"] == "object"]

            # Fetch all file records in parallel for LFS files (using repo-specific settings)
            lfs_files = [
                obj
                for obj in file_objects
                if should_use_lfs(repo_row, obj["path"], obj.get("size_bytes", 0))
            ]

            file_records = {}
            if lfs_files:
                # Fetch all file records sequentially (sync DB operations)
                for obj in lfs_files:
                    try:
                        record = get_file(repo_row, obj["path"])
                        if record:
                            file_records[obj["path"]] = record
                    except Exception:
                        # Skip files that fail to fetch
                        pass

            # Convert to siblings format
            for obj in file_objects:
                sibling = {
                    "rfilename": obj["path"],
                    "size": obj.get("size_bytes", 0),
                }

                # Add LFS info if applicable (using repo-specific settings)
                if should_use_lfs(repo_row, obj["path"], obj.get("size_bytes", 0)):
                    file_record = file_records.get(obj["path"])
                    checksum = (
                        file_record.sha256
                        if file_record and file_record.sha256
                        else obj.get("checksum", "")
                    )
                    sibling["lfs"] = {
                        "sha256": checksum,
                        "size": obj["size_bytes"],
                        "pointerSize": 134,
                    }

                siblings.append(sibling)

        except Exception as ex:
            logger.exception(
                f"Could not fetch siblings for {lakefs_repo}: {str(ex)}", ex
            )
            logger.debug(f"Could not fetch siblings for {lakefs_repo}: {str(ex)}")
            # Continue without siblings if fetch fails

    except Exception as e:
        # Log warning but continue - repo exists even if LakeFS has issues
        logger.warning(f"Could not get branch info for {lakefs_repo}/main: {str(e)}")

    # Format created_at
    created_at = format_hf_datetime(repo_row.created_at)

    # Get storage info if user has read permission (already checked above)
    storage_info = None
    try:
        if user:  # Only include storage info for authenticated users
            storage_data = get_repo_storage_info(repo_row)
            storage_info = {
                "quota_bytes": storage_data["quota_bytes"],
                "used_bytes": storage_data["used_bytes"],
                "available_bytes": storage_data["available_bytes"],
                "percentage_used": storage_data["percentage_used"],
                "effective_quota_bytes": storage_data["effective_quota_bytes"],
                "is_inheriting": storage_data["is_inheriting"],
            }
    except Exception as e:
        logger.warning(f"Failed to get storage info for {repo_id}: {e}")
        # Continue without storage info if it fails

    # Return repository info in HuggingFace format
    response = {
        "_id": repo_row.id,
        "id": repo_id,
        "modelId": repo_id if repo_type == "model" else None,
        "author": repo_row.namespace,
        "sha": commit_id[:40],
        "lastModified": last_modified,
        "createdAt": created_at,
        "private": repo_row.private,
        "disabled": False,
        "gated": False,
        "downloads": repo_row.downloads,
        "likes": repo_row.likes_count,
        "tags": [],
        "pipeline_tag": None,
        "library_name": None,
        "siblings": siblings,
        "spaces": [],
        "models": [],
        "datasets": [],
    }

    # Add storage info if available
    if storage_info:
        response["storage"] = storage_info

    return response


def _filter_repos_by_privacy(q, user: Optional[User], author: Optional[str] = None):
    """Helper to filter repositories by privacy settings.

    Args:
        q: Peewee query object
        user: Current authenticated user (optional)
        author: Target author/namespace being queried (optional)

    Returns:
        Filtered query
    """
    if user:
        # Authenticated user can see:
        # 1. All public repos
        # 2. Their own private repos
        # 3. Private repos in organizations they're a member of

        # Get user's organizations using FK relationship
        user_orgs = [
            uo.organization.username
            for uo in UserOrganization.select().where(UserOrganization.user == user)
        ]

        # Build query: public OR (private AND owned by user or user's orgs)
        q = q.where(
            (Repository.private == False)
            | (
                (Repository.private == True)
                & (
                    (Repository.namespace == user.username)
                    | (Repository.namespace.in_(user_orgs))
                )
            )
        )
    else:
        # Not authenticated: only show public repos
        q = q.where(Repository.private == False)

    return q


async def _list_repos_internal(
    rt: str,
    author: Optional[str] = None,
    limit: int = 50,
    sort: str = "recent",
    user: User | None = None,
    fallback: bool = True,
) -> list[dict]:
    """Internal function to list repositories (called by decorated versions).

    Args:
        rt: Repository type ("model", "dataset", or "space")
        author: Filter by author/namespace
        limit: Maximum number of results
        sort: Sort order
        user: Current authenticated user (optional)
        fallback: Enable fallback to external sources (default: True)

    Returns:
        List of repositories
    """
    # Query database
    q = Repository.select().where(Repository.repo_type == rt)

    # Filter by author if specified
    if author:
        q = q.where(Repository.namespace == author)

    # Apply privacy filtering
    q = _filter_repos_by_privacy(q, user, author)

    # Apply sorting
    if sort == "trending":
        # Use trending algorithm (recent activity with decay)
        from kohakuhub.api.utils.trending import get_trending_repositories

        rows = get_trending_repositories(rt, limit=limit, days=7)
    else:
        # Standard sorting
        if sort == "likes":
            q = q.order_by(Repository.likes_count.desc())
        elif sort == "downloads":
            q = q.order_by(Repository.downloads.desc())
        else:  # recent (default)
            q = q.order_by(Repository.created_at.desc())

        rows = list(q.limit(limit))

    # Format response with lastModified from LakeFS
    client = get_lakefs_client()
    result = []

    for r in rows:
        last_modified = None
        sha = None

        # Try to get lastModified from LakeFS main branch
        try:
            lakefs_repo = lakefs_repo_name(rt, r.full_id)
            branch = await client.get_branch(repository=lakefs_repo, branch="main")
            sha = branch["commit_id"]

            if sha:
                commit_info = await client.get_commit(
                    repository=lakefs_repo, commit_id=sha
                )
                if commit_info and commit_info.get("creation_date"):
                    last_modified = datetime.fromtimestamp(
                        commit_info["creation_date"]
                    ).strftime(DATETIME_FORMAT_ISO)
        except Exception as e:
            logger.debug(f"Could not get lastModified for {r.full_id}: {str(e)}")

        result.append(
            {
                "id": r.full_id,
                "author": r.namespace,
                "private": r.private,
                "sha": sha,
                "lastModified": last_modified,
                "createdAt": safe_strftime(r.created_at, DATETIME_FORMAT_ISO),
                "downloads": r.downloads,
                "likes": r.likes_count,
                "gated": False,
                "tags": [],
            }
        )

    # Sorting already applied by database query
    return result


# Create decorated versions for each repo type
@with_list_aggregation("model")
async def _list_models_with_aggregation(author, limit, sort, user, fallback=True):
    return await _list_repos_internal("model", author, limit, sort, user, fallback)


@with_list_aggregation("dataset")
async def _list_datasets_with_aggregation(author, limit, sort, user, fallback=True):
    return await _list_repos_internal("dataset", author, limit, sort, user, fallback)


@with_list_aggregation("space")
async def _list_spaces_with_aggregation(author, limit, sort, user, fallback=True):
    return await _list_repos_internal("space", author, limit, sort, user, fallback)


@router.get("/models")
@router.get("/datasets")
@router.get("/spaces")
async def list_repos(
    author: Optional[str] = None,
    limit: int = Query(
        50, ge=1, le=100000
    ),  # Very high limit to support "get all repos"
    sort: str = Query("recent", regex="^(recent|likes|downloads|trending)$"),
    fallback: bool = Query(True, description="Enable fallback to external sources"),
    request: Request = None,
    user: User | None = Depends(get_optional_user),
):
    """List repositories of a specific type.

    Args:
        author: Filter by author/namespace
        limit: Maximum number of results
        sort: Sort order (recent, likes, downloads, trending) - default: recent
        fallback: Enable fallback to external sources (default: True)
        request: FastAPI request object
        user: Current authenticated user (optional)

    Returns:
        List of repositories (respects privacy settings, aggregated from local + external sources)
    """
    path = request.url.path

    match path:
        case _ if "models" in path:
            return await _list_models_with_aggregation(
                author, limit, sort, user, fallback
            )
        case _ if "datasets" in path:
            return await _list_datasets_with_aggregation(
                author, limit, sort, user, fallback
            )
        case _ if "spaces" in path:
            return await _list_spaces_with_aggregation(
                author, limit, sort, user, fallback
            )
        case _:
            return hf_error_response(
                404,
                HFErrorCode.INVALID_REPO_TYPE,
                "Unknown repository type",
            )


@router.get("/users/{username}/repos")
@with_user_fallback("repos")
async def list_user_repos(
    username: str,
    request: Request,
    limit: int = Query(
        100, ge=1, le=100000
    ),  # Very high limit to support "get all repos"
    sort: str = Query("recent", regex="^(recent|likes|downloads)$"),
    fallback: bool = True,
    user: User | None = Depends(get_optional_user),
):
    """List all repositories for a specific user/namespace.

    This endpoint returns repositories grouped by type, similar to a profile page.

    Args:
        username: Username or organization name
        limit: Maximum number of results per type
        sort: Sort order (recent, likes, downloads) - default: recent
        user: Current authenticated user (optional)

    Returns:
        Dict with models, datasets, and spaces lists
    """
    # Check if the username exists
    target_user = get_user_by_username(username)
    if not target_user:
        # Could also be an organization
        target_org = get_organization(username)
        if not target_org:
            return hf_error_response(
                404,
                HFErrorCode.BAD_REQUEST,
                f"User or organization '{username}' not found",
            )

    result = {
        "models": [],
        "datasets": [],
        "spaces": [],
    }

    for repo_type in ["model", "dataset", "space"]:
        q = Repository.select().where(
            (Repository.repo_type == repo_type) & (Repository.namespace == username)
        )

        # Apply privacy filtering
        q = _filter_repos_by_privacy(q, user, username)

        # Apply sorting
        if sort == "likes":
            q = q.order_by(Repository.likes_count.desc())
        elif sort == "downloads":
            q = q.order_by(Repository.downloads.desc())
        else:  # recent (default)
            q = q.order_by(Repository.created_at.desc())

        rows = list(q.limit(limit))

        key = repo_type + "s"
        repos_list = []
        client = get_lakefs_client()

        for r in rows:
            last_modified = None
            sha = None

            # Try to get lastModified from LakeFS
            try:
                lakefs_repo = lakefs_repo_name(repo_type, r.full_id)
                branch = await client.get_branch(repository=lakefs_repo, branch="main")
                sha = branch["commit_id"]

                if sha:
                    commit_info = await client.get_commit(
                        repository=lakefs_repo, commit_id=sha
                    )
                    if commit_info and commit_info.get("creation_date"):
                        last_modified = datetime.fromtimestamp(
                            commit_info["creation_date"]
                        ).strftime(DATETIME_FORMAT_ISO)
            except Exception as e:
                logger.debug(f"Could not get lastModified for {r.full_id}: {str(e)}")

            repos_list.append(
                {
                    "id": r.full_id,
                    "author": r.namespace,
                    "private": r.private,
                    "sha": sha,
                    "lastModified": last_modified,
                    "createdAt": safe_strftime(r.created_at, DATETIME_FORMAT_ISO),
                    "downloads": r.downloads,
                    "likes": r.likes_count,
                    "gated": False,
                    "tags": [],
                }
            )

        # Sorting already applied by database query
        result[key] = repos_list

    return result
