"""Decorators for adding fallback functionality to endpoints."""

import asyncio
import inspect
from functools import wraps
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import Response

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger
from kohakuhub.db_operations import get_merged_external_tokens
from kohakuhub.api.fallback.config import get_enabled_sources
from kohakuhub.api.fallback.operations import (
    fetch_external_list,
    try_fallback_info,
    try_fallback_org_avatar,
    try_fallback_paths_info,
    try_fallback_resolve,
    try_fallback_tree,
    try_fallback_user_avatar,
    try_fallback_user_profile,
    try_fallback_user_repos,
)

logger = get_logger("FALLBACK_DEC")

OperationType = Literal["resolve", "tree", "info", "revision", "paths_info"]
UserOperationType = Literal["profile", "repos", "avatar"]


def _repo_sort_key(item: dict) -> tuple[str, str, str]:
    return (
        item.get("lastModified") or "",
        item.get("createdAt") or "",
        item.get("id") or "",
    )


def with_repo_fallback(operation: OperationType):
    """Decorator for endpoints that access individual repositories.

    Falls back to external sources if repository/file not found locally.

    Args:
        operation: Type of operation ("resolve", "tree", "info", "revision", "paths_info")

    Returns:
        Decorated function
    """

    def decorator(func):
        # Get function signature to extract default values
        sig = inspect.signature(func)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract fallback param - priority: query param > kwargs > function default > True
            fallback_enabled = True  # Default to True

            # Check function signature default
            if "fallback" in sig.parameters:
                default = sig.parameters["fallback"].default
                if default != inspect.Parameter.empty:
                    fallback_enabled = default

            # Check kwargs (FastAPI injected value)
            if "fallback" in kwargs:
                fallback_enabled = kwargs["fallback"]

            # Check query param (highest priority - overrides everything)
            request = kwargs.get("request")
            if request and hasattr(request, "query_params"):
                fallback_param = request.query_params.get("fallback")
                if fallback_param is not None:
                    fallback_enabled = fallback_param.lower() not in (
                        "false",
                        "0",
                        "no",
                    )

            # Check if fallback is enabled globally and not disabled by param
            if not cfg.fallback.enabled or not fallback_enabled:
                return await func(*args, **kwargs)

            # Extract repo info from kwargs
            # Handle both "repo_type" (API endpoints) and "type" (public endpoints)
            repo_type = kwargs.get("repo_type") or kwargs.get("type")

            # Convert enum to string if needed
            if repo_type and hasattr(repo_type, "value"):
                repo_type = repo_type.value

            # If repo_type not in kwargs, try to parse from request path
            if not repo_type and "request" in kwargs:
                request = kwargs["request"]
                path = request.url.path
                logger.debug(f"Parsing repo_type from path: {path}")
                match path:
                    case _ if "/models/" in path:
                        repo_type = "model"
                    case _ if "/datasets/" in path:
                        repo_type = "dataset"
                    case _ if "/spaces/" in path:
                        repo_type = "space"
                    case _:
                        repo_type = "model"  # Default fallback
                logger.debug(f"Detected repo_type from path: {repo_type}")

            # Final fallback to "model" if still not determined
            if not repo_type:
                logger.debug("No repo_type found, defaulting to 'model'")
                repo_type = "model"

            namespace = kwargs.get("namespace")
            name = kwargs.get("name") or kwargs.get("repo_name")

            logger.debug(
                f"Fallback decorator params: repo_type={repo_type}, namespace={namespace}, name={name}"
            )

            if not namespace or not name:
                # Can't determine repo, skip fallback
                return await func(*args, **kwargs)

            is_404 = False
            original_error = None
            original_response = None

            try:
                # Try local first
                local_result = await func(*args, **kwargs)

                # Check if result is a 404 Response (any FastAPI response type)
                if (
                    isinstance(local_result, Response)
                    and getattr(local_result, "status_code", 200) == 404
                ):
                    is_404 = True
                    original_response = local_result
                    logger.info(
                        f"Local 404 response for {repo_type}/{namespace}/{name}, trying fallback sources..."
                    )
                else:
                    return local_result

            except HTTPException as e:
                # Only fallback on 404 errors
                if e.status_code != 404:
                    raise

                is_404 = True
                original_error = e
                logger.info(
                    f"Local 404 exception for {repo_type}/{namespace}/{name}, trying fallback sources..."
                )

            # If we got here, we have a 404 - try fallback
            if is_404:
                # Get user and external tokens for fallback
                user = kwargs.get("user")  # May be None for anonymous access
                request = kwargs.get("request")
                header_tokens = (
                    getattr(request.state, "external_tokens", {}) if request else {}
                )

                # Merge DB tokens + header tokens
                user_tokens = get_merged_external_tokens(user, header_tokens)

                # Try fallback based on operation type
                match operation:
                    case "resolve":
                        revision = kwargs.get("revision", "main")
                        path = kwargs.get("path", "")
                        # Detect HTTP method from request
                        request = kwargs.get("request")
                        method = request.method if request else "GET"
                        result = await try_fallback_resolve(
                            repo_type,
                            namespace,
                            name,
                            revision,
                            path,
                            user_tokens=user_tokens,
                            method=method,
                        )

                    case "tree":
                        revision = kwargs.get("revision", "main")
                        path = kwargs.get("path", "")
                        result = await try_fallback_tree(
                            repo_type,
                            namespace,
                            name,
                            revision,
                            path,
                            user_tokens=user_tokens,
                        )

                    case "info" | "revision":
                        result = await try_fallback_info(
                            repo_type, namespace, name, user_tokens=user_tokens
                        )

                    case "paths_info":
                        # For paths-info, extract paths and revision from kwargs
                        revision = kwargs.get("revision", "main")
                        paths = kwargs.get("paths", [])
                        result = await try_fallback_paths_info(
                            repo_type,
                            namespace,
                            name,
                            revision,
                            paths,
                            user_tokens=user_tokens,
                        )

                    case _:
                        logger.warning(f"Unknown fallback operation: {operation}")
                        result = None

                if result:
                    logger.success(
                        f"Fallback SUCCESS for {operation}: {repo_type}/{namespace}/{name}"
                    )
                    return result
                else:
                    # Not found in any source
                    logger.debug(
                        f"Fallback MISS for {operation}: {repo_type}/{namespace}/{name}"
                    )
                    # Return original 404 response or raise original exception
                    if original_error:
                        raise original_error
                    else:
                        # Return the original 404 JSONResponse
                        return original_response

        return wrapper

    return decorator


