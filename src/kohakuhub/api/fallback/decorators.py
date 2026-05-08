"""Decorators for adding fallback functionality to endpoints."""

import asyncio
import inspect
import time
from functools import wraps
from typing import Any, Literal, Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger
from kohakuhub.db_operations import (
    get_merged_external_tokens,
    get_organization,
    get_user_by_username,
)
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
from kohakuhub.api.fallback.trace import (
    PROBE_ID_HEADER,
    encode_trace_header,
    inject_trace_cookie,
    inject_trace_header,
    inject_trace_into_exception_headers,
    record_local_hop,
    start_trace,
    X_CHAIN_TRACE,
    sanitize_probe_id,
)

logger = get_logger("FALLBACK_DEC")

OperationType = Literal["resolve", "tree", "info", "revision", "paths_info"]
UserOperationType = Literal["profile", "repos", "avatar"]


def _resolve_repo_read_denied_error() -> type:
    """Resolve ``RepoReadDeniedError`` lazily.

    The test harness in ``test/kohakuhub/support/bootstrap.py`` clears
    every ``kohakuhub.*`` module from ``sys.modules`` and re-imports
    them at session start, then returns a fresh ``app`` instance. Tests
    that exercise the wrapper after the reload may end up with this
    decorator module bound to a *previous* ``RepoReadDeniedError``
    class object — different ``id()`` from the one the live
    ``check_repo_read_permission`` raises. ``isinstance()`` then
    silently returns False even on legitimate raises, and the generic
    ``except Exception`` branch below converts the masked-private-repo
    raise into a 500. The chain-tester probe (``probe_local.py``) hit
    the same gotcha and uses the same lazy-resolve trick.

    Re-resolving from ``sys.modules`` on every call binds whichever
    copy is currently live, so the ``except`` clause matches at
    request time regardless of any reload that happened between
    decorator import and the route call.
    """
    from kohakuhub.auth.permissions import RepoReadDeniedError

    return RepoReadDeniedError


def _repo_sort_key(item: dict) -> tuple[str, str, str]:
    return (
        item.get("lastModified") or "",
        item.get("createdAt") or "",
        item.get("id") or "",
    )


def _classify_local_response(local_result: Response) -> tuple[str, Optional[str], Optional[str]]:
    """Map a local-handler ``Response`` to ``(decision, x_error_code, x_error_message)``.

    Decision values are the same string codes ``trace.record_local_hop``
    expects:

    - ``LOCAL_HIT``: 2xx/3xx — local served the request.
    - ``LOCAL_FILTERED``: 404 + ``X-Error-Code`` in
      ``{EntryNotFound, RevisionNotFound}`` — local repo exists, the
      entry/revision does not. Per the strict-consistency contract
      we serve the local 404 verbatim and never fall through.
    - ``LOCAL_MISS``: 404 (no error code, or RepoNotFound, etc.) —
      this is the only decision that triggers the fallback chain.
    - ``LOCAL_OTHER_ERROR``: any other 4xx/5xx — local served an
      error that's not a 404; we surface it without consulting
      fallback (matches the existing behaviour where a non-404
      Response just returned directly).
    """
    status = getattr(local_result, "status_code", 200)
    x_code = local_result.headers.get("x-error-code") if local_result.headers else None
    x_msg = local_result.headers.get("x-error-message") if local_result.headers else None
    if 200 <= status < 400:
        return "LOCAL_HIT", x_code, x_msg
    if status == 404:
        if x_code in ("EntryNotFound", "RevisionNotFound"):
            return "LOCAL_FILTERED", x_code, x_msg
        return "LOCAL_MISS", x_code, x_msg
    return "LOCAL_OTHER_ERROR", x_code, x_msg


def _classify_local_exception(exc: HTTPException) -> tuple[str, Optional[str], Optional[str]]:
    """Map a local-handler ``HTTPException`` to the same shape as
    ``_classify_local_response``.

    Local handlers in this codebase (notably ``_get_file_metadata`` in
    ``api/files.py``) attach ``X-Error-Code`` to ``HTTPException(headers=...)``;
    we read it back with case-insensitive lookup so the gating logic
    matches what the Response branch sees.
    """
    headers = exc.headers or {}
    x_code = headers.get("X-Error-Code") or headers.get("x-error-code")
    x_msg = headers.get("X-Error-Message") or headers.get("x-error-message")
    if exc.status_code != 404:
        return "LOCAL_OTHER_ERROR", x_code, x_msg
    if x_code in ("EntryNotFound", "RevisionNotFound"):
        return "LOCAL_FILTERED", x_code, x_msg
    return "LOCAL_MISS", x_code, x_msg


