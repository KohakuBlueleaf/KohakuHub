"""Git Smart HTTP Protocol endpoints.

This module implements Git Smart HTTP protocol for clone, fetch, pull, and push operations.
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response
from fastapi.responses import StreamingResponse

from kohakuhub.api.utils.git_lakefs_bridge import GitLakeFSBridge
from kohakuhub.api.utils.git_server import (
    GitReceivePackHandler,
    GitUploadPackHandler,
    parse_git_credentials,
)
from kohakuhub.auth.dependencies import get_optional_user
from kohakuhub.auth.permissions import (
    check_repo_read_permission,
    check_repo_write_permission,
)
from kohakuhub.auth.utils import hash_token
from kohakuhub.db import Repository, Token, User
from kohakuhub.db_async import execute_db_query
from kohakuhub.logger import get_logger

logger = get_logger("GIT_HTTP")
router = APIRouter()


async def get_user_from_git_auth(authorization: str | None) -> User | None:
    """Authenticate user from Git Basic Auth header.

    Args:
        authorization: Authorization header value

    Returns:
        Authenticated user or None
    """
    if not authorization:
        return None

    username, token_str = parse_git_credentials(authorization)
    if not username or not token_str:
        return None

    # Hash token and lookup
    token_hash = hash_token(token_str)

    def _get_token():
        return Token.get_or_none(Token.token_hash == token_hash)

    token = await execute_db_query(_get_token)
    if not token:
        logger.debug(f"Invalid token for user {username}")
        return None

    # Get user
    def _get_user():
        return User.get_or_none(User.id == token.user_id)

    user = await execute_db_query(_get_user)
    if not user or not user.is_active:
        logger.warning(f"User {username} not found or inactive")
        return None

    # Update token last used
    from datetime import datetime, timezone

    def _update_token():
        Token.update(last_used=datetime.now(timezone.utc)).where(
            Token.id == token.id
        ).execute()

    await execute_db_query(_update_token)

    logger.info(f"Authenticated {username} via Git HTTP")
    return user


@router.get("/{namespace}/{name}.git/info/refs")
async def git_info_refs(
    namespace: str,
    name: str,
    service: str,
    authorization: str | None = Header(None),
    repo_type: str = "model",  # Default to model
):
    """Git info/refs endpoint for service advertisement.

    This endpoint advertises available refs and capabilities for upload-pack or receive-pack.

    Args:
        namespace: Repository namespace
        name: Repository name
        service: Git service (git-upload-pack or git-receive-pack)
        authorization: Optional Basic Auth header
        repo_type: Repository type

    Returns:
        Service advertisement in pkt-line format
    """
    repo_id = f"{namespace}/{name}"
    logger.info(f"Git info/refs: {service} for {repo_id}")

    # Get repository
    def _get_repo():
        return Repository.get_or_none(
            Repository.namespace == namespace,
            Repository.name == name,
            Repository.repo_type == repo_type,
        )

    repo = await execute_db_query(_get_repo)
    if not repo:
        raise HTTPException(404, detail="Repository not found")

    # Authenticate user
    user = await get_user_from_git_auth(authorization)

    # Check permissions based on service
    if service == "git-upload-pack":
        # Read operation (clone/fetch/pull)
        check_repo_read_permission(repo, user)
    elif service == "git-receive-pack":
        # Write operation (push)
        if not user:
            raise HTTPException(401, detail="Authentication required for push")
        check_repo_write_permission(repo, user)
    else:
        raise HTTPException(400, detail=f"Unknown service: {service}")

    # Get refs from LakeFS
    bridge = GitLakeFSBridge(repo_type, namespace, name)
    refs = await bridge.get_refs(branch="main")

    # Generate service advertisement
    if service == "git-upload-pack":
        handler = GitUploadPackHandler(repo_id)
        response_data = handler.get_service_info(refs)
    elif service == "git-receive-pack":
        handler = GitReceivePackHandler(repo_id)
        response_data = handler.get_service_info(refs)
    else:
        raise HTTPException(400, detail=f"Unknown service: {service}")

    return Response(
        content=response_data,
        media_type=f"application/x-{service}-advertisement",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/{namespace}/{name}.git/git-upload-pack")
async def git_upload_pack(
    namespace: str,
    name: str,
    request: Request,
    authorization: str | None = Header(None),
    repo_type: str = "model",
):
    """Git upload-pack endpoint for clone/fetch/pull.

    This endpoint handles git-upload-pack requests to download objects.

    Args:
        namespace: Repository namespace
        name: Repository name
        request: FastAPI request
        authorization: Optional Basic Auth header
        repo_type: Repository type

    Returns:
        Pack file with requested objects
    """
    repo_id = f"{namespace}/{name}"
    logger.info(f"Git upload-pack for {repo_id}")

    # Get repository
    def _get_repo():
        return Repository.get_or_none(
            Repository.namespace == namespace,
            Repository.name == name,
            Repository.repo_type == repo_type,
        )

    repo = await execute_db_query(_get_repo)
    if not repo:
        raise HTTPException(404, detail="Repository not found")

    # Authenticate and check read permission
    user = await get_user_from_git_auth(authorization)
    check_repo_read_permission(repo, user)

    # Read request body
    request_body = await request.body()

    # Create bridge for LakeFS integration
    bridge = GitLakeFSBridge(repo_type, namespace, name)

    # Handle upload-pack
    handler = GitUploadPackHandler(repo_id, bridge=bridge)
    response_data = await handler.handle_upload_pack(request_body)

    return Response(
        content=response_data,
        media_type="application/x-git-upload-pack-result",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/{namespace}/{name}.git/git-receive-pack")
async def git_receive_pack(
    namespace: str,
    name: str,
    request: Request,
    authorization: str | None = Header(None),
    repo_type: str = "model",
):
    """Git receive-pack endpoint for push.

    This endpoint handles git-receive-pack requests to upload objects.

    Args:
        namespace: Repository namespace
        name: Repository name
        request: FastAPI request
        authorization: Basic Auth header (required)
        repo_type: Repository type

    Returns:
        Status report
    """
    repo_id = f"{namespace}/{name}"
    logger.info(f"Git receive-pack for {repo_id}")

    # Get repository
    def _get_repo():
        return Repository.get_or_none(
            Repository.namespace == namespace,
            Repository.name == name,
            Repository.repo_type == repo_type,
        )

    repo = await execute_db_query(_get_repo)
    if not repo:
        raise HTTPException(404, detail="Repository not found")

    # Authenticate and check write permission
    user = await get_user_from_git_auth(authorization)
    if not user:
        raise HTTPException(401, detail="Authentication required for push")

    check_repo_write_permission(repo, user)

    # Read request body
    request_body = await request.body()

    # Handle receive-pack
    handler = GitReceivePackHandler(repo_id)
    response_data = await handler.handle_receive_pack(request_body)

    return Response(
        content=response_data,
        media_type="application/x-git-receive-pack-result",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/{namespace}/{name}.git/HEAD")
async def git_head(
    namespace: str,
    name: str,
    authorization: str | None = Header(None),
    repo_type: str = "model",
):
    """Git HEAD endpoint.

    Returns the default branch reference.

    Args:
        namespace: Repository namespace
        name: Repository name
        authorization: Optional Basic Auth header
        repo_type: Repository type

    Returns:
        HEAD reference
    """
    repo_id = f"{namespace}/{name}"

    # Get repository
    def _get_repo():
        return Repository.get_or_none(
            Repository.namespace == namespace,
            Repository.name == name,
            Repository.repo_type == repo_type,
        )

    repo = await execute_db_query(_get_repo)
    if not repo:
        raise HTTPException(404, detail="Repository not found")

    # Authenticate and check read permission
    user = await get_user_from_git_auth(authorization)
    check_repo_read_permission(repo, user)

    # Return HEAD reference
    return Response(
        content=b"ref: refs/heads/main\n",
        media_type="text/plain",
    )