def with_list_aggregation(repo_type: str):
    """Decorator for list endpoints.

    Merges results from local + external sources.

    Args:
        repo_type: "model", "dataset", or "space"

    Returns:
        Decorated function
    """

    def decorator(func):
        # Get function signature to find 'fallback' parameter position
        sig = inspect.signature(func)
        param_names = list(sig.parameters.keys())
        fallback_index = (
            param_names.index("fallback") if "fallback" in param_names else -1
        )

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract fallback parameter from args or kwargs
            fallback_enabled = True  # Default to True

            # Try kwargs first
            if "fallback" in kwargs:
                fallback_enabled = kwargs["fallback"]
            # Try positional args
            elif fallback_index >= 0 and len(args) > fallback_index:
                fallback_enabled = args[fallback_index]
            # Use default from signature
            elif fallback_index >= 0:
                default = sig.parameters["fallback"].default
                if default != inspect.Parameter.empty:
                    fallback_enabled = default

            logger.info(
                f"with_list_aggregation decorator params: fallback_enabled={fallback_enabled}"
            )

            # Check if fallback is enabled globally and not disabled by param
            if not cfg.fallback.enabled or not fallback_enabled:
                # Call without fallback
                return await func(*args, **kwargs)

            # Get local results
            local_results = await func(*args, **kwargs)

            # Ensure results is a list
            if not isinstance(local_results, list):
                return local_results

            # Add source tag to local results
            for item in local_results:
                if isinstance(item, dict):
                    item["_source"] = "local"
                    item["_source_url"] = cfg.app.base_url

            # Get author from kwargs or args
            # Functions are called as: _list_models_with_aggregation(author, limit, sort, user)
            author = kwargs.get("author")
            if author is None and len(args) > 0:
                author = args[0]  # First positional arg is author

            # Build query params dict for external sources
            query_params = {
                "author": author,
                "limit": kwargs.get("limit", args[1] if len(args) > 1 else 50),
                "sort": kwargs.get("sort", args[2] if len(args) > 2 else "recent"),
            }

            # Get user and external tokens for fallback
            user = kwargs.get("user") or (
                args[3] if len(args) > 3 else None
            )  # 4th arg is user
            request = kwargs.get("request")
            header_tokens = (
                getattr(request.state, "external_tokens", {}) if request else {}
            )

            # Merge DB tokens + header tokens
            user_tokens = get_merged_external_tokens(user, header_tokens)

            sources = get_enabled_sources(
                namespace=author or "", user_tokens=user_tokens
            )

            if not sources:
                logger.debug("No fallback sources for list aggregation")
                return local_results

            # Fetch from external sources concurrently
            logger.info(
                f"Aggregating {repo_type} list from {len(sources)} external sources..."
            )

            external_tasks = [
                fetch_external_list(source, repo_type, query_params)
                for source in sources
            ]

            external_results_list = await asyncio.gather(
                *external_tasks, return_exceptions=True
            )

            # Merge results
            all_results = local_results.copy()
            seen_ids = {
                item.get("id")
                for item in local_results
                if isinstance(item, dict) and "id" in item
            }  # Local takes precedence

            for external_results in external_results_list:
                if isinstance(external_results, Exception):
                    logger.warning(f"External source failed: {external_results}")
                    continue

                if not isinstance(external_results, list):
                    continue

                for item in external_results:
                    if not isinstance(item, dict):
                        continue

                    item_id = item.get("id")
                    if item_id and item_id not in seen_ids:
                        all_results.append(item)
                        seen_ids.add(item_id)

            sort = kwargs.get("sort", args[2] if len(args) > 2 else "recent")
            if sort == "updated":
                all_results.sort(key=_repo_sort_key, reverse=True)

            # Get limit from kwargs (if None or very large, return all)
            limit = kwargs.get("limit")
            if limit is None or limit >= len(all_results):
                # No effective limit - return all results
                final_results = all_results
            else:
                # Apply limit after merging
                final_results = all_results[:limit]

            logger.info(
                f"Aggregated {len(final_results)} {repo_type}s (local: {len(local_results)}, total merged: {len(all_results)})"
            )

            return final_results

        return wrapper

    return decorator


