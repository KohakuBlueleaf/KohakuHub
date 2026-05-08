"""Organization related API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request
from peewee import fn
from pydantic import BaseModel

from kohakuhub.db import Repository, User, UserOrganization, db
from kohakuhub.constants import ERROR_USER_NOT_FOUND
from kohakuhub.db_operations import (
    create_organization,
    create_user_organization,
    delete_user_organization,
    get_organization,
    get_user_by_username,
    get_user_organization,
    list_organization_members,
    list_user_organizations,
    update_user_organization,
)
from kohakuhub.logger import get_logger
from kohakuhub.api.fallback import with_user_fallback
from kohakuhub.api.validation import RESERVED_NAMES
from kohakuhub.auth.dependencies import get_current_user
from kohakuhub.utils.names import normalize_name

# Error messages
_ERR_ORG_NOT_FOUND = "Organization not found"

logger = get_logger("ORG")

router = APIRouter()


class CreateOrganizationPayload(BaseModel):
    name: str
    description: str | None = None


@router.post("/create")
async def create_organization_endpoint(
    payload: CreateOrganizationPayload, user: User = Depends(get_current_user)
):
    """Create a new organization with default quotas."""

    # Check if organization name is reserved
    if (
        payload.name.lower() in RESERVED_NAMES
        or normalize_name(payload.name) in RESERVED_NAMES
    ):
        logger.warning(
            f"Organization creation failed: name '{payload.name}' is reserved"
        )
        raise HTTPException(
            400,
            detail=f"Organization name '{payload.name}' is reserved and cannot be used",
        )

    # Check if organization already exists and create atomically
    with db.atomic():
        existing_org = get_organization(payload.name)
        if existing_org:
            raise HTTPException(400, detail="Organization name already exists")

        # Create organization with default quotas
        org = create_organization(payload.name, payload.description)

        # Add creator as super-admin (using ForeignKey objects)
        create_user_organization(user, org, "super-admin")

    logger.info(f"User {user.username} created organization: {org.username}")

    return {"success": True, "name": org.username}


def _count_repos_by_type(namespace: str) -> dict[str, int]:
    """Return repo counts in ``namespace`` grouped by type.

    Single ``GROUP BY repo_type`` SQL — replaces the frontend pattern of
    listing 1000 rows × 3 types per org card just to read ``.length``.
    See issue #63.
    """
    counts: dict[str, int] = {"model": 0, "dataset": 0, "space": 0}
    for row in (
        Repository.select(
            Repository.repo_type, fn.COUNT(Repository.id).alias("c")
        )
        .where(Repository.namespace == namespace)
        .group_by(Repository.repo_type)
        .dicts()
    ):
        counts[row["repo_type"]] = row["c"]
    counts["total"] = counts["model"] + counts["dataset"] + counts["space"]
    return counts


@router.get("/{org_name}")
@with_user_fallback("profile")
async def get_organization_info(org_name: str, request: Request, fallback: bool = True):
    """Get organization details.

    Includes a ``repo_count`` field grouped by repo type so the orgs grid
    page can render the per-card "X repos" badge without fan-out listing
    (see issue #63). The count covers all repos in the namespace
    (public + private); the orgs grid card is only rendered for orgs the
    viewer is a member of, so private repos counting is fine. Public-only
    surfaces should derive their own count via the privacy-filtered list
    endpoints.

    Query params:
        fallback: Set to "false" to disable fallback to external sources (default: true)
    """
    org = get_organization(org_name)
    if not org:
        raise HTTPException(404, detail=_ERR_ORG_NOT_FOUND)
    return {
        "name": org.username,
        "description": org.description,
        "created_at": org.created_at,
        "repo_count": _count_repos_by_type(org_name),
        "_source": "local",  # Tag local orgs
    }


class AddMemberPayload(BaseModel):
    username: str
    role: str


@router.post("/{org_name}/members")
async def add_member(
    org_name: str,
    payload: AddMemberPayload,
    current_user: User = Depends(get_current_user),
):
    """Add a member to an organization."""
    org = get_organization(org_name)
    if not org:
        raise HTTPException(404, detail=_ERR_ORG_NOT_FOUND)

    # Check if the current user is an admin of the organization
    user_org = get_user_organization(current_user, org)
    if not user_org or user_org.role not in ["admin", "super-admin"]:
        raise HTTPException(403, detail="Not authorized to add members")

    # Get the user to add
    user_to_add = get_user_by_username(payload.username)
    if not user_to_add:
        raise HTTPException(404, detail=ERROR_USER_NOT_FOUND)

    # Check if already a member
    existing = get_user_organization(user_to_add, org)
    if existing:
        raise HTTPException(400, detail="User is already a member of the organization")

    # Add member using ForeignKey objects
    create_user_organization(user_to_add, org, payload.role)
    return {"success": True, "message": "Member added successfully"}


@router.delete("/{org_name}/members/{username}")
async def remove_member_endpoint(
    org_name: str,
    username: str,
    current_user: User = Depends(get_current_user),
):
    """Remove a member from an organization."""
    org = get_organization(org_name)
    if not org:
        raise HTTPException(404, detail=_ERR_ORG_NOT_FOUND)

    # Check if the current user is an admin of the organization
    user_org = get_user_organization(current_user, org)
    if not user_org or user_org.role not in ["admin", "super-admin"]:
        raise HTTPException(403, detail="Not authorized to remove members")

    # Get the user to remove
    user_to_remove = get_user_by_username(username)
    if not user_to_remove:
        raise HTTPException(404, detail=ERROR_USER_NOT_FOUND)

    # Get the membership
    membership = get_user_organization(user_to_remove, org)
    if not membership:
        raise HTTPException(404, detail="User is not a member of the organization")

    # Remove member using object operation
    delete_user_organization(membership)
    return {"success": True, "message": "Member removed successfully"}


@router.get("/users/{username}/orgs")
async def list_user_organizations_endpoint(username: str):
    """List organizations a user belongs to."""
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(404, detail=ERROR_USER_NOT_FOUND)

    # Use backref to get user's org memberships
    orgs = list_user_organizations(user)
    return {
        "organizations": [
            {
                "name": org.organization.username,
                "description": org.organization.description,
                "role": org.role,
            }
            for org in orgs
        ]
    }


class UpdateMemberRolePayload(BaseModel):
    role: str


@router.put("/{org_name}/members/{username}")
async def update_member_role_endpoint(
    org_name: str,
    username: str,
    payload: UpdateMemberRolePayload,
    current_user: User = Depends(get_current_user),
):
    """Update a member's role in an organization."""
    org = get_organization(org_name)
    if not org:
        raise HTTPException(404, detail=_ERR_ORG_NOT_FOUND)

    # Check if the current user is an admin of the organization
    user_org = get_user_organization(current_user, org)
    if not user_org or user_org.role not in ["admin", "super-admin"]:
        raise HTTPException(403, detail="Not authorized to update member roles")

    # Get the user to update
    user_to_update = get_user_by_username(username)
    if not user_to_update:
        raise HTTPException(404, detail=ERROR_USER_NOT_FOUND)

    # Get the membership
    membership = get_user_organization(user_to_update, org)
    if not membership:
        raise HTTPException(404, detail="User is not a member of the organization")

    # Update role using object operation
    update_user_organization(membership, role=payload.role)
    return {"success": True, "message": "Member role updated successfully"}


@router.get("/{org_name}/members")
async def list_organization_members_endpoint(org_name: str):
    """List organization members."""
    org = get_organization(org_name)
    if not org:
        raise HTTPException(404, detail=_ERR_ORG_NOT_FOUND)

    # Get all members using backref
    members = list_organization_members(org)

    return {
        "members": [
            {
                "user": m.user.username,
                "role": m.role,
            }
            for m in members
        ]
    }
