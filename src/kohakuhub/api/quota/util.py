"""Storage quota utilities for KohakuHub with separate private/public quotas.

This module provides functions to calculate, track, and enforce storage quotas
for users and organizations with separate tracking for private and public repositories.
"""

import asyncio

from peewee import fn

from kohakuhub.config import cfg
from kohakuhub.db import File, LFSObjectHistory, Repository, User
from kohakuhub.db_operations import get_organization
from kohakuhub.logger import get_logger
from kohakuhub.utils.lakefs import get_lakefs_client, lakefs_repo_name

logger = get_logger("QUOTA")


async def calculate_repository_storage(repo: Repository) -> dict[str, int]:
    """Calculate total storage usage for a repository.

    Storage calculation:
    - Non-LFS files: Counted from current branch
    - LFS files: Counted from ALL history (all versions, including deleted)

    This ensures:
    - Deleting LFS files doesn't decrease quota (LFS cache preserved)
    - No double counting of LFS files

    Args:
        repo: Repository model instance

    Returns:
        Dict with keys:
        - total_bytes: Total storage used (non-LFS current + all LFS history)
        - current_branch_bytes: Storage in current branch (all files)
        - current_branch_non_lfs_bytes: Non-LFS files in current branch
        - lfs_total_bytes: Total LFS storage (all versions)
        - lfs_unique_bytes: Unique LFS storage (deduplicated by SHA256)
    """
    lakefs_repo = lakefs_repo_name(repo.repo_type, repo.full_id)
    client = get_lakefs_client()

    # Bulk-load LFS flag for every active file in this repo *once* up front,
    # so the per-object loop below is O(1) lookup instead of O(N) DB queries.
    # The (repository, path_in_repo) composite index makes this scan cheap
    # even for repos with tens of thousands of files.
    file_is_lfs: dict[str, bool] = {
        path: lfs
        for path, lfs in File.select(File.path_in_repo, File.lfs)
        .where((File.repository == repo) & (File.is_deleted == False))
        .tuples()
        .iterator()
    }

    # Calculate current branch storage (all files)
    current_branch_bytes = 0
    current_branch_lfs_bytes = 0

    try:
        # List all objects in main branch
        after = ""
        has_more = True

        while has_more:
            result = await client.list_objects(
                repository=lakefs_repo,
                ref="main",
                delimiter="",  # Recursive
                amount=1000,
                after=after,
            )

            for obj in result["results"]:
                if obj["path_type"] == "object":
                    size = obj.get("size_bytes") or 0
                    current_branch_bytes += size

                    # Look up LFS flag from the bulk-loaded dict.
                    # Missing path or non-LFS file → counted as non-LFS, matching
                    # the previous semantics (File.get_or_none returning None or
                    # a row with lfs=False both yielded "non-LFS").
                    if file_is_lfs.get(obj.get("path"), False):
                        current_branch_lfs_bytes += size

            if result.get("pagination") and result["pagination"].get("has_more"):
                after = result["pagination"]["next_offset"]
                has_more = True
            else:
                has_more = False

    except Exception as e:
        logger.warning(
            f"Failed to calculate current branch storage for {repo.full_id}: {e}"
        )

    # Calculate non-LFS storage in current branch
    current_branch_non_lfs_bytes = current_branch_bytes - current_branch_lfs_bytes

    # Calculate LFS storage from history (all versions, including deleted).
    # SQL aggregation avoids pulling every history row into Python.
    lfs_total_bytes = (
        LFSObjectHistory.select(fn.COALESCE(fn.SUM(LFSObjectHistory.size), 0))
        .where(LFSObjectHistory.repository == repo)
        .scalar()
        or 0
    )

    # Unique LFS storage: SUM over distinct (sha256, size) pairs for this repo.
    # Built as a subquery so the SUM happens server-side instead of pulling
    # every distinct row into Python.
    unique_subquery = (
        LFSObjectHistory.select(LFSObjectHistory.sha256, LFSObjectHistory.size)
        .where(LFSObjectHistory.repository == repo)
        .distinct()
        .alias("u")
    )
    lfs_unique_bytes = (
        LFSObjectHistory.select(fn.COALESCE(fn.SUM(unique_subquery.c.size), 0))
        .from_(unique_subquery)
        .scalar()
        or 0
    )

    # Total storage = non-LFS in current branch + unique LFS storage (deduplicated)
    # Using lfs_unique_bytes ensures global deduplication works correctly for quota
    # This avoids counting the same SHA256 object multiple times across versions
    total_bytes = current_branch_non_lfs_bytes + lfs_unique_bytes

    return {
        "total_bytes": total_bytes,
        "current_branch_bytes": current_branch_bytes,
        "current_branch_non_lfs_bytes": current_branch_non_lfs_bytes,
        "lfs_total_bytes": lfs_total_bytes,
        "lfs_unique_bytes": lfs_unique_bytes,
    }