def _attach_trace_to_result(
    result: Any,
    hops: list[dict],
    probe_id: Optional[str] = None,
) -> Any:
    """Return ``result`` with ``X-Chain-Trace`` + cookie injected — only
    when the caller is the chain tester (``X-Khub-Probe-Id`` header on
    the inbound request).

    Auth gating rationale: the encoded hop list reveals every fallback
    source's name + URL, which is operator-internal topology data. An
    earlier draft of this PR emitted the header on every fallback-
    decorated response; that was a regression vs the pre-#78 wire
    because anonymous callers could decode it and enumerate the
    operator's mirror config. Now we treat the header as chain-tester
    opt-in: no probe id ⇒ no trace on the wire (the ContextVar is
    still populated for in-process logs / telemetry, just not emitted).

    - ``Response`` (or subclass like ``JSONResponse``) → mutate
      ``response.headers`` to add ``X-Chain-Trace`` + ``Set-Cookie``,
      return as-is.
    - dict / list / etc. → wrap in a fresh ``JSONResponse`` carrying
      both. We use ``jsonable_encoder`` to mirror FastAPI's
      auto-conversion path so non-JSON-native types (datetime, UUID,
      etc.) survive.
    - ``None`` → unchanged (FastAPI emits a 200 with no body; nothing
      to attach).

    No-ops on empty ``hops`` or missing ``probe_id`` so the function
    is safe to call unconditionally at the decorator's exit.

    .. warning::

        The dict / list path wraps the return in ``JSONResponse``
        which **bypasses FastAPI's** ``response_model`` validation.
        None of the current ``with_repo_fallback``-decorated routes
        use ``response_model``, so this is latent. If you ever add
        ``response_model=`` to a fallback-decorated route, audit
        this wrap — you'll lose the schema-coercion side-effect of
        FastAPI's auto-conversion.
    """
    if not hops or not probe_id:
        return result
    if isinstance(result, Response):
        inject_trace_header(result, hops)
        inject_trace_cookie(result, hops, probe_id)
        return result
    if result is None:
        return result
    response = JSONResponse(
        content=jsonable_encoder(result),
        headers={X_CHAIN_TRACE: encode_trace_header(hops)},
    )
    inject_trace_cookie(response, hops, probe_id)
    return response


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

            # Begin chain trace. The list returned here is the same one
            # ``record_*_hop`` will mutate, and the one we encode into
            # ``X-Chain-Trace`` on the way out. ``start_trace`` resets
            # the ContextVar so a previous request's hops can never leak
            # into this one.
            hops = start_trace()

            # Optional per-probe id: the chain tester sends an
            # ``X-Khub-Probe-Id`` header so we can also Set-Cookie the
            # trace under a per-probe name — that's the only pickup
            # channel the SPA has after a redirect-follow round trip
            # (see trace.inject_trace_cookie). Sanitize to RFC-6265
            # cookie-name-safe charset to avoid Set-Cookie injection.
            probe_id = None
            if request and hasattr(request, "headers"):
                try:
                    raw = request.headers.get(PROBE_ID_HEADER) or request.headers.get(
                        PROBE_ID_HEADER.lower()
                    )
                    probe_id = sanitize_probe_id(raw)
                except Exception:  # pragma: no cover — defensive
                    probe_id = None

            is_404 = False
            original_error = None
            original_response = None

            local_t0 = time.monotonic()
            try:
                # Try local first
                local_result = await func(*args, **kwargs)
                local_dt_ms = int((time.monotonic() - local_t0) * 1000)

                # Check if result is a Response object (any FastAPI response type)
                if isinstance(local_result, Response):
                    decision, x_code, x_msg = _classify_local_response(local_result)
                    record_local_hop(
                        decision=decision,
                        status_code=getattr(local_result, "status_code", 200),
                        x_error_code=x_code,
                        x_error_message=x_msg,
                        duration_ms=local_dt_ms,
                    )
                    if decision == "LOCAL_FILTERED":
                        logger.debug(
                            f"Local 404 with X-Error-Code={x_code} "
                            f"for {repo_type}/{namespace}/{name} — local repo "
                            f"exists, returning local response unchanged "
                            f"(no fallback)"
                        )
                        return _attach_trace_to_result(local_result, hops, probe_id)
                    if decision == "LOCAL_MISS":
                        is_404 = True
                        original_response = local_result
                        logger.info(
                            f"Local 404 response for {repo_type}/{namespace}/{name} "
                            f"(X-Error-Code={x_code or 'none'}), trying "
                            f"fallback sources..."
                        )
                    else:
                        # LOCAL_HIT or LOCAL_OTHER_ERROR — surface the
                        # local response unchanged, with trace attached.
                        return _attach_trace_to_result(local_result, hops, probe_id)
                else:
                    # dict / list / None → success path. Record a HIT
                    # (status 200 is the value FastAPI will emit) and
                    # wrap so we can attach the trace header.
                    record_local_hop(
                        decision="LOCAL_HIT",
                        status_code=200,
                        duration_ms=local_dt_ms,
                    )
                    return _attach_trace_to_result(local_result, hops, probe_id)

            except HTTPException as e:
                local_dt_ms = int((time.monotonic() - local_t0) * 1000)
                decision, x_code, x_msg = _classify_local_exception(e)
                record_local_hop(
                    decision=decision,
                    status_code=e.status_code,
                    x_error_code=x_code,
                    x_error_message=x_msg,
                    duration_ms=local_dt_ms,
                )
                if decision != "LOCAL_MISS":
                    # LOCAL_OTHER_ERROR / LOCAL_FILTERED: re-raise with
                    # the trace attached as a header so the chain tester
                    # can still read what just happened. ``HTTPException``
                    # carries a flat ``headers`` mapping, so we copy +
                    # extend.
                    new_headers = inject_trace_into_exception_headers(e.headers, hops, probe_id)
                    raise HTTPException(
                        status_code=e.status_code,
                        detail=e.detail,
                        headers=new_headers,
                    ) from e

                is_404 = True
                original_error = e
                logger.info(
                    f"Local 404 HTTPException for {repo_type}/{namespace}/{name} "
                    f"(X-Error-Code={x_code or 'none'}), trying "
                    f"fallback sources..."
                )

            except (asyncio.CancelledError, GeneratorExit):
                # Cooperative cancellation must propagate cleanly so the
                # ASGI event loop can tear down the request — never
                # swallow these. No trace recorded (the request itself
                # is being aborted; the chain tester won't see this
                # response anyway).
                raise

            except _resolve_repo_read_denied_error():
                # ``RepoReadDeniedError`` is the privacy-preserving raise
                # from ``check_repo_read_permission`` for masked private
                # repos (post-#76). It must reach the FastAPI global
                # handler in ``main.py`` to be converted to
                # ``404 + X-Error-Code: RepoNotFound`` with an empty
                # body — getting absorbed into the ``except Exception``
                # branch below would degrade it into a 500 and silently
                # break the wire-shape contract. Skip the chain probe
                # entirely (we don't want to leak the existence of a
                # private local repo by lighting up upstream traffic
                # for it) and re-raise so the global handler runs.
                #
                # The class is resolved lazily because the test harness
                # reloads ``kohakuhub.auth.permissions`` between
                # sessions; a module-level import would freeze a stale
                # class identity and ``isinstance()`` would silently
                # miss legitimate raises (same gotcha the
                # ``fallback/probe_local.py`` lazy import documents).
                raise

            except Exception as e:
                # Generic exception from the local handler (LakeFS
                # ``httpx.ReadTimeout``, peewee ``OperationalError``,
                # AssertionError from a stale dev branch, etc.).
                # Without this branch the exception propagates before
                # ``record_local_hop`` runs, so the chain tester sees
                # nothing useful and the production wire surfaces an
                # unannotated 500 — defeating the universal-debug
                # claim of the trace channel. Record the hop as
                # LOCAL_OTHER_ERROR and re-raise as a 500 carrying
                # the trace (gated on probe_id like every other
                # emission path; anonymous callers still see the
                # original exception's effect via FastAPI's default
                # 500 response).
                local_dt_ms = int((time.monotonic() - local_t0) * 1000)
                err_msg = f"{type(e).__name__}: {e}"
                logger.exception(
                    f"Local handler raised {type(e).__name__} for "
                    f"{repo_type}/{namespace}/{name}"
                )
                record_local_hop(
                    decision="LOCAL_OTHER_ERROR",
                    status_code=500,
                    x_error_code=None,
                    x_error_message=err_msg,
                    duration_ms=local_dt_ms,
                    error=err_msg,
                )
                new_headers = inject_trace_into_exception_headers(
                    None, hops, probe_id,
                )
                raise HTTPException(
                    status_code=500,
                    detail={"error": "Internal server error in local handler"},
                    headers=new_headers,
                ) from e

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
                        # Plan A: forward client request headers so
                        # Range / If-* survive end-to-end. The actual
                        # whitelist is enforced inside
                        # ``try_fallback_resolve`` (defense in depth) —
                        # passing raw ``request.headers`` is safe.
                        result = await try_fallback_resolve(
                            repo_type,
                            namespace,
                            name,
                            revision,
                            path,
                            user_tokens=user_tokens,
                            method=method,
                            user=user,
                            client_headers=(
                                request.headers if request is not None else None
                            ),
                        )

                    case "tree":
                        revision = kwargs.get("revision", "main")
                        path = kwargs.get("path", "")
                        recursive = kwargs.get("recursive", False)
                        expand = kwargs.get("expand", False)
                        limit = kwargs.get("limit")
                        cursor = kwargs.get("cursor")
                        result = await try_fallback_tree(
                            repo_type,
                            namespace,
                            name,
                            revision,
                            path,
                            recursive=recursive,
                            expand=expand,
                            limit=limit,
                            cursor=cursor,
                            user_tokens=user_tokens,
                            user=user,
                        )

                    case "info" | "revision":
                        result = await try_fallback_info(
                            repo_type, namespace, name,
                            user_tokens=user_tokens, user=user,
                        )

                    case "paths_info":
                        # For paths-info, extract paths and revision from kwargs
                        revision = kwargs.get("revision", "main")
                        paths = kwargs.get("paths", [])
                        expand = kwargs.get("expand", False)
                        result = await try_fallback_paths_info(
                            repo_type,
                            namespace,
                            name,
                            revision,
                            paths,
                            expand=expand,
                            user_tokens=user_tokens,
                            user=user,
                        )

                    case _:
                        logger.warning(f"Unknown fallback operation: {operation}")
                        result = None

                if result:
                    logger.success(
                        f"Fallback SUCCESS for {operation}: {repo_type}/{namespace}/{name}"
                    )
                    return _attach_trace_to_result(result, hops, probe_id)
                else:
                    # Not found in any source
                    logger.debug(
                        f"Fallback MISS for {operation}: {repo_type}/{namespace}/{name}"
                    )
                    # Return original 404 response or raise original exception
                    if original_error:
                        new_headers = inject_trace_into_exception_headers(
                            original_error.headers, hops, probe_id
                        )
                        raise HTTPException(
                            status_code=original_error.status_code,
                            detail=original_error.detail,
                            headers=new_headers,
                        ) from original_error
                    else:
                        # Return the original 404 JSONResponse with trace
                        return _attach_trace_to_result(original_response, hops, probe_id)

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

            # Strict-consistency rule for the user/org family
            # (parallel to the X-Error-Code gating in
            # ``with_repo_fallback``): if the namespace exists locally
            # — as either a user or an organization — this khub
            # instance owns it. Every feature of that namespace
            # (profile, avatar, repos, etc.) is answered locally,
            # regardless of whether the specific feature returns a
            # 200 or a 404 (e.g. user has no avatar uploaded).
            #
            # Without this, a local user with no avatar would fall
            # through to a *same-named* HF user's avatar — pulling
            # an unrelated person's image into our user's profile.
            # Since the user/org local handlers raise raw
            # HTTPException(404) and don't carry a discriminating
            # X-Error-Code, the cleanest gate is a DB existence
            # check up front.
            local_user = get_user_by_username(username)
            local_org = get_organization(username)
            if local_user is not None or local_org is not None:
                logger.debug(
                    f"Namespace {username!r} exists locally — local handler "
                    f"is authoritative for {operation!r}, no fallback"
                )
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
