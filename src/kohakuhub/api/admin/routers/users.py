"""User management endpoints for admin API."""

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuhub.db import Repository, User, db
from kohakuhub.db_operations import create_user, delete_repository, delete_user
from kohakuhub.logger import get_logger
from kohakuhub.api.admin.utils import verify_admin_token

logger = get_logger("ADMIN")
router = APIRouter()


# ===== Models =====


class UserInfo(BaseModel):
    """User information response."""

    id: int
    username: str
    email: str | None  # Nullable for organizations
    email_verified: bool
    is_active: bool
    is_org: bool  # Add org flag
    private_quota_bytes: int | None
    public_quota_bytes: int | None
    private_used_bytes: int
    public_used_bytes: int
    created_at: str


class CreateUserRequest(BaseModel):
    """Request to create a new user."""

    username: str
    email: str
    password: str
    email_verified: bool = False
    is_active: bool = True
    private_quota_bytes: int | None = None
    public_quota_bytes: int | None = None


# ===== Endpoints =====


@router.get("/users/{username}")
async def get_user_info(
    username: str,
    _admin: bool = Depends(verify_admin_token),
):
    """Get detailed user or organization information.

    Args:
        username: Username or org name to query
        _admin: Admin authentication (dependency)

    Returns:
        User/org information

    Raises:
        HTTPException: If user/org not found
    """

    user = User.get_or_none(User.username == username)

    if not user:
        raise HTTPException(404, detail={"error": f"User/org not found: {username}"})

    return UserInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        email_verified=user.email_verified,
        is_active=user.is_active,
        is_org=user.is_org,  # Add org flag
        private_quota_bytes=user.private_quota_bytes,
        public_quota_bytes=user.public_quota_bytes,
        private_used_bytes=user.private_used_bytes,
        public_used_bytes=user.public_used_bytes,
        created_at=user.created_at.isoformat(),
    )


@router.get("/users")
async def list_users(
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    include_orgs: bool = False,
    _admin: bool = Depends(verify_admin_token),
):
    """List all users with quota information.

    Args:
        search: Search by username or email (optional)
        limit: Maximum number of users to return
        offset: Offset for pagination
        include_orgs: Include organizations in results (default: False)
        _admin: Admin authentication (dependency)

    Returns:
        List of users with quota information
    """

    # Filter by type
    if include_orgs:
        users_query = User.select()  # Include both users and orgs
    else:
        users_query = User.select().where(User.is_org == False)  # Users only

    # Add search filter if provided
    if search:
        users_query = users_query.where(
            (User.username.contains(search)) | (User.email.contains(search))
        )

    users_query = users_query.limit(limit).offset(offset)

    users = [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "email_verified": u.email_verified,
            "is_active": u.is_active,
            "is_org": u.is_org,  # Add org flag
            "private_quota_bytes": u.private_quota_bytes,
            "public_quota_bytes": u.public_quota_bytes,
            "private_used_bytes": u.private_used_bytes,
            "public_used_bytes": u.public_used_bytes,
            "created_at": u.created_at.isoformat(),
        }
        for u in users_query
    ]

    return {"users": users, "limit": limit, "offset": offset, "search": search}


@router.post("/users")
async def create_user_admin(
    request: CreateUserRequest,
    _admin: bool = Depends(verify_admin_token),
):
    """Create a new user (admin only).

    Args:
        request: User creation request
        _admin: Admin authentication (dependency)

    Returns:
        Created user information

    Raises:
        HTTPException: If username or email already exists
    """

    # Hash password
    password_hash = bcrypt.hashpw(request.password.encode(), bcrypt.gensalt()).decode()

    # Check if user already exists and create atomically
    with db.atomic():
        existing_username = User.get_or_none(User.username == request.username)
        if existing_username:
            raise HTTPException(
                400, detail={"error": f"Username already exists: {request.username}"}
            )

        existing_email = User.get_or_none(User.email == request.email)
        if existing_email:
            raise HTTPException(
                400, detail={"error": f"Email already exists: {request.email}"}
            )

        # Create user with quotas (defaults applied if not specified)
        user = create_user(
            username=request.username,
            email=request.email,
            password_hash=password_hash,
            email_verified=request.email_verified,
            is_active=request.is_active,
            private_quota_bytes=request.private_quota_bytes,
            public_quota_bytes=request.public_quota_bytes,
        )

    logger.info(f"Admin created user: {user.username}")

    return UserInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        email_verified=user.email_verified,
        is_active=user.is_active,
        is_org=user.is_org,
        private_quota_bytes=user.private_quota_bytes,
        public_quota_bytes=user.public_quota_bytes,
        private_used_bytes=user.private_used_bytes,
        public_used_bytes=user.public_used_bytes,
        created_at=user.created_at.isoformat(),
    )


