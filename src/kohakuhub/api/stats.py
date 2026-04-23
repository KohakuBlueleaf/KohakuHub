"""Repository statistics and trending API endpoints."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from kohakuhub.db import DailyRepoStats, Repository, User
from kohakuhub.db_operations import get_repository
from kohakuhub.logger import get_logger
from kohakuhub.auth.dependencies import get_optional_user
from kohakuhub.auth.permissions import check_repo_read_permission
from kohakuhub.api.utils.downloads import ensure_stats_up_to_date
from kohakuhub.api.repo.utils.hf import hf_repo_not_found

logger = get_logger("STATS")

router = APIRouter()


@router.get("/{repo_type}s/{namespace}/{name}/stats")
async def get_repository_stats(
    repo_type: str,
    namespace: str,
    name: str,
    user: User | None = Depends(get_optional_user),
):
    """Get repository statistics (downloads, likes).

    Triggers lazy aggregation for historical dates if needed.
    TODAY's stats are already real-time updated.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        user: Current authenticated user (optional)

    Returns:
        Repository statistics

    Raises:
        HTTPException: If repository not found
    """
    repo_id = f"{namespace}/{name}"
    repo = get_repository(repo_type, namespace, name)

    if not repo:
        return hf_repo_not_found(repo_id, repo_type)

    # Check read permission
    check_repo_read_permission(repo, user)

    # Ensure stats are up-to-date (lazy aggregation for historical dates)
    await ensure_stats_up_to_date(repo)

    return {
        "downloads": repo.downloads,
        "likes": repo.likes_count,
    }


@router.get("/{repo_type}s/{namespace}/{name}/stats/recent")
async def get_recent_stats(
    repo_type: str,
    namespace: str,
    name: str,
    days: int = Query(30, ge=1, le=365),
    user: User | None = Depends(get_optional_user),
):
    """Get recent download statistics for trends.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        days: Number of days to retrieve (default: 30, max: 365)
        user: Current authenticated user (optional)

    Returns:
        Daily download statistics for the requested period

    Raises:
        HTTPException: If repository not found
    """
    repo_id = f"{namespace}/{name}"
    repo = get_repository(repo_type, namespace, name)

    if not repo:
        return hf_repo_not_found(repo_id, repo_type)

    # Check read permission
    check_repo_read_permission(repo, user)

    # Ensure stats are up-to-date
    await ensure_stats_up_to_date(repo)

    # Get stats for last N days
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days - 1)

    daily_stats = list(
        DailyRepoStats.select()
        .where(
            (DailyRepoStats.repository == repo)
            & (DailyRepoStats.date >= start_date)
            & (DailyRepoStats.date <= end_date)
        )
        .order_by(DailyRepoStats.date.asc())
    )

    return {
        "stats": [
            {
                "date": str(stat.date),
                "downloads": stat.download_sessions,
                "authenticated": stat.authenticated_downloads,
                "anonymous": stat.anonymous_downloads,
                "files": stat.total_files,
            }
            for stat in daily_stats
        ],
        "period": {"start": str(start_date), "end": str(end_date), "days": days},
    }


@router.get("/trending")
async def get_trending_repositories(
    repo_type: str = Query("model", regex="^(model|dataset|space)$"),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(20, ge=1, le=100),
    user: User | None = Depends(get_optional_user),
):
    """Get trending repositories based on recent downloads.

    Trending score = (downloads_last_7_days * 10) + (downloads_last_30_days * 1)

    Args:
        repo_type: Repository type filter
        days: Calculate trend based on last N days (default: 7)
        limit: Maximum number of repos to return
        user: Current authenticated user (optional)

    Returns:
        List of trending repositories sorted by score
    """
    # Get date range
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days - 1)

    # Query all DailyRepoStats for the period
    stats_query = DailyRepoStats.select(
        DailyRepoStats.repository, DailyRepoStats.download_sessions
    ).where((DailyRepoStats.date >= start_date) & (DailyRepoStats.date <= today))

    # Aggregate by repository
    repo_downloads = {}  # repo_id -> total_downloads_in_period
    for stat in stats_query:
        repo_id = stat.repository.id
        if repo_id not in repo_downloads:
            repo_downloads[repo_id] = 0
        repo_downloads[repo_id] += stat.download_sessions

    # Get top repositories
    top_repos = sorted(repo_downloads.items(), key=lambda x: x[1], reverse=True)[:limit]

    # Build response
    trending = []
    for repo_id, download_count in top_repos:
        repo = Repository.get_or_none(
            (Repository.id == repo_id) & (Repository.repo_type == repo_type)
        )

        if not repo:
            continue

        # Check read permission (skip private repos user can't access)
        try:
            check_repo_read_permission(repo, user)
        except HTTPException:
            continue

        trending.append(
            {
                "id": repo.full_id,
                "type": repo.repo_type,
                "downloads": repo.downloads,
                "likes": repo.likes_count,
                "recent_downloads": download_count,
                "private": repo.private,
            }
        )

    return {
        "trending": trending,
        "period": {"start": str(start_date), "end": str(today), "days": days},
    }