async def calculate_namespace_storage(
    namespace: str, is_org: bool = False
) -> dict[str, int]:
    """Calculate total storage usage for a user or organization by privacy.

    Args:
        namespace: Username or organization name
        is_org: True if namespace is an organization

    Returns:
        Dict with keys:
        - private_bytes: Total private repository storage
        - public_bytes: Total public repository storage
        - total_bytes: Total storage
    """

    # Get all repositories in this namespace
    repos = list(Repository.select().where(Repository.namespace == namespace))

    # Separate repos by privacy
    private_repos = [r for r in repos if r.private]
    public_repos = [r for r in repos if not r.private]

    # Calculate storage for all repos in parallel
    private_bytes = 0
    public_bytes = 0

    if private_repos:
        stats_list = await asyncio.gather(
            *[calculate_repository_storage(repo) for repo in private_repos]
        )
        private_bytes = sum(stats["total_bytes"] for stats in stats_list)

    if public_repos:
        stats_list = await asyncio.gather(
            *[calculate_repository_storage(repo) for repo in public_repos]
        )
        public_bytes = sum(stats["total_bytes"] for stats in stats_list)

    total_bytes = private_bytes + public_bytes

    logger.info(
        f"Calculated storage for {'org' if is_org else 'user'} {namespace}: "
        f"private={private_bytes:,} bytes, public={public_bytes:,} bytes, total={total_bytes:,} bytes"
    )

    return {
        "private_bytes": private_bytes,
        "public_bytes": public_bytes,
        "total_bytes": total_bytes,
    }


async def update_namespace_storage(
    namespace: str, is_org: bool = False
) -> dict[str, int]:
    """Recalculate and update storage usage for a user or organization.

    Args:
        namespace: Username or organization name
        is_org: True if namespace is an organization

    Returns:
        Dict with updated storage usage (private_bytes, public_bytes, total_bytes)
    """
    storage = await calculate_namespace_storage(namespace, is_org)

    # Update database - organizations are now users with is_org=True
    if is_org:
        entity = get_organization(namespace)
    else:
        entity = User.get(User.username == namespace)

    if entity:
        entity.private_used_bytes = storage["private_bytes"]
        entity.public_used_bytes = storage["public_bytes"]
        entity.save()

    return storage


def check_quota(
    namespace: str, additional_bytes: int, is_private: bool, is_org: bool = False
) -> tuple[bool, str | None]:
    """Check if adding additional storage would exceed quota (SYNCHRONOUS).

    Args:
        namespace: Username or organization name
        additional_bytes: Bytes to be added
        is_private: True if uploading to a private repository
        is_org: True if namespace is an organization

    Returns:
        Tuple of (allowed: bool, error_message: str | None)
    """

    # Get current quota and usage - organizations are now users with is_org=True
    if is_org:
        entity = get_organization(namespace)
    else:
        entity = User.get_or_none(User.username == namespace)

    if not entity:
        return False, f"{'Organization' if is_org else 'User'} not found: {namespace}"

    private_quota = entity.private_quota_bytes
    public_quota = entity.public_quota_bytes
    private_used = entity.private_used_bytes
    public_used = entity.public_used_bytes

    # Check the appropriate quota based on repo privacy
    if is_private:
        quota_bytes = private_quota
        used_bytes = private_used
        quota_type = "private"
    else:
        quota_bytes = public_quota
        used_bytes = public_used
        quota_type = "public"

    # NULL quota = unlimited
    if quota_bytes is None:
        return True, None

    # Check if would exceed quota
    new_usage = used_bytes + additional_bytes

    if new_usage > quota_bytes:
        quota_gb = quota_bytes / (1000**3)
        new_usage_gb = new_usage / (1000**3)
        return (
            False,
            f"{quota_type.capitalize()} storage quota exceeded: {new_usage_gb:.2f}GB would exceed limit of {quota_gb:.2f}GB",
        )

    return True, None


