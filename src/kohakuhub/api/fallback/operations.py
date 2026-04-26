"""Fallback operations for different endpoint types."""

import asyncio
from typing import Optional
from urllib.parse import urljoin

import httpx
from fastapi.responses import JSONResponse, RedirectResponse, Response

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger
from kohakuhub.api.fallback.cache import get_cache
from kohakuhub.api.fallback.client import FallbackClient
from kohakuhub.api.fallback.config import get_enabled_sources
from kohakuhub.api.fallback.utils import (
    add_source_headers,
    build_fallback_attempt,
    build_aggregate_failure_response,
    extract_error_message,
    is_not_found_error,
    should_retry_source,
    strip_xet_response_headers,
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

    # Per-source attempts accumulated across the loop. If every source
    # fails, the aggregated JSON body exposes this list under
    # `body.sources` so the client can tell which sources were asked,
    # what each one answered, and pick the right remediation (token,
    # retry, move on). See src/kohakuhub/api/fallback/utils.py for the
    # status-priority + HF-compatible X-Error-Code contract.
    attempts: list[dict] = []

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
                    # Rewrite any relative Location against the upstream
                    # request URL so clients following a 3xx hit the
                    # upstream (e.g. huggingface.co) instead of KohakuHub
                    # itself — HF's /api/resolve-cache/... path lives only
                    # on the HF origin.
                    resp_headers = dict(response.headers)
                    location = resp_headers.get("location") or resp_headers.get(
                        "Location"
                    )
                    if location:
                        upstream_url = str(response.request.url)
                        absolute_location = urljoin(upstream_url, location)
                        for k in list(resp_headers.keys()):
                            if k.lower() == "location":
                                resp_headers.pop(k, None)
                        resp_headers["location"] = absolute_location

                    # For non-LFS 3xx redirects (no X-Linked-Size), HF's 307
                    # Content-Length is the redirect body length (~278B),
                    # not the file size. Without X-Linked-Size the hf_hub
                    # client takes that bogus value as expected_size and
                    # fails its post-download consistency check
                    # (observed in imgutils' get_wd14_tags on
                    # selected_tags.csv). One extra HEAD to the rewritten
                    # Location picks up the real Content-Length / ETag.
                    # LFS files already carry X-Linked-Size; hf_hub prefers
                    # it over Content-Length so we skip the follow.
                    if (
                        300 <= response.status_code < 400
                        and location
                        and not any(
                            k.lower() == "x-linked-size" for k in resp_headers
                        )
                    ):
                        try:
                            async with httpx.AsyncClient(
                                timeout=client.timeout
                            ) as hc:
                                # `identity` asks HF not to gzip the
                                # (empty) HEAD body; otherwise httpx's
                                # auto-decoding strips Content-Length from
                                # the response and we lose the size we
                                # came here to fetch.
                                extra_headers = {"Accept-Encoding": "identity"}
                                if client.token:
                                    extra_headers["Authorization"] = (
                                        f"Bearer {client.token}"
                                    )
                                follow_resp = await hc.head(
                                    resp_headers["location"],
                                    headers=extra_headers,
                                    follow_redirects=False,
                                )
                            for k in [
                                k for k in list(resp_headers)
                                if k.lower() in ("content-length", "etag")
                            ]:
                                resp_headers.pop(k)
                            for k, v in follow_resp.headers.items():
                                if k.lower() in ("content-length", "etag"):
                                    resp_headers[k] = v
                        except httpx.HTTPError:
                            # Extra HEAD failed — return what we have; no
                            # worse than the original PR#21 behavior.
                            pass

                    strip_xet_response_headers(resp_headers)
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

                        strip_xet_response_headers(resp_headers)
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
                        # GET failed, try next source. Log the attempt so
                        # the aggregate response can explain what each
                        # source actually answered.
                        logger.warning(
                            f"GET request failed for {source['name']}: {get_response.status_code}"
                        )
                        attempts.append(
                            build_fallback_attempt(source, response=get_response)
                        )
                        continue

            # Non-success HEAD response. Record and continue to the next
            # source: a mirror that does not gate (or that simply has
            # the artifact when the first source does not) can still
            # serve the request. The old short-circuit on 4xx lost the
            # status + body completely and blamed the local 404 for
            # what was really an upstream auth failure (issue tied to
            # PR#28: gated repos surfacing as RepoNotFound).
            else:
                logger.warning(
                    f"Fallback attempt at {source['name']}: HTTP {response.status_code}"
                )
                attempts.append(build_fallback_attempt(source, response=response))
                continue

        except httpx.TimeoutException as e:
            logger.warning(f"Fallback source {source['name']} timed out")
            attempts.append(build_fallback_attempt(source, timeout=e))
            continue

        except Exception as e:
            logger.warning(f"Fallback source {source['name']} failed: {e}")
            attempts.append(build_fallback_attempt(source, network=e))
            continue

    # Every source produced a non-success response. Build an aggregated
    # error with HF-compatible X-Error-Code (see
    # build_aggregate_failure_response for the status-priority rules and
    # the reason we align with huggingface_hub's `hf_raise_for_status`
    # classification) and let the caller return it unchanged — the
    # `with_repo_fallback` decorator passes non-None results through, so
    # the aggregated 4xx/5xx bubbles up to the client instead of
    # collapsing to the local "RepoNotFound".
    # Reaching here means every enabled source produced a non-success
    # outcome (every branch of the loop that does not `return` also
    # `attempts.append(...)`), so the attempts list is always non-empty
    # at this point. The early `if not sources: return None` above
    # already handled the zero-source case.
    logger.debug(
        f"Fallback MISS: aggregating {len(attempts)} source failure(s) "
        f"for {repo_type}/{namespace}/{name}"
    )
    return build_aggregate_failure_response(attempts)


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

    # Every non-2xx source probe becomes an attempt dict; if we exit
    # the loop without a success, aggregate the attempts into a
    # classified JSONResponse (same contract as try_fallback_resolve,
    # but with `scope="repo"` so all-404 maps to RepoNotFound rather
    # than EntryNotFound — info is a repo-level operation).
    attempts: list[dict] = []

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

            logger.warning(
                f"Fallback info attempt at {source['name']}: HTTP {response.status_code}"
            )
            attempts.append(build_fallback_attempt(source, response=response))

        except httpx.TimeoutException as e:
            logger.warning(f"Fallback info timed out at {source['name']}")
            attempts.append(build_fallback_attempt(source, timeout=e))
            continue
        except Exception as e:
            logger.warning(f"Fallback info failed for {source['name']}: {e}")
            attempts.append(build_fallback_attempt(source, network=e))
            continue

    if not attempts:
        return None
    logger.debug(
        f"Fallback info MISS: aggregating {len(attempts)} source failure(s) "
        f"for {repo_type}/{namespace}/{name}"
    )
    return build_aggregate_failure_response(attempts, scope="repo")


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

    # Tree is a repo-level operation: `scope="repo"` on all-404 so
    # hf_hub_download / HfApi.list_repo_files raise
    # RepositoryNotFoundError (not EntryNotFoundError), matching what
    # HF itself returns for a missing model.
    attempts: list[dict] = []

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

            logger.warning(
                f"Fallback tree attempt at {source['name']}: HTTP {response.status_code}"
            )
            attempts.append(build_fallback_attempt(source, response=response))

        except httpx.TimeoutException as e:
            logger.warning(f"Fallback tree timed out at {source['name']}")
            attempts.append(build_fallback_attempt(source, timeout=e))
            continue
        except Exception as e:
            logger.warning(f"Fallback tree failed for {source['name']}: {e}")
            attempts.append(build_fallback_attempt(source, network=e))
            continue

    if not attempts:
        return None
    logger.debug(
        f"Fallback tree MISS: aggregating {len(attempts)} source failure(s) "
        f"for {repo_type}/{namespace}/{name}/tree"
    )
    return build_aggregate_failure_response(attempts, scope="repo")


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

    # paths-info is per-file (it answers "does file X exist at
    # revision R"), so all-404 stays scope="file" → EntryNotFound.
    attempts: list[dict] = []

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

            logger.warning(
                f"Fallback paths-info attempt at {source['name']}: HTTP {response.status_code}"
            )
            attempts.append(build_fallback_attempt(source, response=response))

        except httpx.TimeoutException as e:
            logger.warning(f"Fallback paths-info timed out at {source['name']}")
            attempts.append(build_fallback_attempt(source, timeout=e))
            continue
        except Exception as e:
            logger.warning(f"Fallback paths-info failed for {source['name']}: {e}")
            attempts.append(build_fallback_attempt(source, network=e))
            continue

    if not attempts:
        return None
    logger.debug(
        f"Fallback paths-info MISS: aggregating {len(attempts)} source failure(s) "
        f"for {repo_type}/{namespace}/{name}"
    )
    return build_aggregate_failure_response(attempts, scope="file")


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