def with_user_fallback(operation: UserOperationType):
    """Decorator for user/org endpoints.

    Falls back to external sources if user/org not found locally.

    Args:
        operation: Type of operation ("profile", "repos")

    Returns:
        Decorated function
    """

    def decorator(func):
        # Get function signature to extract default values
        sig = inspect.signature(func)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract fallback param - priority: query param > kwargs > function default > True
            fallback_enabled = True  # Default to True

            # Check function signature default
            if "fallback" in sig.parameters:
                default = sig.parameters["fallback"].default
                if default != inspect.Parameter.empty:
                    fallback_enabled = default

            # Check kwargs (FastAPI injected value)
            if "fallback" in kwargs:
                fallback_enabled = kwargs["fallback"]

            # Check query param (highest priority - overrides everything)
            request = kwargs.get("request")
            if request and hasattr(request, "query_params"):
                fallback_param = request.query_params.get("fallback")
                if fallback_param is not None:
                    fallback_enabled = fallback_param.lower() not in (
                        "false",
                        "0",
                        "no",
                    )

            # Check if fallback is enabled globally and not disabled by param
            if not cfg.fallback.enabled or not fallback_enabled:
                return await func(*args, **kwargs)

            # Extract username/org_name from kwargs
            username = kwargs.get("username") or kwargs.get("org_name")

            if not username:
                return await func(*args, **kwargs)

            is_404 = False
            original_error = None
            original_response = None

            try:
                # Try local first
                local_result = await func(*args, **kwargs)

                # Check if result is a 404 Response
                if (
                    isinstance(local_result, Response)
                    and getattr(local_result, "status_code", 200) == 404
                ):
                    is_404 = True
                    original_response = local_result
                    logger.info(
                        f"Local 404 response for user {username}, trying fallback sources..."
                    )
                else:
                    return local_result

            except HTTPException as e:
                # Only fallback on 404 errors
                if e.status_code != 404:
                    raise

                is_404 = True
                original_error = e
                logger.info(
                    f"Local 404 exception for user {username}, trying fallback sources..."
                )

            # If we got here, we have a 404 - try fallback
            if is_404:
                # Get user and external tokens for fallback
                user = kwargs.get("user")  # May be None for anonymous access
                request = kwargs.get("request")
                header_tokens = (
                    getattr(request.state, "external_tokens", {}) if request else {}
                )

                # Merge DB tokens + header tokens
                user_tokens = get_merged_external_tokens(user, header_tokens)

                match operation:
                    case "profile":
                        result = await try_fallback_user_profile(
                            username, user_tokens=user_tokens
                        )

                    case "repos":
                        result = await try_fallback_user_repos(
                            username, user_tokens=user_tokens
                        )

                    case "avatar":
                        # Check if it's org or user based on parameter name
                        org_name = kwargs.get("org_name")
                        if org_name:
                            result = await try_fallback_org_avatar(
                                org_name, user_tokens=user_tokens
                            )
                        else:
                            result = await try_fallback_user_avatar(
                                username, user_tokens=user_tokens
                            )

                    case _:
                        logger.warning(f"Unknown user fallback operation: {operation}")
                        result = None

                if result:
                    logger.success(f"Fallback SUCCESS for user {operation}: {username}")
                    # For avatar operation, wrap bytes in Response
                    if operation == "avatar" and isinstance(result, bytes):
                        return Response(
                            content=result,
                            media_type="image/jpeg",
                            headers={
                                "Cache-Control": "public, max-age=86400",  # 24 hour cache
                            },
                        )
                    return result
                else:
                    # Not found in any source
                    logger.debug(f"Fallback MISS for user {operation}: {username}")
                    if original_error:
                        raise original_error
                    else:
                        return original_response

        return wrapper

    return decorator
