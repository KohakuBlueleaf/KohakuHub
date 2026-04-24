"""System statistics endpoints for admin API."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from peewee import fn

from kohakuhub.db import Commit, File, LFSObjectHistory, Repository, User
from kohakuhub.logger import get_logger
from kohakuhub.api.admin.utils import verify_admin_token

logger = get_logger("ADMIN")
router = APIRouter()


@router.get("/stats")
async def get_system_stats(
    _admin: bool = Depends(verify_admin_token),
):
    """Get system statistics.

    Args:
        _admin: Admin authentication (dependency)

    Returns:
        System statistics
    """

    user_count = User.select().where(User.is_org == False).count()
    org_count = User.select().where(User.is_org == True).count()
    repo_count = Repository.select().count()
    private_repo_count = Repository.select().where(Repository.private == True).count()
    public_repo_count = Repository.select().where(Repository.private == False).count()

    return {
        "users": user_count,
        "organizations": org_count,
        "repositories": {
            "total": repo_count,
            "private": private_repo_count,
            "public": public_repo_count,
        },
    }


@router.get("/stats/detailed")
async def get_detailed_stats(
    _admin: bool = Depends(verify_admin_token),
):
    """Get detailed system statistics.

    Args:
        _admin: Admin authentication (dependency)

    Returns:
        Detailed statistics from database
    """

    # User stats
    total_users = User.select().where(User.is_org == False).count()
    active_users = (
        User.select().where((User.is_active == True) & (User.is_org == False)).count()
    )
    verified_users = (
        User.select()
        .where((User.email_verified == True) & (User.is_org == False))
        .count()
    )

    # Organization stats
    total_orgs = User.select().where(User.is_org == True).count()

    # Repository stats
    total_repos = Repository.select().count()
    private_repos = Repository.select().where(Repository.private == True).count()
    public_repos = Repository.select().where(Repository.private == False).count()

    # Repos by type
    model_repos = Repository.select().where(Repository.repo_type == "model").count()
    dataset_repos = Repository.select().where(Repository.repo_type == "dataset").count()
    space_repos = Repository.select().where(Repository.repo_type == "space").count()

    # Commit stats
    total_commits = Commit.select().count()

    # Top contributors
    top_contributors = (
        Commit.select(Commit.username, fn.COUNT(Commit.id).alias("commit_count"))
        .group_by(Commit.username)
        .order_by(fn.COUNT(Commit.id).desc())
        .limit(10)
    )

    contributors = [
        {"username": c.username, "commit_count": c.commit_count}
        for c in top_contributors
    ]

    # LFS object stats
    total_lfs_objects = LFSObjectHistory.select().count()
    total_lfs_size = (
        LFSObjectHistory.select(fn.SUM(LFSObjectHistory.size).alias("total")).scalar()
        or 0
    )

    # Storage stats (only count regular users, not orgs)
    total_private_used = (
        User.select(fn.SUM(User.private_used_bytes).alias("total"))
        .where(User.is_org == False)
        .scalar()
        or 0
    )
    total_public_used = (
        User.select(fn.SUM(User.public_used_bytes).alias("total"))
        .where(User.is_org == False)
        .scalar()
        or 0
    )

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "verified": verified_users,
            "inactive": total_users - active_users,
        },
        "organizations": {
            "total": total_orgs,
        },
        "repositories": {
            "total": total_repos,
            "private": private_repos,
            "public": public_repos,
            "by_type": {
                "model": model_repos,
                "dataset": dataset_repos,
                "space": space_repos,
            },
        },
        "commits": {
            "total": total_commits,
            "top_contributors": contributors,
        },
        "lfs": {
            "total_objects": total_lfs_objects,
            "total_size": total_lfs_size,
        },
        "storage": {
            "private_used": total_private_used,
            "public_used": total_public_used,
            "total_used": total_private_used + total_public_used,
        },
    }


@router.get("/stats/timeseries")
async def get_timeseries_stats(
    days: int = Query(default=30, ge=1, le=365),
    _admin: bool = Depends(verify_admin_token),
):
    """Get time-series statistics for charts.

    Args:
        days: Number of days to include
        _admin: Admin authentication (dependency)

    Returns:
        Time-series data for various metrics
    """

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    # Repositories created per day
    repos_by_day = (
        Repository.select(
            Repository.created_at,
            Repository.repo_type,
        )
        .where(Repository.created_at >= cutoff_date)
        .order_by(Repository.created_at.asc())
    )

    # Group by date
    daily_repos = {}
    for repo in repos_by_day:
        date_key = repo.created_at.date().isoformat()
        if date_key not in daily_repos:
            daily_repos[date_key] = {"model": 0, "dataset": 0, "space": 0}
        daily_repos[date_key][repo.repo_type] += 1

    # Commits created per day
    commits_by_day = (
        Commit.select(Commit.created_at)
        .where(Commit.created_at >= cutoff_date)
        .order_by(Commit.created_at.asc())
    )

    daily_commits = {}
    for commit in commits_by_day:
        date_key = commit.created_at.date().isoformat()
        daily_commits[date_key] = daily_commits.get(date_key, 0) + 1

    # Users created per day (only regular users, not orgs)
    users_by_day = (
        User.select(User.created_at)
        .where((User.created_at >= cutoff_date) & (User.is_org == False))
        .order_by(User.created_at.asc())
    )

    daily_users = {}
    for user in users_by_day:
        date_key = user.created_at.date().isoformat()
        daily_users[date_key] = daily_users.get(date_key, 0) + 1

    return {
        "repositories_by_day": daily_repos,
        "commits_by_day": daily_commits,
        "users_by_day": daily_users,
    }


@router.get("/stats/top-repos")
async def get_top_repositories(
    limit: int = Query(default=10, ge=1, le=100),
    by: str = Query(default="commits", pattern="^(commits|size)$"),
    _admin: bool = Depends(verify_admin_token),
):
    """Get top repositories by various metrics.

    Args:
        limit: Number of top repos to return
        by: Sort by 'commits' or 'size'
        _admin: Admin authentication (dependency)

    Returns:
        List of top repositories
    """

    if by == "commits":
        # Top repos by commit count (using FK)
        top_repos = (
            Commit.select(
                Commit.repository,
                fn.COUNT(Commit.id).alias("count"),
            )
            .group_by(Commit.repository)
            .order_by(fn.COUNT(Commit.id).desc())
            .limit(limit)
        )

        result = []
        for item in top_repos:
            repo = item.repository
            result.append(
                {
                    "repo_full_id": repo.full_id if repo else "unknown",
                    "repo_type": repo.repo_type if repo else "unknown",
                    "commit_count": item.count,
                    "private": repo.private if repo else False,
                }
            )

    else:  # by size
        # Top repos by total file size (active files only, using FK)
        top_repos = (
            File.select(File.repository, fn.SUM(File.size).alias("total_size"))
            .where(File.is_deleted == False)
            .group_by(File.repository)
            .order_by(fn.SUM(File.size).desc())
            .limit(limit)
        )

        result = []
        for item in top_repos:
            repo = item.repository
            result.append(
                {
                    "repo_full_id": repo.full_id if repo else "unknown",
                    "repo_type": repo.repo_type if repo else "unknown",
                    "total_size": item.total_size,
                    "private": repo.private if repo else False,
                }
            )

    return {"top_repositories": result, "sorted_by": by}