@router.delete("/users/{username}")
async def delete_user_admin(
    username: str,
    force: bool = False,
    _admin: bool = Depends(verify_admin_token),
):
    """Delete a user (admin only).

    WARNING: This will delete all user's sessions, tokens, and org memberships.
    Repositories owned by the user will be transferred to a default admin user
    or deleted if force=true.

    Args:
        username: Username to delete
        force: If true, delete user even if they own repositories (repositories will be deleted)
        _admin: Admin authentication (dependency)

    Returns:
        Success message with deletion info

    Raises:
        HTTPException: If user not found or has repositories (without force)
    """

    user = User.get_or_none(User.username == username)

    if not user:
        raise HTTPException(404, detail={"error": f"User not found: {username}"})

    # Get all repositories owned by this user (using FK)
    owned_repos = list(Repository.select().where(Repository.owner == user))

    # Check if user owns repositories
    if owned_repos and not force:
        raise HTTPException(
            400,
            detail={
                "error": "User owns repositories",
                "message": f"User owns {len(owned_repos)} repository(ies). Use force=true to delete user and their repositories.",
                "owned_repositories": [
                    f"{r.repo_type}:{r.full_id}" for r in owned_repos
                ],
            },
        )

    # Delete user's repositories if force=true
    deleted_repos = []
    if owned_repos and force:
        # Delete repositories sequentially (sync DB operations)
        for repo in owned_repos:
            delete_repository(repo)
            logger.warning(f"Admin deleted repository: {repo.full_id}")
            deleted_repos.append(f"{repo.repo_type}:{repo.full_id}")

    # Delete user (already has db.atomic() inside)
    delete_user(user)

    logger.warning(
        f"Admin deleted user: {username} (deleted {len(deleted_repos)} repositories)"
    )

    return {
        "message": f"User deleted: {username}",
        "deleted_repositories": deleted_repos,
    }


@router.patch("/users/{username}/email-verification")
async def set_email_verification(
    username: str,
    verified: bool,
    _admin: bool = Depends(verify_admin_token),
):
    """Set email verification status for a user.

    Args:
        username: Username to update
        verified: Email verification status
        _admin: Admin authentication (dependency)

    Returns:
        Updated user information

    Raises:
        HTTPException: If user not found
    """

    user = User.get_or_none(User.username == username)
    if not user:
        raise HTTPException(404, detail={"error": f"User not found: {username}"})

    user.email_verified = verified
    user.save()

    logger.info(f"Admin set email_verified={verified} for user: {username}")

    return {
        "username": user.username,
        "email": user.email,
        "email_verified": user.email_verified,
    }


class UpdateQuotaRequest(BaseModel):
    """Request to update user/org quota."""

    private_quota_bytes: int | None = None
    public_quota_bytes: int | None = None


@router.put("/users/{username}/quota")
async def update_user_quota(
    username: str,
    request: UpdateQuotaRequest,
    _admin: bool = Depends(verify_admin_token),
):
    """Update storage quota for user or organization.

    Args:
        username: Username or org name
        request: Quota update request
        _admin: Admin authentication

    Returns:
        Updated quota information

    Raises:
        HTTPException: If user/org not found
    """
    user = User.get_or_none(User.username == username)
    if not user:
        raise HTTPException(404, detail={"error": f"User/org not found: {username}"})

    # Update quotas
    user.private_quota_bytes = request.private_quota_bytes
    user.public_quota_bytes = request.public_quota_bytes
    user.save()

    logger.info(
        f"Admin updated quota for {username}: "
        f"private={request.private_quota_bytes}, public={request.public_quota_bytes}"
    )

    return {
        "username": user.username,
        "private_quota_bytes": user.private_quota_bytes,
        "public_quota_bytes": user.public_quota_bytes,
        "private_used_bytes": user.private_used_bytes,
        "public_used_bytes": user.public_used_bytes,
    }