def increment_storage(
    namespace: str, bytes_delta: int, is_private: bool, is_org: bool = False
) -> tuple[int, int]:
    """Increment storage usage for a user or organization (SYNCHRONOUS).

    Args:
        namespace: Username or organization name
        bytes_delta: Bytes to add (can be negative for deletions)
        is_private: True if this is for a private repository
        is_org: True if namespace is an organization

    Returns:
        Tuple of (private_used_bytes, public_used_bytes)
    """

    # Organizations are now users with is_org=True
    if is_org:
        entity = get_organization(namespace)
    else:
        entity = User.get(User.username == namespace)

    if is_private:
        entity.private_used_bytes = max(0, entity.private_used_bytes + bytes_delta)
    else:
        entity.public_used_bytes = max(0, entity.public_used_bytes + bytes_delta)
    entity.save()

    private_used, public_used = entity.private_used_bytes, entity.public_used_bytes

    logger.debug(
        f"Updated storage for {'org' if is_org else 'user'} {namespace}: "
        f"{'private' if is_private else 'public'} {bytes_delta:+,} bytes "
        f"(totals: private={private_used:,}, public={public_used:,})"
    )

    return private_used, public_used


def get_storage_info(
    namespace: str, is_org: bool = False
) -> dict[str, int | float | None]:
    """Get storage quota and usage information (SYNCHRONOUS).

    Args:
        namespace: Username or organization name
        is_org: True if namespace is an organization

    Returns:
        Dict with quota information for both private and public storage
    """

    # Organizations are now users with is_org=True
    if is_org:
        entity = get_organization(namespace)
    else:
        entity = User.get_or_none(User.username == namespace)

    if not entity:
        private_quota, public_quota, private_used, public_used = None, None, 0, 0
    else:
        private_quota = entity.private_quota_bytes
        public_quota = entity.public_quota_bytes
        private_used = entity.private_used_bytes
        public_used = entity.public_used_bytes

    # Calculate availability and percentages
    private_available = (
        None if private_quota is None else max(0, private_quota - private_used)
    )
    public_available = (
        None if public_quota is None else max(0, public_quota - public_used)
    )

    private_percentage = (
        None
        if private_quota is None or private_quota == 0
        else (private_used / private_quota * 100)
    )
    public_percentage = (
        None
        if public_quota is None or public_quota == 0
        else (public_used / public_quota * 100)
    )

    total_used = private_used + public_used

    return {
        "private_quota_bytes": private_quota,
        "public_quota_bytes": public_quota,
        "private_used_bytes": private_used,
        "public_used_bytes": public_used,
        "private_available_bytes": private_available,
        "public_available_bytes": public_available,
        "private_percentage_used": private_percentage,
        "public_percentage_used": public_percentage,
        "total_used_bytes": total_used,
    }


def set_quota(
    namespace: str,
    private_quota_bytes: int | None = None,
    public_quota_bytes: int | None = None,
    is_org: bool = False,
) -> dict[str, int | float | None]:
    """Set storage quota for a user or organization (SYNCHRONOUS).

    Args:
        namespace: Username or organization name
        private_quota_bytes: Private repo storage quota (None = unlimited, 0 = no change)
        public_quota_bytes: Public repo storage quota (None = unlimited, 0 = no change)
        is_org: True if namespace is an organization

    Returns:
        Updated storage info dict
    """

    # Organizations are now users with is_org=True
    if is_org:
        entity = get_organization(namespace)
    else:
        entity = User.get(User.username == namespace)

    if private_quota_bytes is not None:
        entity.private_quota_bytes = private_quota_bytes
    if public_quota_bytes is not None:
        entity.public_quota_bytes = public_quota_bytes
    entity.save()

    logger.info(
        f"Set quota for {'org' if is_org else 'user'} {namespace}: "
        f"private={private_quota_bytes}, public={public_quota_bytes}"
    )

    return get_storage_info(namespace, is_org)


