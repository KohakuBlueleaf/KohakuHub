"""Fallback operations for different endpoint types."""

import asyncio
from typing import Optional

import httpx
from fastapi.responses import RedirectResponse, Response

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger
from kohakuhub.api.fallback.cache import get_cache
from kohakuhub.api.fallback.client import FallbackClient
from kohakuhub.api.fallback.config import get_enabled_sources
from kohakuhub.api.fallback.utils import (
    add_source_headers,
    extract_error_message,
    is_not_found_error,
    should_retry_source,
)

logger = get_logger("FALLBACK_OPS")


async def try_fallback_resolve(
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    path: str,
    user_tokens: dict[str, str] | None = None,
    method: str = "GET",
) -> Optional[Response]:
    """Try to resolve file from fallback sources.

    Args:
        repo_type: "model", "dataset", or "space"
        namespace: Repository namespace
        name: Repository name
        revision: Branch or commit
        path: File path in repository
        user_tokens: User-provided external tokens (overrides admin tokens)
        method: HTTP method ("GET" or "HEAD")

    Returns:
        Response (redirect for GET, response with headers for HEAD) or None if not found
    """
    cache = get_cache()
    sources = get_enabled_sources(namespace, user_tokens=user_tokens)

    if not sources:
        logger.debug(f"No fallback sources configured for {namespace}")
        return None

    # Check cache first
    cached = cache.get(repo_type, namespace, name)
    if cached and cached.get("exists"):
        # Cache hit - try this source first
        source_url = cached["source_url"]
        source_name = cached["source_name"]
        source_type = cached["source_type"]

        # Find source config by URL
        source_config = next((s for s in sources if s["url"] == source_url), None)
        if source_config:
            sources = [source_config] + [s for s in sources if s["url"] != source_url]
            logger.debug(
                f"Cache hit: trying {source_name} first for {namespace}/{name}"
            )

    # Construct KohakuHub path
    kohaku_path = f"/{repo_type}s/{namespace}/{name}/resolve/{revision}/{path}"

    # Try each source in priority order
    for source in sources:
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )

            # Make HEAD request to check if file exists
            response = await client.head(kohaku_path, repo_type)

            # Accept 2xx (success) or 3xx (redirect) as "file exists"
            # HuggingFace often returns 307 redirects to CDN
            if 200 <= response.status_code < 400:
                logger.debug(
                    f"HEAD returned {response.status_code}, file exists at {source['name']}"
                )

                # Update cache
                cache.set(
                    repo_type,
                    namespace,
                    name,
                    source["url"],
                    source["name"],
                    source["source_type"],
                    exists=True,
                )

                logger.info(
                    f"Fallback SUCCESS: {repo_type}/{namespace}/{name} found in {source['name']}"
                )

                if method == "HEAD":
                    # For HEAD: Return response with original headers
                    resp_headers = dict(response.headers)
                    resp_headers.update(
                        add_source_headers(response, source["name"], source["url"])
                    )
                    final_resp = Response(
                        status_code=response.status_code,
                        content=response.content,
                        headers=resp_headers,
                    )
                    return final_resp
                else:
                    # For GET: Make actual GET request to fetch content (proxy)
                    get_response = await client.get(
                        kohaku_path, repo_type, follow_redirects=True
                    )

                    if get_response.status_code == 200:
                        # Proxy the content with original headers
                        resp_headers = dict(get_response.headers)

                        # Remove compression headers since httpx already decompressed
                        # Otherwise browser will try to decompress already-decompressed content
                        resp_headers.pop("content-encoding", None)
                        resp_headers.pop(
                            "content-length", None
                        )  # Length may be wrong after decompression
                        resp_headers.pop("transfer-encoding", None)

                        resp_headers.update(
                            add_source_headers(
                                get_response, source["name"], source["url"]
                            )
                        )
                        final_resp = Response(
                            status_code=get_response.status_code,
                            content=get_response.content,
                            headers=resp_headers,
                        )
                        return final_resp
                    else:
                        # GET failed, try next source
                        logger.warning(
                            f"GET request failed for {source['name']}: {get_response.status_code}"
                        )
                        continue

            elif not should_retry_source(response):
                # Don't try more sources on auth/permission errors
                logger.warning(
                    f"Fallback stopped at {source['name']}: {response.status_code}"
                )
                return None

        except httpx.TimeoutException:
            logger.warning(f"Fallback source {source['name']} timed out")
            continue

        except Exception as e:
            logger.warning(f"Fallback source {source['name']} failed: {e}")
            continue

    # Not found in any source
    logger.debug(
        f"Fallback MISS: {repo_type}/{namespace}/{name} not found in any source"
    )
    return None


