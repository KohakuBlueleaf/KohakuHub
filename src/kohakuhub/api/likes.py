"""Repository likes API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from kohakuhub.db import Repository, RepositoryLike, User, db
from kohakuhub.db_operations import (
    create_repository_like,
    delete_repository_like,
    get_repository,
    get_repository_like,
    get_user_by_username,
    list_repository_likers,
    update_repository,
)
from kohakuhub.logger import get_logger
from kohakuhub.auth.dependencies import get_current_user, get_optional_user
from kohakuhub.auth.permissions import check_repo_read_permission
from kohakuhub.api.repo.utils.hf import hf_repo_not_found

logger = get_logger("LIKES")

router = APIRouter()


@router.post("/{repo_type}s/{namespace}/{name}/like")
async def like_repository(
    repo_type: str,
    namespace: str,
    name: str,
    user: User = Depends(get_current_user),
):
    """Like a repository.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        user: Current authenticated user

    Returns:
        Success message with updated like count

    Raises:
        HTTPException: If repository not found or already liked
    """
    repo_id = f"{namespace}/{name}"
    repo = get_repository(repo_type, namespace, name)

    if not repo:
        return hf_repo_not_found(repo_id, repo_type)

    # Check read permission (must have read access to like)
    check_repo_read_permission(repo, user)

    # Check if already liked
    existing_like = get_repository_like(repo, user)
    if existing_like:
        raise HTTPException(400, detail={"error": "Repository already liked"})

    # Create like and increment counter (atomic transaction)
    with db.atomic():
        create_repository_like(repo, user)
        new_count = repo.likes_count + 1
        update_repository(repo, likes_count=new_count)

    logger.info(f"User {user.username} liked {repo_id}")

    return {
        "success": True,
        "message": "Repository liked successfully",
        "likes_count": new_count,
    }


@router.delete("/{repo_type}s/{namespace}/{name}/like")
async def unlike_repository(
    repo_type: str,
    namespace: str,
    name: str,
    user: User = Depends(get_current_user),
):
    """Unlike a repository.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        user: Current authenticated user

    Returns:
        Success message with updated like count

    Raises:
        HTTPException: If repository not found or not liked
    """
    repo_id = f"{namespace}/{name}"
    repo = get_repository(repo_type, namespace, name)

    if not repo:
        return hf_repo_not_found(repo_id, repo_type)

    # Delete like and decrement counter (atomic transaction)
    with db.atomic():
        deleted = delete_repository_like(repo, user)

        if deleted == 0:
            raise HTTPException(400, detail={"error": "Repository not liked"})

        new_count = max(0, repo.likes_count - 1)
        update_repository(repo, likes_count=new_count)

    logger.info(f"User {user.username} unliked {repo_id}")

    return {
        "success": True,
        "message": "Repository unliked successfully",
        "likes_count": new_count,
    }


@router.get("/{repo_type}s/{namespace}/{name}/like")
async def check_repository_liked(
    repo_type: str,
    namespace: str,
    name: str,
    user: User | None = Depends(get_optional_user),
):
    """Check if current user has liked a repository.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        user: Current authenticated user (optional)

    Returns:
        Dict with liked status

    Raises:
        HTTPException: If repository not found
    """
    repo_id = f"{namespace}/{name}"
    repo = get_repository(repo_type, namespace, name)

    if not repo:
        return hf_repo_not_found(repo_id, repo_type)

    # Check read permission
    check_repo_read_permission(repo, user)

    if not user:
        # Anonymous users can't like
        return {"liked": False}

    # Check if liked
    liked = get_repository_like(repo, user) is not None

    return {"liked": liked}


@router.get("/{repo_type}s/{namespace}/{name}/likers")
async def list_repository_likers_endpoint(
    repo_type: str,
    namespace: str,
    name: str,
    limit: int = 50,
    user: User | None = Depends(get_optional_user),
):
    """List users who liked a repository.

    Args:
        repo_type: Repository type (model/dataset/space)
        namespace: Repository namespace
        name: Repository name
        limit: Maximum number of likers to return
        user: Current authenticated user (optional)

    Returns:
        List of users who liked the repository

    Raises:
        HTTPException: If repository not found
    """
    repo_id = f"{namespace}/{name}"
    repo = get_repository(repo_type, namespace, name)

    if not repo:
        return hf_repo_not_found(repo_id, repo_type)

    # Check read permission
    check_repo_read_permission(repo, user)

    # Get likers
    likers = list_repository_likers(repo, limit=limit)
    return [
        {
            "user": liker.username,
            "fullname": liker.full_name or liker.username,
            "avatarUrl": None,
        }
        for liker in likers
    ]


@router.get("/users/{username}/likes")
async def list_user_likes(
    username: str,
    limit: int = 50,
    user: User | None = Depends(get_optional_user),
):
    """List repositories that a user has liked.

    Args:
        username: Username to query
        limit: Maximum number of repos to return
        user: Current authenticated user (optional)

    Returns:
        List of repositories user has liked
    """
    target_user = get_user_by_username(username)
    if not target_user:
        raise HTTPException(404, detail={"error": "User not found"})

    # Get liked repositories
    likes = (
        RepositoryLike.select()
        .where(RepositoryLike.user == target_user)
        .order_by(RepositoryLike.created_at.desc())
        .limit(limit)
    )

    repos = []
    for like in likes:
        repo = like.repository
        # Check if current user has read permission
        try:
            check_repo_read_permission(repo, user)
            repos.append(
                {
                    "createdAt": like.created_at.isoformat(),
                    "repo": {
                        "name": repo.full_id,
                        "type": repo.repo_type,
                    },
                }
            )
        except HTTPException:
            # Skip private repos user can't access
            continue

    return repos
