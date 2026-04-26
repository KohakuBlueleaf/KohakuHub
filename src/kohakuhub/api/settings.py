"""User, organization, and repository settings API endpoints."""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from kohakuhub.config import cfg
from kohakuhub.db import User
from kohakuhub.db_operations import (
    get_organization,
    get_repository,
    get_user_by_email_excluding_id,
    get_user_by_username,
    get_user_organization,
    list_organization_members,
    update_organization,
    update_repository,
    update_user,
)
from kohakuhub.logger import get_logger
from kohakuhub.api.fallback import with_user_fallback
from kohakuhub.api.quota.util import calculate_repository_storage, check_quota
from kohakuhub.api.repo.utils.hf import hf_repo_not_found
from kohakuhub.auth.dependencies import get_current_user
from kohakuhub.auth.permissions import check_repo_delete_permission
from kohakuhub.utils.datetime_utils import safe_isoformat

logger = get_logger("SETTINGS")

router = APIRouter()


# ============================================================================
# User Settings API
# ============================================================================
# Note: /whoami-v2 is implemented in api/routers/misc.py


class UpdateUserSettingsRequest(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    bio: Optional[str] = None
    website: Optional[str] = None
    social_media: Optional[dict] = None  # {twitter_x, threads, github, huggingface}


@router.put("/users/{username}/settings")
async def update_user_settings(
    username: str,
    req: UpdateUserSettingsRequest,
    user: User = Depends(get_current_user),
):
    """Update user settings.

    Args:
        username: Username to update (must match authenticated user)
        req: Settings to update
        user: Current authenticated user

    Returns:
        Success message
    """
    # Verify user can only update their own settings
    if user.username != username:
        raise HTTPException(403, detail="Not authorized to update this user's settings")

    # Update fields if provided
    update_fields = {}

    if req.email is not None:
        # Check if email is already taken by another user
        existing = get_user_by_email_excluding_id(req.email, user.id)
        if existing:
            raise HTTPException(400, detail="Email already in use")
        update_fields["email"] = req.email
        update_fields["email_verified"] = False
        # TODO: Send new verification email

    if req.full_name is not None:
        update_fields["full_name"] = req.full_name

    if req.bio is not None:
        update_fields["bio"] = req.bio

    if req.website is not None:
        update_fields["website"] = req.website

    if req.social_media is not None:
        # Validate social_media structure
        if not isinstance(req.social_media, dict):
            raise HTTPException(400, detail="social_media must be a dictionary")

        # Store as JSON string
        update_fields["social_media"] = json.dumps(req.social_media)

    # Execute update if there are fields to update
    if update_fields:
        update_user(user, **update_fields)

    return {"success": True, "message": "User settings updated successfully"}


@router.get("/users/{username}/type")
async def get_namespace_type(username: str, request: Request, fallback: bool = True):
    """Get namespace type (user or org).

    This endpoint checks both local and fallback sources to determine if a namespace
    is a user or organization.

    For HuggingFace fallback:
    - Try /api/users/{name}/overview - if succeeds, it's a user
    - If 404, try /{name} (GET) - if succeeds, it's an org

    Args:
        username: Username or org name
        request: FastAPI request object
        fallback: Enable fallback to external sources

    Returns:
        Dict with type: "user" or "org"
    """
    # Check local first
    user = get_user_by_username(username)
    if user:
        return {"type": "org" if user.is_org else "user", "_source": "local"}

    org = get_organization(username)
    if org:
        return {"type": "org", "_source": "local"}

    # If not found locally and fallback enabled, try fallback sources
    if not fallback:
        raise HTTPException(404, detail="Namespace not found")

    # Try fallback - check profile which will determine type
    from kohakuhub.api.fallback.operations import try_fallback_user_profile

    profile = await try_fallback_user_profile(username)

    if profile:
        namespace_type = (
            "org" if profile.get("_hf_type") in ["org", "organization"] else "user"
        )
        return {"type": namespace_type, "_source": profile.get("_source", "unknown")}

    raise HTTPException(404, detail="Namespace not found")


@router.get("/users/{username}/profile")
@with_user_fallback("profile")
async def get_user_profile(username: str, request: Request, fallback: bool = True):
    """Get user public profile information.

    Args:
        username: Username to query
        request: FastAPI request object
        fallback: Enable fallback to external sources

    Returns:
        Public profile data
    """
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(404, detail="User not found")

    # Parse social_media JSON if exists
    social_media = None
    if user.social_media:
        try:
            social_media = json.loads(user.social_media)
        except json.JSONDecodeError:
            social_media = None

    return {
        "username": user.username,
        "full_name": user.full_name,
        "bio": user.bio,
        "website": user.website,
        "social_media": social_media,
        "created_at": safe_isoformat(user.created_at),
        "_source": "local",  # Tag local users
    }


# ============================================================================
# Organization Settings API
# ============================================================================


class UpdateOrganizationSettingsRequest(BaseModel):
    description: Optional[str] = None
    bio: Optional[str] = None
    website: Optional[str] = None
    social_media: Optional[dict] = None  # {twitter_x, threads, github, huggingface}


@router.put("/organizations/{org_name}/settings")
async def update_organization_settings(
    org_name: str,
    req: UpdateOrganizationSettingsRequest,
    user: User = Depends(get_current_user),
):
    """Update organization settings.

    Args:
        org_name: Organization name
        req: Settings to update
        user: Current authenticated user

    Returns:
        Success message
    """
    org = get_organization(org_name)
    if not org:
        raise HTTPException(404, detail="Organization not found")

    # Check if user is admin of the organization
    user_org = get_user_organization(user, org)
    if not user_org or user_org.role not in ["admin", "super-admin"]:
        raise HTTPException(
            403, detail="Not authorized to update organization settings"
        )

    # Update fields if provided
    update_fields = {}

    if req.description is not None:
        update_fields["description"] = req.description

    if req.bio is not None:
        update_fields["bio"] = req.bio

    if req.website is not None:
        update_fields["website"] = req.website

    if req.social_media is not None:
        # Validate social_media structure
        if not isinstance(req.social_media, dict):
            raise HTTPException(400, detail="social_media must be a dictionary")

        # Store as JSON string
        update_fields["social_media"] = json.dumps(req.social_media)

    # Execute update if there are fields to update
    if update_fields:
        update_organization(org, **update_fields)

    return {"success": True, "message": "Organization settings updated successfully"}


@router.get("/organizations/{org_name}/profile")
async def get_organization_profile(org_name: str):
    """Get organization public profile information.

    Args:
        org_name: Organization name

    Returns:
        Public profile data
    """
    org = get_organization(org_name)
    if not org:
        raise HTTPException(404, detail="Organization not found")

    # Parse social_media JSON if exists
    social_media = None
    if org.social_media:
        try:
            social_media = json.loads(org.social_media)
        except json.JSONDecodeError:
            social_media = None

    # Count members
    members = list_organization_members(org)
    member_count = len(members)

    return {
        "name": org.username,
        "description": org.description,
        "bio": org.bio,
        "website": org.website,
        "social_media": social_media,
        "member_count": member_count,
        "created_at": org.created_at.isoformat(),
    }


# ============================================================================
# Repository Settings API
# ============================================================================


class UpdateRepoSettingsPayload(BaseModel):
    """Payload for repository settings update."""

    private: Optional[bool] = None
    visibility: Optional[str] = None
    gated: Optional[str] = None  # "auto", "manual", or False/None
    lfs_threshold_bytes: Optional[int] = None  # NULL = use server default
    lfs_keep_versions: Optional[int] = None  # NULL = use server default
    lfs_suffix_rules: Optional[list[str]] = None  # NULL = no suffix rules


@router.put("/{repo_type}s/{namespace}/{name}/settings")
async def update_repo_settings(
    repo_type: str,
    namespace: str,
    name: str,
    payload: UpdateRepoSettingsPayload,
    user: User = Depends(get_current_user),
):
    """Update repository settings.

    Matches pattern: PUT /api/{repo_type}s/{namespace}/{name}/settings

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        payload: Settings to update
        user: Current authenticated user

    Returns:
        Success message
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check if user has permission to update this repository
    check_repo_delete_permission(repo_row, user)

    # Update fields if provided
    update_fields = {}

    # Validate and update LFS settings
    if payload.lfs_threshold_bytes is not None:
        # Validate threshold (minimum 1MB = 1,000,000 bytes)
        if payload.lfs_threshold_bytes < 1000000:
            raise HTTPException(
                400,
                detail={
                    "error": f"LFS threshold must be at least 1 MB (1,000,000 bytes), got {payload.lfs_threshold_bytes}"
                },
            )
        update_fields["lfs_threshold_bytes"] = payload.lfs_threshold_bytes

    if payload.lfs_keep_versions is not None:
        # Validate keep_versions (minimum 2)
        if payload.lfs_keep_versions < 2:
            raise HTTPException(
                400,
                detail={
                    "error": f"LFS keep_versions must be at least 2, got {payload.lfs_keep_versions}"
                },
            )
        update_fields["lfs_keep_versions"] = payload.lfs_keep_versions

    if payload.lfs_suffix_rules is not None:
        # Validate suffix rules (must start with '.')
        for suffix in payload.lfs_suffix_rules:
            if not suffix.startswith("."):
                raise HTTPException(
                    400,
                    detail={
                        "error": f"LFS suffix rules must start with '.', got: {suffix}"
                    },
                )
        # Store as JSON string
        update_fields["lfs_suffix_rules"] = json.dumps(payload.lfs_suffix_rules)

    requested_private = payload.private
    if requested_private is None and payload.visibility is not None:
        if payload.visibility == "private":
            requested_private = True
        elif payload.visibility == "public":
            requested_private = False
        else:
            raise HTTPException(
                400,
                detail={
                    "error": (
                        "Unsupported repository visibility. "
                        "Only 'public' and 'private' are supported."
                    )
                },
            )

    if requested_private is not None:
        # Check if visibility is actually changing
        if repo_row.private != requested_private:
            # Calculate repository storage
            logger.info(
                f"Checking quota for visibility change: {repo_id} from "
                f"{'private to public' if repo_row.private else 'public to private'}"
            )

            repo_storage = await calculate_repository_storage(repo_row)
            repo_size = repo_storage["total_bytes"]

            # Check if namespace is an organization
            org = get_organization(namespace)
            is_org = org is not None

            # Check quota for the new visibility setting
            # If changing to private, check private quota; if to public, check public quota
            allowed, error_msg = check_quota(
                namespace=namespace,
                additional_bytes=repo_size,
                is_private=requested_private,
                is_org=is_org,
            )

            if not allowed:
                logger.warning(
                    f"Quota check failed for {repo_id} visibility change: {error_msg}"
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": error_msg,
                        "repo_size_bytes": repo_size,
                    },
                )

        # Update repository visibility
        update_fields["private"] = requested_private

    # Apply all updates if there are any
    if update_fields:
        update_repository(repo_row, **update_fields)

    # Note: gated functionality not yet implemented in database schema
    # Would require adding a 'gated' field to Repository model

    return {"success": True, "message": "Repository settings updated successfully"}


@router.get("/{repo_type}s/{namespace}/{name}/settings/lfs")
async def get_repo_lfs_settings(
    repo_type: str,
    namespace: str,
    name: str,
    user: User = Depends(get_current_user),
):
    """Get repository LFS settings (configured + effective values).

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        user: Current authenticated user

    Returns:
        LFS settings with both configured and effective values
    """
    repo_id = f"{namespace}/{name}"

    # Check if repository exists
    repo_row = get_repository(repo_type, namespace, name)

    if not repo_row:
        return hf_repo_not_found(repo_id, repo_type)

    # Check if user has permission to view settings
    check_repo_delete_permission(repo_row, user)

    # Get effective values from helper functions
    from kohakuhub.db_operations import (
        get_effective_lfs_keep_versions,
        get_effective_lfs_suffix_rules,
        get_effective_lfs_threshold,
    )

    effective_threshold = get_effective_lfs_threshold(repo_row)
    effective_keep_versions = get_effective_lfs_keep_versions(repo_row)
    effective_suffix_rules = get_effective_lfs_suffix_rules(repo_row)

    # Parse suffix rules from JSON if exists
    configured_suffix_rules = None
    if repo_row.lfs_suffix_rules:
        try:
            configured_suffix_rules = json.loads(repo_row.lfs_suffix_rules)
        except json.JSONDecodeError:
            pass

    return {
        # Configured values (NULL = using server default)
        "lfs_threshold_bytes": repo_row.lfs_threshold_bytes,
        "lfs_keep_versions": repo_row.lfs_keep_versions,
        "lfs_suffix_rules": configured_suffix_rules,
        # Effective values (resolved with server defaults)
        "lfs_threshold_bytes_effective": effective_threshold,
        "lfs_threshold_bytes_source": (
            "repository"
            if repo_row.lfs_threshold_bytes is not None
            else "server_default"
        ),
        "lfs_keep_versions_effective": effective_keep_versions,
        "lfs_keep_versions_source": (
            "repository" if repo_row.lfs_keep_versions is not None else "server_default"
        ),
        "lfs_suffix_rules_effective": effective_suffix_rules,
        "lfs_suffix_rules_source": (
            "merged" if repo_row.lfs_suffix_rules is not None else "server_default"
        ),
        # Server defaults for reference
        "server_defaults": {
            "lfs_threshold_bytes": cfg.app.lfs_threshold_bytes,
            "lfs_keep_versions": cfg.app.lfs_keep_versions,
            "lfs_suffix_rules_default": cfg.app.lfs_suffix_rules_default,
        },
    }