async def try_fallback_info(
    repo_type: str,
    namespace: str,
    name: str,
    user_tokens: dict[str, str] | None = None,
) -> Optional[dict]:
    """Try to get repository info from fallback sources.

    Args:
        repo_type: "model", "dataset", or "space"
        namespace: Repository namespace
        name: Repository name
        user_tokens: User-provided external tokens (overrides admin tokens)

    Returns:
        Repository info dict or None if not found
    """
    cache = get_cache()
    sources = get_enabled_sources(namespace, user_tokens=user_tokens)

    if not sources:
        return None

    # Check cache first
    cached = cache.get(repo_type, namespace, name)
    if cached and cached.get("exists"):
        source_url = cached["source_url"]
        source_config = next((s for s in sources if s["url"] == source_url), None)
        if source_config:
            sources = [source_config] + [s for s in sources if s["url"] != source_url]

    # Construct API path
    kohaku_path = f"/api/{repo_type}s/{namespace}/{name}"

    # Try each source
    for source in sources:
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )

            response = await client.get(kohaku_path, repo_type)

            if response.status_code == 200:
                data = response.json()

                # Add source tag
                data["_source"] = source["name"]
                data["_source_url"] = source["url"]

                # Update cache
                cache.set(
                    repo_type,
                    namespace,
                    name,
                    source["url"],
                    source["name"],
                    source["source_type"],
                    exists=True,
                )

                logger.info(
                    f"Fallback info SUCCESS: {repo_type}/{namespace}/{name} from {source['name']}"
                )
                return data

            elif not should_retry_source(response):
                return None

        except Exception as e:
            logger.warning(f"Fallback info failed for {source['name']}: {e}")
            continue

    return None


async def try_fallback_tree(
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    path: str = "",
    recursive: bool = False,
    expand: bool = False,
    limit: int | None = None,
    cursor: str | None = None,
    user_tokens: dict[str, str] | None = None,
) -> Optional[Response]:
    """Try to get repository tree from fallback sources.

    Args:
        repo_type: "model", "dataset", or "space"
        namespace: Repository namespace
        name: Repository name
        revision: Branch or commit
        path: Path within repository
        user_tokens: User-provided external tokens (overrides admin tokens)

    Returns:
        JSON response or None if not found
    """
    sources = get_enabled_sources(namespace, user_tokens=user_tokens)

    if not sources:
        return None

    # Construct API path (strip leading slash from path to avoid double slash)
    clean_path = path.lstrip("/") if path else ""
    kohaku_path = f"/api/{repo_type}s/{namespace}/{name}/tree/{revision}/{clean_path}"

    # Try each source
    for source in sources:
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )

            params = {
                "recursive": recursive,
                "expand": expand,
            }
            if limit is not None:
                params["limit"] = limit
            if cursor:
                params["cursor"] = cursor

            response = await client.get(kohaku_path, repo_type, params=params)

            if response.status_code == 200:
                logger.info(
                    f"Fallback tree SUCCESS: {repo_type}/{namespace}/{name}/tree from {source['name']}"
                )
                headers = {}
                if response.headers.get("link"):
                    headers["Link"] = response.headers["link"]
                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type"),
                    headers=headers,
                )

            elif not should_retry_source(response):
                return None

        except Exception as e:
            logger.warning(f"Fallback tree failed for {source['name']}: {e}")
            continue

    return None


