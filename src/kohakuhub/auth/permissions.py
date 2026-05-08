"""Authorization and permission checking utilities."""

from typing import Optional

from fastapi import HTTPException

from kohakuhub.db import Repository, User
from kohakuhub.constants import ERROR_USER_AUTH_REQUIRED
from kohakuhub.db_operations import get_organization, get_user_organization


class RepoReadDeniedError(Exception):
    """Raised when a caller cannot read a repository.

    Used by ``check_repo_read_permission`` for both anonymous-on-private
    and authed-no-access cases. Inheriting from ``Exception`` (not
    ``HTTPException``) keeps the fallback decorator's ``except
    HTTPException`` from absorbing it — but on its own that is **not
    sufficient** to preserve propagation, because ``with_repo_fallback``
    also has a generic ``except Exception`` block that converts
    LakeFS / DB / timeout failures into a 500. Propagation past the
    decorator depends on the *explicit* ``except RepoReadDeniedError:
    raise`` clause sitting between the cancellation handler and the
    generic catch (``api/fallback/decorators.py``). Both pieces — the
    inheritance choice **and** the decorator's explicit re-raise — are
    load-bearing. Skipping the chain probe is the intended outcome:
    we don't want to leak the existence of a masked private local repo
    by emitting upstream traffic for it.

    A global FastAPI exception handler (registered in ``main.py``)
    converts this exception into ``404 + X-Error-Code: RepoNotFound``,
    aligning hkub's wire shape with HuggingFace's authed-style answer
    for "repo doesn't exist (to you)" — which ``huggingface_hub.utils
    ._http.hf_raise_for_status`` dispatches to ``RepositoryNotFoundError``.

    Picking this over the anonymous 401-anti-enum shape (issue #76
    Option A): the **wire shape** is identical for anonymous-on-private
    and authed-no-access, simpler than mirroring HF's anon-vs-authed
    branch, and matches what HF returns to authenticated callers. Note
    that "wire-shape identical" is **not the same as** "fully
    indistinguishable": the authed-no-access path executes additional
    DB lookups (``get_organization``, ``get_user_organization``) that
    the anon path doesn't, leaving a sub-millisecond timing-side-channel
    that could in principle be exploited at scale. Treating that as
    out-of-scope for now — HF's own backend has the equivalent
    asymmetry, and a constant-time path would mean adding 2 always-
    needless DB hits to every anon read. Document the gap honestly
    rather than overclaim equivalence.
    """

    def __init__(self, repo: Repository):
        self.repo = repo
        self.repo_id = repo.full_id
        self.repo_type = repo.repo_type
        super().__init__(
            f"Read access denied to {self.repo_type} '{self.repo_id}'"
        )


def check_namespace_permission(
    namespace: str,
    user: Optional[User],
    require_admin: bool = False,
    is_admin: bool = False,
) -> bool:
    """Check if user has permission to use a namespace.

    Args:
        namespace: The namespace (username or org name)
        user: The authenticated user (None if admin token used)
        require_admin: If True, require admin/super-admin role for orgs
        is_admin: If True, bypass all checks (admin token authenticated)

    Returns:
        True if user has permission

    Raises:
        HTTPException: If user doesn't have permission
    """
    # Admin bypass
    if is_admin:
        return True

    if not user:
        raise HTTPException(403, detail=ERROR_USER_AUTH_REQUIRED)

    # User's own namespace
    if namespace == user.username:
        return True

    # Check if it's an organization
    org = get_organization(namespace)
    if not org:
        raise HTTPException(
            403,
            detail=f"Namespace '{namespace}' does not exist or you don't have access",
        )

    # Check user's membership in the organization
    membership = get_user_organization(user, org)

    if not membership:
        raise HTTPException(
            403, detail=f"You are not a member of organization '{namespace}'"
        )

    # If admin required, check role
    if require_admin and membership.role not in ["admin", "super-admin"]:
        raise HTTPException(
            403, detail=f"You need admin privileges in organization '{namespace}'"
        )

    return True


def check_repo_read_permission(
    repo: Repository, user: Optional[User] = None, is_admin: bool = False
) -> bool:
    """Check if user can read a repository.

    Public repos: anyone can read
    Private repos: only creator or org members can read

    Args:
        repo: The repository to check
        user: The authenticated user (optional for public repos)
        is_admin: If True, bypass all checks (admin token authenticated)

    Returns:
        True if user has permission

    Raises:
        HTTPException: If user doesn't have permission
    """
    # Admin bypass
    if is_admin:
        return True

    # Public repos are accessible to everyone
    if not repo.private:
        return True

    # Private repos: anonymous-on-private and authed-no-access both
    # collapse to ``RepoReadDeniedError`` so the wire shape is identical
    # for both callers (privacy-preserving Option A from #76 — hides the
    # private-repo enumeration leak that distinct 401-vs-403 responses
    # would otherwise expose, and maps cleanly through hf_raise_for_status
    # to ``RepositoryNotFoundError`` on the client).
    if not user:
        raise RepoReadDeniedError(repo)

    # Check if user is the creator (namespace matches username)
    if repo.namespace == user.username:
        return True

    # Check if namespace is an organization and user is a member
    org = get_organization(repo.namespace)
    if org:
        membership = get_user_organization(user, org)
        if membership:
            return True

    raise RepoReadDeniedError(repo)


def check_repo_write_permission(
    repo: Repository, user: Optional[User], is_admin: bool = False
) -> bool:
    """Check if user can modify a repository.

    Users can modify:
    - Their own repos
    - Repos in orgs where they are member/admin/super-admin

    Args:
        repo: The repository to check
        user: The authenticated user (None if admin token used)
        is_admin: If True, bypass all checks (admin token authenticated)

    Returns:
        True if user has permission

    Raises:
        HTTPException: If user doesn't have permission
    """
    # Admin bypass
    if is_admin:
        return True

    if not user:
        raise HTTPException(403, detail=ERROR_USER_AUTH_REQUIRED)

    # Check if user owns the repo (namespace matches username)
    if repo.namespace == user.username:
        return True

    # Check if namespace is an organization and user is a member
    org = get_organization(repo.namespace)
    if org:
        membership = get_user_organization(user, org)
        if membership:
            # Any member can write (visitor role can also read but not write)
            if membership.role in ["member", "admin", "super-admin"]:
                return True

    raise HTTPException(
        403, detail=f"You don't have permission to modify repository '{repo.full_id}'"
    )


def check_repo_delete_permission(
    repo: Repository, user: Optional[User], is_admin: bool = False
) -> bool:
    """Check if user can delete a repository.

    Users can delete:
    - Their own repos
    - Repos in orgs where they are admin/super-admin

    Args:
        repo: The repository to check
        user: The authenticated user (None if admin token used)
        is_admin: If True, bypass all checks (admin token authenticated)

    Returns:
        True if user has permission

    Raises:
        HTTPException: If user doesn't have permission
    """
    # Admin bypass
    if is_admin:
        return True

    if not user:
        raise HTTPException(403, detail=ERROR_USER_AUTH_REQUIRED)

    # Check if user owns the repo (namespace matches username)
    if repo.namespace == user.username:
        return True

    # Check if namespace is an organization and user is admin
    org = get_organization(repo.namespace)
    if org:
        membership = get_user_organization(user, org)
        if membership and membership.role in ["admin", "super-admin"]:
            return True

    raise HTTPException(
        403, detail=f"You don't have permission to delete repository '{repo.full_id}'"
    )