# ============================================================================
# Repository-specific quota management
# ============================================================================


def get_repo_storage_info(repo: Repository) -> dict[str, int | float | None]:
    """Get storage quota and usage information for a specific repository.

    Args:
        repo: Repository model instance

    Returns:
        Dict with quota information including namespace context
    """
    # Get namespace quota info for context - use owner ForeignKey
    entity = repo.owner  # Direct ForeignKey access

    if entity:
        namespace_quota = (
            entity.private_quota_bytes if repo.private else entity.public_quota_bytes
        )
        namespace_used = (
            entity.private_used_bytes if repo.private else entity.public_used_bytes
        )
    else:
        namespace_quota = None
        namespace_used = 0

    # Calculate namespace available quota
    namespace_available = (
        None if namespace_quota is None else max(0, namespace_quota - namespace_used)
    )

    # Repository quota and usage
    repo_quota = repo.quota_bytes
    repo_used = repo.used_bytes

    # Calculate effective quota (repo quota or namespace quota)
    effective_quota = repo_quota if repo_quota is not None else namespace_quota

    # Calculate availability
    available = None if effective_quota is None else max(0, effective_quota - repo_used)

    # Calculate percentage
    percentage = (
        None
        if effective_quota is None or effective_quota == 0
        else (repo_used / effective_quota * 100)
    )

    return {
        # Repository-specific
        "quota_bytes": repo_quota,
        "used_bytes": repo_used,
        "available_bytes": available,
        "percentage_used": percentage,
        # Effective quota (what's actually enforced)
        "effective_quota_bytes": effective_quota,
        # Namespace context
        "namespace_quota_bytes": namespace_quota,
        "namespace_used_bytes": namespace_used,
        "namespace_available_bytes": namespace_available,
        "is_inheriting": repo_quota is None,
    }


def set_repo_quota(
    repo: Repository, quota_bytes: int | None
) -> dict[str, int | float | None]:
    """Set storage quota for a repository with validation.

    Args:
        repo: Repository model instance
        quota_bytes: Quota in bytes (None = inherit from namespace)

    Returns:
        Updated storage info dict

    Raises:
        ValueError: If quota exceeds namespace available quota
    """
    # If setting a specific quota (not NULL), validate against namespace
    if quota_bytes is not None:
        # Get namespace available quota - use owner ForeignKey
        entity = repo.owner  # Direct ForeignKey access

        if entity:
            namespace_quota = (
                entity.private_quota_bytes
                if repo.private
                else entity.public_quota_bytes
            )
            namespace_used = (
                entity.private_used_bytes if repo.private else entity.public_used_bytes
            )
        else:
            namespace_quota = None
            namespace_used = 0

        # Validate: repository quota cannot exceed namespace available
        if namespace_quota is not None:
            namespace_available = max(0, namespace_quota - namespace_used)

            # Add back current repo quota if it was set (we're replacing it)
            if repo.quota_bytes is not None:
                namespace_available += repo.quota_bytes

            if quota_bytes > namespace_available:
                raise ValueError(
                    f"Repository quota ({quota_bytes / (1000**3):.2f}GB) exceeds "
                    f"namespace available quota ({namespace_available / (1000**3):.2f}GB)"
                )

    # Update repository quota
    repo.quota_bytes = quota_bytes
    repo.save()

    logger.info(f"Set quota for repository {repo.full_id}: quota={quota_bytes} bytes")

    return get_repo_storage_info(repo)


async def update_repository_storage(repo: Repository) -> dict[str, int]:
    """Recalculate and update storage usage for a repository.

    Args:
        repo: Repository model instance

    Returns:
        Dict with updated storage usage
    """
    storage = await calculate_repository_storage(repo)
    total_bytes = storage["total_bytes"]

    # Update repository used_bytes
    repo.used_bytes = total_bytes
    repo.save()

    logger.info(f"Updated storage for repository {repo.full_id}: {total_bytes:,} bytes")

    return {"used_bytes": total_bytes, "total_bytes": total_bytes}