async def try_fallback_paths_info(
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    paths: list[str],
    expand: bool = False,
    user_tokens: dict[str, str] | None = None,
) -> Optional[list]:
    """Try to get paths info from fallback sources.

    Args:
        repo_type: "model", "dataset", or "space"
        namespace: Repository namespace
        name: Repository name
        revision: Branch or commit
        paths: List of paths to query
        user_tokens: User-provided external tokens (overrides admin tokens)

    Returns:
        List of path info objects or None if not found
    """
    sources = get_enabled_sources(namespace, user_tokens=user_tokens)

    if not sources:
        return None

    # Construct API path
    kohaku_path = f"/api/{repo_type}s/{namespace}/{name}/paths-info/{revision}"

    # Try each source
    for source in sources:
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )

            # POST request with form data
            response = await client.post(
                kohaku_path, repo_type, data={"paths": paths, "expand": expand}
            )

            if response.status_code == 200:
                data = response.json()

                logger.info(
                    f"Fallback paths-info SUCCESS: {repo_type}/{namespace}/{name} from {source['name']}"
                )
                return data

            elif not should_retry_source(response):
                return None

        except Exception as e:
            logger.warning(f"Fallback paths-info failed for {source['name']}: {e}")
            continue

    return None


async def fetch_external_list(
    source: dict, repo_type: str, query_params: dict
) -> list[dict]:
    """Fetch repository list from external source.

    Args:
        source: Source config dict
        repo_type: "model", "dataset", or "space"
        query_params: Query parameters (author, limit, sort, etc.)

    Returns:
        List of repository dicts with _source and _source_url added
    """
    try:
        # Construct API path
        kohaku_path = f"/api/{repo_type}s"

        # Build query string
        params = {}
        if query_params.get("author"):
            params["author"] = query_params["author"]
            logger.debug(f"Fetching {repo_type}s with author={params['author']}")
        if query_params.get("limit"):
            params["limit"] = query_params["limit"]
        # Don't send sort to HuggingFace - they don't support it
        # HF returns models sorted by downloads by default

        client = FallbackClient(
            source_url=source["url"],
            source_type=source["source_type"],
            token=source.get("token"),
        )

        # Make request with query params
        external_url = client.map_url(kohaku_path, repo_type)

        async with httpx.AsyncClient(timeout=client.timeout) as http_client:
            response = await http_client.get(external_url, params=params)

        if response.status_code == 200:
            results = response.json()

            # Add source tags to each item
            if isinstance(results, list):
                for item in results:
                    item["_source"] = source["name"]
                    item["_source_url"] = source["url"]

                logger.info(
                    f"Fetched {len(results)} {repo_type}s from {source['name']}"
                )
                return results

        logger.warning(
            f"Failed to fetch list from {source['name']}: {response.status_code}"
        )
        logger.debug(f"Request URL: {response.url}")
        logger.debug(f"Response: {response.text[:200]}")
        return []

    except Exception as e:
        logger.warning(f"Failed to fetch list from {source['name']}: {e}")
        return []


