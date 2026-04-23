"""Storage quota management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuhub.db import Repository, User
from kohakuhub.db_operations import get_organization, get_user_organization
from kohakuhub.logger import get_logger
from kohakuhub.auth.dependencies import get_current_user, get_optional_user
from kohakuhub.auth.permissions import (
    check_repo_read_permission,
    check_repo_write_permission,
)
from kohakuhub.api.quota.util import (
    get_storage_info,
    set_quota,
    update_namespace_storage,
    get_repo_storage_info,
    set_repo_quota,
    update_repository_storage,
)

logger = get_logger("QUOTA")
router = APIRouter()


class QuotaInfo(BaseModel):
    """Storage quota information."""

    namespace: str
    is_organization: bool
    quota_bytes: int | None
    used_bytes: int
    available_bytes: int | None
    percentage_used: float | None


class PublicQuotaInfo(BaseModel):
    """Public storage quota information with separate private/public tracking."""

    namespace: str
    is_organization: bool
    # Public storage (always visible)
    public_quota_bytes: int | None
    public_used_bytes: int
    public_available_bytes: int | None
    public_percentage_used: float | None
    # Private storage (only visible with permission)
    private_quota_bytes: int | None = None
    private_used_bytes: int | None = None
    private_available_bytes: int | None = None
    private_percentage_used: float | None = None
    # Total (calculated from visible fields)
    total_used_bytes: int
    # Permission indicator
    can_see_private: bool


class SetQuotaRequest(BaseModel):
    """Request to set storage quota."""

    quota_bytes: int | None  # None = unlimited


def _build_legacy_quota_info(
    namespace: str, is_org: bool, info: dict[str, int | float | None]
) -> QuotaInfo:
    """Build a legacy single-quota view on top of split private/public quotas."""
    private_quota = info["private_quota_bytes"]
    public_quota = info["public_quota_bytes"]
    quota_bytes = (
        None
        if private_quota is None or public_quota is None
        else private_quota + public_quota
    )
    used_bytes = info["total_used_bytes"]
    available_bytes = (
        None if quota_bytes is None else max(0, quota_bytes - used_bytes)
    )
    percentage_used = (
        None
        if quota_bytes is None or quota_bytes == 0
        else (used_bytes / quota_bytes * 100)
    )

    return QuotaInfo(
        namespace=namespace,
        is_organization=is_org,
        quota_bytes=quota_bytes,
        used_bytes=used_bytes,
        available_bytes=available_bytes,
        percentage_used=percentage_used,
    )


class RepoQuotaInfo(BaseModel):
    """Repository storage quota information."""

    repo_id: str
    repo_type: str
    namespace: str
    # Repository-specific
    quota_bytes: int | None
    used_bytes: int
    available_bytes: int | None
    percentage_used: float | None
    # Effective quota (what's actually enforced)
    effective_quota_bytes: int | None
    # Namespace context
    namespace_quota_bytes: int | None
    namespace_used_bytes: int
    namespace_available_bytes: int | None
    is_inheriting: bool


class RepoStorageItem(BaseModel):
    """Storage information for a single repository."""

    repo_id: str
    repo_type: str
    name: str
    private: bool
    quota_bytes: int | None
    used_bytes: int
    percentage_used: float | None
    is_inheriting: bool
    created_at: str


class RepoStorageListResponse(BaseModel):
    """List of repositories with storage information."""

    namespace: str
    is_organization: bool
    total_repos: int
    repositories: list[RepoStorageItem]


@router.get("/api/quota/{namespace}")
async def get_quota(
    namespace: str,
    user: User = Depends(get_current_user),
):
    """Get storage quota information for a user or organization.

    Args:
        namespace: Username or organization name
        user: Current authenticated user

    Returns:
        Quota information

    Raises:
        HTTPException: If not authorized or namespace not found
    """

    # Check if namespace is organization
    org = get_organization(namespace)
    is_org = org is not None

    # Check if namespace exists
    if not is_org:
        target_user = User.get_or_none(User.username == namespace)
        if not target_user:
            raise HTTPException(
                404, detail={"error": f"User or organization not found: {namespace}"}
            )

    # Get storage info
    info = get_storage_info(namespace, is_org)

    return _build_legacy_quota_info(namespace, is_org, info)


@router.put("/api/quota/{namespace}")
async def update_quota(
    namespace: str,
    request: SetQuotaRequest,
    user: User = Depends(get_current_user),
):
    """Set storage quota for a user or organization.

    Only the user themselves or organization admins can set quotas.

    Args:
        namespace: Username or organization name
        request: Quota settings
        user: Current authenticated user

    Returns:
        Updated quota information

    Raises:
        HTTPException: If not authorized or namespace not found
    """

    # Check if namespace is organization
    org = get_organization(namespace)
    is_org = org is not None

    # Authorization check
    if is_org:
        # For orgs, must be admin member
        member = get_user_organization(user, org)
        is_admin = member and member.role in ("admin", "super-admin")
        if not is_admin:
            raise HTTPException(
                403,
                detail={
                    "error": "Only organization admins can set quota for organizations"
                },
            )
    else:
        # For users, must be themselves (or we could add admin check here)
        if user.username != namespace:
            raise HTTPException(
                403, detail={"error": "You can only set quota for yourself"}
            )

    # Set quota
    info = set_quota(
        namespace,
        private_quota_bytes=request.quota_bytes,
        public_quota_bytes=request.quota_bytes,
        is_org=is_org,
    )

    return _build_legacy_quota_info(namespace, is_org, info)


@router.post("/api/quota/{namespace}/recalculate")
async def recalculate_storage(
    namespace: str,
    user: User = Depends(get_current_user),
):
    """Recalculate storage usage for a user or organization.

    This can be useful if storage tracking gets out of sync.

    Args:
        namespace: Username or organization name
        user: Current authenticated user

    Returns:
        Updated quota information

    Raises:
        HTTPException: If not authorized or namespace not found
    """

    # Check if namespace is organization
    org = get_organization(namespace)
    is_org = org is not None

    # Authorization check (same as update_quota)
    if is_org:
        # For orgs, must be admin member
        member = get_user_organization(user, org)
        is_admin = member and member.role in ("admin", "super-admin")
        if not is_admin:
            raise HTTPException(
                403,
                detail={
                    "error": "Only organization admins can recalculate quota for organizations"
                },
            )
    else:
        # For users, must be themselves
        if user.username != namespace:
            raise HTTPException(
                403, detail={"error": "You can only recalculate quota for yourself"}
            )

    # Recalculate storage
    logger.info(f"Recalculating storage for {'org' if is_org else 'user'} {namespace}")
    await update_namespace_storage(namespace, is_org)

    # Get updated info
    info = get_storage_info(namespace, is_org)

    return _build_legacy_quota_info(namespace, is_org, info)


@router.get("/api/quota/{namespace}/public")
async def get_public_quota(
    namespace: str,
    user: User | None = Depends(get_optional_user),
):
    """Get public storage quota information with permission-based private data access.

    This endpoint is designed for profile pages. It returns:
    - Public storage quota (always visible to everyone)
    - Private storage quota (only visible if viewer has permission to see private repos)

    Permission to see private quota:
    - User viewing their own profile
    - Organization members viewing org profile

    Args:
        namespace: Username or organization name
        user: Current authenticated user (optional)

    Returns:
        Public quota information with conditional private data

    Raises:
        HTTPException: If namespace not found
    """

    # Check if namespace is organization
    org = get_organization(namespace)
    is_org = org is not None

    # Check if namespace exists
    target_user = None
    if not is_org:
        target_user = User.get_or_none(User.username == namespace)
        if not target_user:
            raise HTTPException(
                404, detail={"error": f"User or organization not found: {namespace}"}
            )

    # Determine if viewer can see private storage info
    can_see_private = False

    if user:
        if is_org:
            # For organizations: user must be a member
            membership = get_user_organization(user, org)
            can_see_private = membership is not None
        else:
            # For users: user must be viewing their own profile
            can_see_private = user.username == namespace

    # Get storage info
    info = get_storage_info(namespace, is_org)

    # Build response based on permissions
    response_data = {
        "namespace": namespace,
        "is_organization": is_org,
        # Public storage (always visible)
        "public_quota_bytes": info["public_quota_bytes"],
        "public_used_bytes": info["public_used_bytes"],
        "public_available_bytes": info["public_available_bytes"],
        "public_percentage_used": info["public_percentage_used"],
        # Permission indicator
        "can_see_private": can_see_private,
    }

    # Add private storage info if user has permission
    if can_see_private:
        response_data.update(
            {
                "private_quota_bytes": info["private_quota_bytes"],
                "private_used_bytes": info["private_used_bytes"],
                "private_available_bytes": info["private_available_bytes"],
                "private_percentage_used": info["private_percentage_used"],
                "total_used_bytes": info["total_used_bytes"],
            }
        )
    else:
        # Only show public usage total
        response_data["total_used_bytes"] = info["public_used_bytes"]

    return PublicQuotaInfo(**response_data)


# ============================================================================
# Repository-specific quota endpoints
# ============================================================================


@router.get("/api/quota/repo/{repo_type}/{namespace}/{name}")
async def get_repo_quota(
    repo_type: str,
    namespace: str,
    name: str,
    user: User | None = Depends(get_optional_user),
):
    """Get storage quota information for a repository.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace (user or organization)
        name: Repository name
        user: Current authenticated user (optional)

    Returns:
        Repository quota information

    Raises:
        HTTPException: If repository not found or not authorized
    """
    # Get repository
    repo = Repository.get_or_none(
        (Repository.repo_type == repo_type)
        & (Repository.namespace == namespace)
        & (Repository.name == name)
    )

    if not repo:
        raise HTTPException(
            404, detail={"error": f"Repository not found: {namespace}/{name}"}
        )

    # Check read permission
    check_repo_read_permission(repo, user)

    # Get storage info
    info = get_repo_storage_info(repo)

    return RepoQuotaInfo(
        repo_id=repo.full_id,
        repo_type=repo.repo_type,
        namespace=repo.namespace,
        quota_bytes=info["quota_bytes"],
        used_bytes=info["used_bytes"],
        available_bytes=info["available_bytes"],
        percentage_used=info["percentage_used"],
        effective_quota_bytes=info["effective_quota_bytes"],
        namespace_quota_bytes=info["namespace_quota_bytes"],
        namespace_used_bytes=info["namespace_used_bytes"],
        namespace_available_bytes=info["namespace_available_bytes"],
        is_inheriting=info["is_inheriting"],
    )


@router.put("/api/quota/repo/{repo_type}/{namespace}/{name}")
async def update_repo_quota(
    repo_type: str,
    namespace: str,
    name: str,
    request: SetQuotaRequest,
    user: User = Depends(get_current_user),
):
    """Set storage quota for a repository.

    Only users with write permission can set repository quotas.
    Repository quota cannot exceed namespace available quota.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace (user or organization)
        name: Repository name
        request: Quota settings
        user: Current authenticated user

    Returns:
        Updated quota information

    Raises:
        HTTPException: If not authorized, repository not found, or quota exceeds limits
    """
    # Get repository
    repo = Repository.get_or_none(
        (Repository.repo_type == repo_type)
        & (Repository.namespace == namespace)
        & (Repository.name == name)
    )

    if not repo:
        raise HTTPException(
            404, detail={"error": f"Repository not found: {namespace}/{name}"}
        )

    # Check write permission
    check_repo_write_permission(repo, user)

    # Set quota with validation
    try:
        info = set_repo_quota(repo, request.quota_bytes)
    except ValueError as e:
        raise HTTPException(400, detail={"error": str(e)})

    return RepoQuotaInfo(
        repo_id=repo.full_id,
        repo_type=repo.repo_type,
        namespace=repo.namespace,
        quota_bytes=info["quota_bytes"],
        used_bytes=info["used_bytes"],
        available_bytes=info["available_bytes"],
        percentage_used=info["percentage_used"],
        effective_quota_bytes=info["effective_quota_bytes"],
        namespace_quota_bytes=info["namespace_quota_bytes"],
        namespace_used_bytes=info["namespace_used_bytes"],
        namespace_available_bytes=info["namespace_available_bytes"],
        is_inheriting=info["is_inheriting"],
    )


@router.post("/api/quota/repo/{repo_type}/{namespace}/{name}/recalculate")
async def recalculate_repo_storage(
    repo_type: str,
    namespace: str,
    name: str,
    user: User = Depends(get_current_user),
):
    """Recalculate storage usage for a repository.

    This can be useful if storage tracking gets out of sync.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace (user or organization)
        name: Repository name
        user: Current authenticated user

    Returns:
        Updated quota information

    Raises:
        HTTPException: If not authorized or repository not found
    """
    # Get repository
    repo = Repository.get_or_none(
        (Repository.repo_type == repo_type)
        & (Repository.namespace == namespace)
        & (Repository.name == name)
    )

    if not repo:
        raise HTTPException(
            404, detail={"error": f"Repository not found: {namespace}/{name}"}
        )

    # Check write permission
    check_repo_write_permission(repo, user)

    # Recalculate storage
    logger.info(f"Recalculating storage for repository {repo.full_id}")
    await update_repository_storage(repo)

    # Get updated info
    info = get_repo_storage_info(repo)

    return RepoQuotaInfo(
        repo_id=repo.full_id,
        repo_type=repo.repo_type,
        namespace=repo.namespace,
        quota_bytes=info["quota_bytes"],
        used_bytes=info["used_bytes"],
        available_bytes=info["available_bytes"],
        percentage_used=info["percentage_used"],
        effective_quota_bytes=info["effective_quota_bytes"],
        namespace_quota_bytes=info["namespace_quota_bytes"],
        namespace_used_bytes=info["namespace_used_bytes"],
        namespace_available_bytes=info["namespace_available_bytes"],
        is_inheriting=info["is_inheriting"],
    )


@router.get("/api/quota/{namespace}/repos")
async def list_namespace_repo_storage(
    namespace: str,
    user: User = Depends(get_current_user),
):
    """Get detailed storage breakdown for all repositories in a namespace.

    This endpoint requires write permission to the namespace as it includes
    private repositories and detailed storage information.

    Args:
        namespace: Username or organization name
        user: Current authenticated user

    Returns:
        List of all repositories with storage information

    Raises:
        HTTPException: If not authorized or namespace not found
    """
    # Check if namespace is organization
    org = get_organization(namespace)
    is_org = org is not None

    # Check if namespace exists
    if not is_org:
        target_user = User.get_or_none(User.username == namespace)
        if not target_user:
            raise HTTPException(
                404, detail={"error": f"User or organization not found: {namespace}"}
            )

    # Authorization check - requires write permission (to see private repos)
    if is_org:
        # For orgs, must be a member (any role can view storage breakdown)
        member = get_user_organization(user, org)
        if not member:
            raise HTTPException(
                403,
                detail={
                    "error": "Only organization members can view detailed storage breakdown"
                },
            )
    else:
        # For users, must be themselves
        if user.username != namespace:
            raise HTTPException(
                403, detail={"error": "You can only view your own storage breakdown"}
            )

    # Get all repositories for this namespace
    repos = list(Repository.select().where(Repository.namespace == namespace))

    # Build repository storage list
    repo_storage_list = []
    for repo in repos:
        info = get_repo_storage_info(repo)

        repo_storage_list.append(
            RepoStorageItem(
                repo_id=repo.full_id,
                repo_type=repo.repo_type,
                name=repo.name,
                private=repo.private,
                quota_bytes=info["quota_bytes"],
                used_bytes=info["used_bytes"],
                percentage_used=info["percentage_used"],
                is_inheriting=info["is_inheriting"],
                created_at=repo.created_at.isoformat() if repo.created_at else "",
            )
        )

    # Sort by used_bytes descending (largest first)
    repo_storage_list.sort(key=lambda x: x.used_bytes, reverse=True)

    return RepoStorageListResponse(
        namespace=namespace,
        is_organization=is_org,
        total_repos=len(repo_storage_list),
        repositories=repo_storage_list,
    )