async def try_fallback_user_profile(
    username: str, user_tokens: dict[str, str] | None = None
) -> Optional[dict]:
    """Try to get user profile from fallback sources.

    HuggingFace workflow:
    1. Try /api/users/{name}/overview (works for users, returns 404 for orgs)
    2. If 404, try /api/organizations/{name}/members (works for orgs)
    3. If members succeeds → It's an org, return minimal profile
    4. If both fail → Not found

    Args:
        username: Username or org name to lookup
        user_tokens: User-provided external tokens (overrides admin tokens)

    Returns:
        User/org profile dict or None if not found
    """
    sources = get_enabled_sources(
        namespace="", user_tokens=user_tokens
    )  # Global sources only

    if not sources:
        return None

    for source in sources:
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )

            match source["source_type"]:
                case "huggingface":
                    # Step 1: Try user overview
                    user_path = f"/api/users/{username}/overview"
                    user_response = await client.get(user_path, "model")

                    if 200 <= user_response.status_code < 400:
                        # User overview succeeded
                        hf_data = user_response.json()
                        profile_data = {
                            "username": username,
                            "full_name": hf_data.get("fullname")
                            or hf_data.get("name")
                            or username,
                            "bio": None,
                            "website": None,
                            "social_media": None,
                            "created_at": hf_data.get("createdAt"),
                            "_source": source["name"],
                            "_source_url": source["url"],
                            "_partial": True,
                            "_hf_pro": hf_data.get("isPro", False),
                            "_avatar_url": hf_data.get("avatarUrl"),
                            "_hf_type": hf_data.get("type", "user"),
                        }
                        logger.info(
                            f"Fallback user profile SUCCESS: {username} from {source['name']} (type: {profile_data['_hf_type']})"
                        )
                        return profile_data

                    # Step 2: User overview failed, try org members
                    org_members_path = f"/api/organizations/{username}/members"
                    org_response = await client.get(org_members_path, "model")

                    if 200 <= org_response.status_code < 400:
                        # Org members endpoint succeeded → It's an org!
                        members_data = org_response.json()

                        # Try to get org info from first member's avatarUrl or other data
                        first_member = members_data[0] if members_data else {}

                        profile_data = {
                            "username": username,
                            "full_name": username,  # HF doesn't provide org fullname
                            "bio": None,
                            "website": None,
                            "social_media": None,
                            "created_at": None,
                            "_source": source["name"],
                            "_source_url": source["url"],
                            "_partial": True,
                            "_hf_type": "org",  # We know it's an org
                            "_avatar_url": None,  # HF doesn't provide org avatar in members
                            "_member_count": len(members_data),
                        }
                        logger.info(
                            f"Fallback org profile SUCCESS: {username} from {source['name']} ({len(members_data)} members)"
                        )
                        return profile_data

                    # Both failed
                    logger.debug(f"HF user/org not found: {username}")
                    continue

                case "kohakuhub":
                    # Other KohakuHub instances use /profile
                    kohaku_path = f"/api/users/{username}/profile"
                    response = await client.get(kohaku_path, "model")

                    if response.status_code == 200:
                        profile_data = response.json()
                        profile_data["_source"] = source["name"]
                        profile_data["_source_url"] = source["url"]
                        logger.info(
                            f"Fallback user profile SUCCESS: {username} from {source['name']}"
                        )
                        return profile_data

                    elif not should_retry_source(response):
                        return None

                case _:
                    continue

        except Exception as e:
            logger.warning(f"Fallback user profile failed for {source['name']}: {e}")
            continue

    return None


async def try_fallback_user_avatar(
    username: str, user_tokens: dict[str, str] | None = None
) -> Optional[bytes]:
    """Try to get user avatar from fallback sources.

    For HuggingFace: Get avatar URL from overview, then download it
    For KohakuHub: Call /api/users/{username}/avatar directly

    Args:
        username: Username to lookup
        user_tokens: User-provided external tokens (overrides admin tokens)

    Returns:
        Avatar image bytes (JPEG) or None if not found
    """
    sources = get_enabled_sources(
        namespace="", user_tokens=user_tokens
    )  # Global sources only

    if not sources:
        return None

    for source in sources:
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )

            match source["source_type"]:
                case "huggingface":
                    # Get avatar URL from user overview
                    user_path = f"/api/users/{username}/overview"
                    user_response = await client.get(user_path, "model")

                    if 200 <= user_response.status_code < 400:
                        hf_data = user_response.json()
                        avatar_url = hf_data.get("avatarUrl")

                        if avatar_url:
                            # Download avatar image
                            import httpx

                            async with httpx.AsyncClient(timeout=30.0) as http_client:
                                avatar_response = await http_client.get(avatar_url)
                                if avatar_response.status_code == 200:
                                    logger.info(
                                        f"Fallback user avatar SUCCESS: {username} from {source['name']}"
                                    )
                                    return avatar_response.content

                    logger.debug(f"HF user avatar not found: {username}")
                    continue

                case "kohakuhub":
                    # Other KohakuHub instances - call avatar endpoint directly
                    avatar_path = f"/api/users/{username}/avatar"
                    response = await client.get(avatar_path, "model")

                    if response.status_code == 200:
                        logger.info(
                            f"Fallback user avatar SUCCESS: {username} from {source['name']}"
                        )
                        return response.content

                    elif not should_retry_source(response):
                        return None

                case _:
                    continue

        except Exception as e:
            logger.warning(f"Fallback user avatar failed for {source['name']}: {e}")
            continue

    return None


async def try_fallback_org_avatar(
    org_name: str, user_tokens: dict[str, str] | None = None
) -> Optional[bytes]:
    """Try to get organization avatar from fallback sources.

    For KohakuHub: Call /api/organizations/{org_name}/avatar directly
    For HuggingFace: Organizations don't have avatars in the API

    Args:
        org_name: Organization name to lookup
        user_tokens: User-provided external tokens (overrides admin tokens)

    Returns:
        Avatar image bytes (JPEG) or None if not found
    """
    sources = get_enabled_sources(
        namespace="", user_tokens=user_tokens
    )  # Global sources only

    if not sources:
        return None

    for source in sources:
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )

            match source["source_type"]:
                case "kohakuhub":
                    # Other KohakuHub instances - call avatar endpoint directly
                    avatar_path = f"/api/organizations/{org_name}/avatar"
                    response = await client.get(avatar_path, "model")

                    if response.status_code == 200:
                        logger.info(
                            f"Fallback org avatar SUCCESS: {org_name} from {source['name']}"
                        )
                        return response.content

                    elif not should_retry_source(response):
                        return None

                case "huggingface":
                    # HuggingFace doesn't provide org avatars via API
                    continue

                case _:
                    continue

        except Exception as e:
            logger.warning(f"Fallback org avatar failed for {source['name']}: {e}")
            continue

    return None


async def try_fallback_user_repos(
    username: str, user_tokens: dict[str, str] | None = None
) -> Optional[dict]:
    """Try to get user repositories from fallback sources.

    Args:
        username: Username to lookup
        user_tokens: User-provided external tokens (overrides admin tokens)

    Returns:
        Repos dict with models/datasets/spaces or None if not found
    """
    sources = get_enabled_sources(namespace="", user_tokens=user_tokens)

    if not sources:
        return None

    for source in sources:
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )

            match source["source_type"]:
                case "huggingface":
                    # HF doesn't have single repos endpoint, query each type
                    models_path = f"/api/models?author={username}&limit=100"
                    datasets_path = f"/api/datasets?author={username}&limit=100"
                    spaces_path = f"/api/spaces?author={username}&limit=100"

                    # Fetch concurrently
                    models_task = client.get(models_path, "model")
                    datasets_task = client.get(datasets_path, "dataset")
                    spaces_task = client.get(spaces_path, "space")

                    models_resp, datasets_resp, spaces_resp = await asyncio.gather(
                        models_task, datasets_task, spaces_task, return_exceptions=True
                    )

                    result = {"models": [], "datasets": [], "spaces": []}

                    # Parse models
                    if (
                        not isinstance(models_resp, Exception)
                        and models_resp.status_code == 200
                    ):
                        result["models"] = models_resp.json()

                    # Parse datasets
                    if (
                        not isinstance(datasets_resp, Exception)
                        and datasets_resp.status_code == 200
                    ):
                        result["datasets"] = datasets_resp.json()

                    # Parse spaces
                    if (
                        not isinstance(spaces_resp, Exception)
                        and spaces_resp.status_code == 200
                    ):
                        result["spaces"] = spaces_resp.json()

                    # Add source tags to all repos
                    for repo_list in [
                        result["models"],
                        result["datasets"],
                        result["spaces"],
                    ]:
                        for repo in repo_list:
                            if isinstance(repo, dict):
                                repo["_source"] = source["name"]
                                repo["_source_url"] = source["url"]

                    logger.info(
                        f"Fallback user repos SUCCESS: {username} from {source['name']}"
                    )
                    return result

                case "kohakuhub":
                    # Use single endpoint
                    repos_path = f"/api/users/{username}/repos"
                    response = await client.get(repos_path, "model")

                    if response.status_code == 200:
                        data = response.json()

                        # Add source tags
                        for repo_list in [
                            data.get("models", []),
                            data.get("datasets", []),
                            data.get("spaces", []),
                        ]:
                            for repo in repo_list:
                                if isinstance(repo, dict):
                                    repo["_source"] = source["name"]
                                    repo["_source_url"] = source["url"]

                        logger.info(
                            f"Fallback user repos SUCCESS: {username} from {source['name']}"
                        )
                        return data

                    elif not should_retry_source(response):
                        return None

                case _:
                    continue

        except Exception as e:
            logger.warning(f"Fallback user repos failed for {source['name']}: {e}")
            continue

    return None
