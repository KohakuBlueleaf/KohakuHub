"""Fallback operations for different endpoint types."""

import asyncio
import time
from collections import defaultdict
from typing import Optional
from urllib.parse import urljoin

import httpx
from fastapi.responses import JSONResponse, RedirectResponse, Response

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger
from kohakuhub.api.fallback.cache import compute_tokens_hash, get_cache
from kohakuhub.api.fallback.client import FallbackClient
from kohakuhub.api.fallback.config import get_enabled_sources
from kohakuhub.api.fallback.trace import record_source_hop
from kohakuhub.api.fallback.utils import (
    add_source_headers,
    apply_resolve_head_postprocess,
    build_fallback_attempt,
    build_aggregate_failure_response,
    classify_upstream,
    extract_error_message,
    FallbackDecision,
    is_not_found_error,
    should_retry_source,
    strip_xet_response_headers,
)


def _resolve_user_id(user) -> Optional[int]:
    """Extract a stable user_id key from a (possibly None) User object."""
    if user is None:
        return None
    return getattr(user, "id", None)


# Plan A: only these client request headers are forwarded upstream on
# resolve probes. Authorization / Cookie / Proxy-Authorization are
# deliberately excluded — the only credential allowed upstream is the
# admin-configured source token attached by ``FallbackClient`` itself.
# Accept-Encoding is excluded because httpx auto-decompresses responses,
# which would corrupt the redirect-passthrough contract.
_FORWARDABLE_RESOLVE_HEADERS: tuple[str, ...] = (
    "range",
    "if-match",
    "if-none-match",
    "if-modified-since",
    "if-unmodified-since",
    "if-range",
)


def _filter_client_headers(headers) -> dict[str, str]:
    """Return a fresh dict containing only the whitelisted resolve headers.

    Defense-in-depth filter: even if a caller forgets to pre-strip
    Authorization / Cookie before invoking ``try_fallback_resolve``,
    this guard catches it. Header name comparison is case-insensitive;
    values are forwarded with canonical Title-Case names so logs read
    naturally upstream-side.
    """
    if not headers:
        return {}
    if hasattr(headers, "items"):
        items = headers.items()
    else:
        items = headers
    out: dict[str, str] = {}
    allowed = set(_FORWARDABLE_RESOLVE_HEADERS)
    for k, v in items:
        if not k or v is None:
            continue
        lower = k.lower()
        if lower in allowed:
            out[lower.title()] = v
    return out


def _propagate_upstream_response(
    response: httpx.Response, source: dict
) -> Response:
    """Forward an upstream's BIND_AND_PROPAGATE response verbatim.

    Used when ``classify_upstream`` returns ``BIND_AND_PROPAGATE`` —
    i.e. EntryNotFound, RevisionNotFound, or "Access to this resource
    is disabled.". The repo lives at this source; the upstream's 4xx is
    the right answer for the request, and we forward the body and
    ``X-Error-Code`` / ``X-Error-Message`` headers so a hf_hub client
    raises the right exception (``EntryNotFoundError`` /
    ``RevisionNotFoundError`` / ``DisabledRepoError``).

    Header sanitization mirrors the success path (strip
    encoding/length/transfer headers httpx already decompressed, drop
    Xet hints that would push a hf_hub client onto an endpoint we don't
    serve, attach ``X-Source*`` for telemetry).
    """
    headers = dict(response.headers)
    # httpx already decoded the body, so the original
    # Content-Length/Encoding/Transfer-Encoding values would mislead
    # the next hop. Same hygiene as the 200 success path.
    headers.pop("content-encoding", None)
    headers.pop("content-length", None)
    headers.pop("transfer-encoding", None)
    strip_xet_response_headers(headers)
    headers.update(add_source_headers(response, source["name"], source["url"]))
    return Response(
        status_code=response.status_code,
        content=response.content,
        headers=headers,
    )


def _propagate_upstream_redirect(
    response: httpx.Response, source: dict
) -> Response:
    """Forward an upstream resolve-GET 30x to the client without buffering.

    Plan A: bytes never traverse the backend on the resolve GET path.
    ``client.get`` is invoked with ``follow_redirects=False``, so when the
    upstream resolves to a CDN / presigned URL via 301/302/303/307/308
    we hand that ``Location`` back to the client and the actual byte
    transfer is client→CDN, mirroring the local ``resolve_file_get``
    presigned-S3 redirect flow.

    Relative ``Location`` (e.g. HF's ``/api/resolve-cache/...`` 307) is
    rewritten to absolute against the upstream request URL — same fix
    the HEAD postprocess applies — so the client follows it back to the
    upstream, NOT back to KohakuHub which doesn't serve that path.
    """
    headers: dict[str, str] = {}
    location = response.headers.get("location")
    if not location:
        # Malformed 30x without Location — fall back to verbatim
        # propagation so the caller still sees the upstream status.
        return _propagate_upstream_response(response, source)
    # Rewrite relative Location to absolute against the upstream URL.
    # urljoin is a no-op when ``location`` is already absolute (the LFS
    # ``cas-bridge.xethub.hf.co`` case), so this is safe for both
    # patterns. Without this, hf_hub would walk the relative path back
    # to its own ``endpoint`` (= our backend) and 404 because we don't
    # serve /api/resolve-cache/.
    upstream_url = str(response.request.url)
    absolute_location = urljoin(upstream_url, location)
    # Preserve the metadata huggingface_hub clients read off the redirect
    # response (these are the same headers the HEAD-postprocess path
    # surfaces; keeping GET symmetric ensures clients see consistent
    # ETag / size info regardless of which method they used).
    for h in ("etag", "x-repo-commit", "x-linked-etag", "x-linked-size"):
        v = response.headers.get(h)
        if v:
            headers[h] = v
    headers["location"] = absolute_location
    # Presigned redirects expire — never let an intermediary cache a
    # response whose target URL has a baked-in deadline.
    headers["cache-control"] = "no-store"
    # NOTE: the explicit four-key whitelist above (etag / x-repo-commit
    # / x-linked-etag / x-linked-size) plus location / cache-control is
    # the actual Xet-leak defense — none of those keys can collide with
    # ``x-xet-*``, so a defensive ``strip_xet_response_headers`` here
    # would be a guaranteed no-op. The contract is enforced by the
    # whitelist; ``test_try_fallback_resolve_get_redirect_drops_xet_headers_from_upstream``
    # locks it at the response surface.
    headers.update(add_source_headers(response, source["name"], source["url"]))
    return Response(
        status_code=response.status_code,
        content=b"",
        headers=headers,
    )


logger = get_logger("FALLBACK_OPS")


# Per-loop, per-repo binding lock registry. Used to serialize
# concurrent first-binding attempts for the same
# ``(repo_type, namespace, name)`` so two cache-miss callers cannot
# independently scan the chain and bind to different sources due to
# per-source latency variation — that's the strict-consistency
# property required by the user-facing guarantee "same auth + same
# external state ⇒ same source".
#
# Why ``id(loop)`` is part of the key: ``asyncio.Lock`` instances
# bind to the event loop that runs their first ``acquire()``. In
# normal operation KohakuHub has exactly one loop (uvicorn's), so
# all locks live in the same loop slot — no overhead. In tests
# however, the live-server fixture starts and stops uvicorn per
# test, producing a fresh loop each time; reusing a Lock across
# loops raises ``RuntimeError: bound to a different event loop``.
# Keying by loop id keeps each loop's locks isolated and lets the
# dict carry stale entries from torn-down loops without harm
# (they're never looked up again). Tests can call
# ``_reset_binding_locks_for_tests()`` to drop accumulated entries.
_BINDING_LOCKS: defaultdict[
    tuple[int, str, str, str], asyncio.Lock
] = defaultdict(asyncio.Lock)


def _binding_lock(repo_type: str, namespace: str, name: str) -> asyncio.Lock:
    """Return the per-loop, per-repo binding lock."""
    loop_id = id(asyncio.get_running_loop())
    return _BINDING_LOCKS[(loop_id, repo_type, namespace, name)]


def _reset_binding_locks_for_tests() -> None:
    """Clear the binding-lock registry. Test-only hook."""
    _BINDING_LOCKS.clear()


async def _run_cached_then_chain(
    repo_type: str,
    namespace: str,
    name: str,
    user_id: Optional[int],
    tokens_hash: str,
    sources: list[dict],
    cache,
    attempts: list[dict],
    attempt_fn,
    aggregate_scope: str,
):
    """Orchestrate cache-authoritative probe + first-binding under lock.

    Strict-consistency rules (the user-facing guarantee for #77):

    1. **TTL-window stickiness.** Within the cache TTL window, every
       call for a given ``(user_id, tokens_hash, repo_type, namespace,
       name)`` is routed to the same source. If that source's response
       classifies as ``TRY_NEXT_SOURCE`` (5xx, transient auth, etc.),
       we surface the error to the caller **without invalidating the
       cache**. Rationale: invalidating + rebinding to a sibling
       source under transient bound-source failure produces
       cross-source mixing across calls — exactly the inconsistency
       #77 fixes. The client can retry; retries within TTL hit the
       same source.

    2. **Concurrent-binding lock — narrow critical section.** When
       the cache misses and the chain probe is needed, concurrent
       callers serialize on a per-repo ``asyncio.Lock``. The first
       holder walks the chain and writes the cache; subsequent
       holders re-check the cache after acquiring the lock and, if
       they find a binding, **return the decision and call
       ``attempt_fn`` AFTER releasing the lock** — so post-recheck
       waiters fan out in parallel rather than serializing their
       bound-source calls through the lock (issue #85).

       The lock's only job is the first-bind race. Once the cache is
       populated, the lock is released and never blocks I/O. The
       post-lock recheck is pure decision: read cache, return tuple.

    2a. **Lock supervisor (issue #85, option (c)).** The locked
       region is wrapped in
       ``asyncio.wait_for(timeout=fallback.timeout_seconds * (len(sources)+1))``
       so a wedged ``attempt_fn`` (e.g. an httpx call that ignores
       its own timeout under a misbehaving proxy) cannot hold the
       lock forever. Cancellation propagates through ``async with
       binding_lock:`` which guarantees the lock is released. On
       supervisor timeout the caller receives a chain-exhausted
       aggregate response; subsequent same-repo callers see a clean
       lock and retry. Strict consistency is a *safety* invariant
       conditional on stable upstream behaviour — it does not
       require unbounded blocking under wedge.

    3. **Orphaned-cache invalidation only.** The single case that
       *does* invalidate the cache is when the cached source URL is
       no longer in the active ``sources`` list (admin removed it
       from config). That isn't a transient failure — it's a
       configuration change.

    4. **Deterministic chain order.** Sources are configured in a
       priority-ordered list; the chain walks them in order and the
       first ``BIND_*`` outcome wins.

    Strict-freshness extension (#79):

    5. **Per-(user, tokens_hash) keying.** Two requests with different
       effective per-source tokens (DB or header-passed) cannot share
       a binding. ``user_id`` and ``tokens_hash`` are part of the
       cache key.

    6. **Generation-counter race protection.** A snapshot of
       ``(global_gen, user_gens[uid], repo_gens[(rt, ns, name)])`` is
       captured before each probe attempt. ``safe_set`` (in the
       attempt callbacks) rejects the cache write if any of the three
       counters has been bumped during the probe — meaning an
       admin/user/repo invalidation event landed concurrently and the
       binding we are about to write may already be stale.
    """
    # Pre-lock cache hit fast-path: avoid taking the binding lock when
    # the entry is already bound. Snapshot generations BEFORE any
    # upstream I/O so safe_set in attempt_fn sees a consistent baseline.
    gens = cache.snapshot(user_id, repo_type, namespace, name)
    cached_entry = cache.get(user_id, tokens_hash, repo_type, namespace, name)
    if cached_entry and cached_entry.get("exists"):
        cached_url = cached_entry["source_url"]
        cached_source = next((s for s in sources if s["url"] == cached_url), None)
        if cached_source:
            logger.debug(
                f"Cache hit: probing {cached_source['name']} only for "
                f"{repo_type}/{namespace}/{name}"
            )
            result = await attempt_fn(cached_source, gens)
            if result is not None:
                return result
            # Strict-consistency rule #1: bound source's TRY_NEXT
            # response surfaces as the caller-visible error WITHOUT
            # invalidating. Within TTL the bound source stays bound.
            logger.debug(
                f"Bound source {cached_source['name']} returned "
                f"TRY_NEXT_SOURCE for {repo_type}/{namespace}/{name}; "
                f"surfacing error without rebinding (TTL still in force)"
            )
            return build_aggregate_failure_response(
                attempts, scope=aggregate_scope
            )
        else:
            # Strict-consistency rule #3: orphaned cache (admin
            # removed the source from config) must invalidate so the
            # chain can find a new home for this repo_id. Per-entry
            # delete (no gen bump) — admin source mutation already
            # bumped global_gen via cache.clear().
            logger.debug(
                f"Cache hit on orphan source url={cached_url} "
                f"(no longer in active config); invalidating + rebinding"
            )
            cache.invalidate(
                user_id, tokens_hash, repo_type, namespace, name
            )

    # Strict-consistency rule #2 + 2a: concurrent-binding lock with
    # narrow critical section + supervisor (issue #85).
    binding_lock = _binding_lock(repo_type, namespace, name)

    # Supervisor budget: per-source timeout × (chain length + 1)
    # gives the binder enough room to walk every source at full
    # httpx timeout, plus one slot of buffer for scheduling and
    # cache I/O. The caller-visible worst case under wedge is one
    # chain timeout — not unbounded — and the lock is released by
    # cancellation either way so subsequent same-repo callers
    # always see a clean lock.
    supervisor_timeout = cfg.fallback.timeout_seconds * (len(sources) + 1)

    async def _decide_under_lock():
        """Run inside the binding lock. Returns one of:

        - ``("cache_hit", source, gens)`` — a concurrent waiter
          bound the repo while we queued. The outer caller invokes
          ``attempt_fn`` against ``source`` AFTER releasing the
          lock so post-recheck waiters fan out in parallel rather
          than serializing through the lock (issue #85's primary
          fix).
        - ``("bound", result, None)`` — we are the first binder;
          we walked the chain under the lock and produced a
          successful result. ``safe_set`` (inside ``attempt_fn``)
          has populated the cache so subsequent same-repo waiters
          will hit the post-recheck branch.
        - ``("exhausted", None, None)`` — chain walked under lock,
          no source bound. Outer caller surfaces aggregate failure.

        I/O happens inside this coroutine ONLY on the chain-walk
        path (first-bind serialization is the lock's actual job).
        Post-recheck cache hit is pure-decision: read cache,
        return tuple, exit.
        """
        async with binding_lock:
            # Re-snapshot under the lock so the chain probe + safe_set
            # see a fresh baseline (generations may have changed while
            # we were waiting on the lock).
            gens_inner = cache.snapshot(user_id, repo_type, namespace, name)
            # Re-check the cache after lock acquisition: another waiter
            # may have already bound this repo while we were queued.
            cached_entry_inner = cache.get(
                user_id, tokens_hash, repo_type, namespace, name
            )
            if cached_entry_inner and cached_entry_inner.get("exists"):
                cached_url_inner = cached_entry_inner["source_url"]
                cached_source_inner = next(
                    (s for s in sources if s["url"] == cached_url_inner),
                    None,
                )
                if cached_source_inner:
                    # Pure decision — DO NOT call attempt_fn here.
                    return ("cache_hit", cached_source_inner, gens_inner)
                # Concurrent waiter bound to a source we don't have
                # in config — extremely rare (admin reconfig race
                # between the binder's ``cache.set`` and the
                # waiter's post-lock cache-recheck); treat as orphan
                # and proceed to a fresh chain.
                cache.invalidate(  # pragma: no cover
                    user_id, tokens_hash, repo_type, namespace, name
                )

            # Fresh chain probe: deterministic priority order, first
            # BIND wins. I/O is under the lock here because
            # first-bind serialization is the lock's actual job —
            # without it, two concurrent first-binders could pick
            # different sources from the chain (the cross-source
            # mixing #75/#77 prevent).
            for source in sources:
                result = await attempt_fn(source, gens_inner)
                if result is not None:
                    return ("bound", result, None)

            return ("exhausted", None, None)

    try:
        decision, payload, gens_used = await asyncio.wait_for(
            _decide_under_lock(), timeout=supervisor_timeout
        )
    except asyncio.TimeoutError:
        # Supervisor fired — locked region exceeded its budget.
        # Cancellation propagated through ``async with binding_lock:``
        # so the lock has been released and subsequent same-repo
        # callers can proceed. Surface a chain-exhausted aggregate
        # (typically empty attempts → 502 UpstreamFailure) to this
        # caller.
        logger.error(
            f"Lock supervisor fired for {repo_type}/{namespace}/{name} "
            f"after {supervisor_timeout}s — locked region took too "
            f"long. Lock released by cancellation; surfacing "
            f"aggregate failure to caller."
        )
        return build_aggregate_failure_response(attempts, scope=aggregate_scope)

    if decision == "cache_hit":
        # Bound source from concurrent waiter; call attempt_fn
        # OUTSIDE the lock so all post-recheck waiters fan out in
        # parallel. This is issue #85's primary liveness fix.
        result = await attempt_fn(payload, gens_used)
        if result is not None:
            return result
        # Strict-consistency rule #1: bound source's TRY_NEXT
        # response surfaces as the caller-visible error WITHOUT
        # invalidating. Within TTL the bound source stays bound.
        return build_aggregate_failure_response(attempts, scope=aggregate_scope)

    if decision == "bound":
        return payload

    # decision == "exhausted"
    if not attempts:  # pragma: no cover
        # Defensive: caller already filtered out empty ``sources``.
        return None
    logger.debug(
        f"Fallback MISS: aggregating {len(attempts)} source failure(s) "
        f"for {repo_type}/{namespace}/{name}"
    )
    return build_aggregate_failure_response(attempts, scope=aggregate_scope)


async def try_fallback_resolve(
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    path: str,
    user_tokens: dict[str, str] | None = None,
    method: str = "GET",
    user=None,
    client_headers: dict[str, str] | None = None,
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
        user: Authenticated user (or None for anonymous). Threaded
            through to the cache key as ``user_id`` for strict
            per-user binding isolation (#79).
        client_headers: Client request headers to forward upstream.
            Filtered through ``_filter_client_headers`` before any
            outbound use, so callers may safely pass the raw
            ``request.headers`` mapping — only Range / If-* survive
            (Authorization / Cookie / Proxy-Authorization /
            Accept-Encoding are dropped here).

    Returns:
        Response (redirect for GET, response with headers for HEAD) or None if not found
    """
    cache = get_cache()
    sources = get_enabled_sources(namespace, user_tokens=user_tokens)

    if not sources:
        logger.debug(f"No fallback sources configured for {namespace}")
        return None

    user_id = _resolve_user_id(user)
    tokens_hash = compute_tokens_hash(user_tokens)

    # Defense-in-depth: drop everything that isn't on the resolve
    # whitelist before it gets anywhere near the upstream chain.
    safe_client_headers = _filter_client_headers(client_headers)

    # Construct KohakuHub path
    kohaku_path = f"/{repo_type}s/{namespace}/{name}/resolve/{revision}/{path}"

    # Per-source attempts accumulated across the loop. If every source
    # falls through (TRY_NEXT_SOURCE), the aggregated JSON body exposes
    # this list under `body.sources` so the client can tell which
    # sources were asked, what each one answered, and pick the right
    # remediation (token, retry, move on).
    attempts: list[dict] = []

    async def _attempt(source, gens):
        return await _resolve_one_source(
            source,
            repo_type,
            namespace,
            name,
            user_id,
            tokens_hash,
            kohaku_path,
            method,
            attempts,
            cache,
            gens,
            client_headers=safe_client_headers,
        )

    return await _run_cached_then_chain(
        repo_type, namespace, name,
        user_id, tokens_hash,
        sources, cache, attempts, _attempt,
        # `resolve` is per-file. All-404 → EntryNotFound (default scope
        # for the aggregate). The aggregate's own status-priority logic
        # promotes that to RepoNotFound if any attempt was a bare-401
        # anti-enum, matching hf_hub's behavior.
        aggregate_scope="file",
    )


async def _resolve_one_source(
    source: dict,
    repo_type: str,
    namespace: str,
    name: str,
    user_id: Optional[int],
    tokens_hash: str,
    kohaku_path: str,
    method: str,
    attempts: list[dict],
    cache,
    gens: tuple[int, int, int],
    *,
    client_headers: dict[str, str] | None = None,
) -> Optional[Response]:
    """Run a resolve probe (HEAD, then GET if method=GET) against one source.

    Returns:
        ``Response`` if the source binds (BIND_AND_RESPOND or
        BIND_AND_PROPAGATE) — the loop should stop and serve this.
        ``None`` if the source falls through (TRY_NEXT_SOURCE) — the
        attempt has already been appended to ``attempts``.
    """
    head_t0 = time.monotonic()
    try:
        client = FallbackClient(
            source_url=source["url"],
            source_type=source["source_type"],
            token=source.get("token"),
        )
        response = await client.head(kohaku_path, repo_type)
    except httpx.TimeoutException as e:
        head_dt_ms = int((time.monotonic() - head_t0) * 1000)
        logger.warning(f"Fallback source {source['name']} HEAD timed out")
        attempts.append(build_fallback_attempt(source, timeout=e))
        record_source_hop(
            source,
            method="HEAD",
            upstream_path=kohaku_path,
            duration_ms=head_dt_ms,
            transport_decision="TIMEOUT",
            error=str(e) or "request timed out",
        )
        return None
    except Exception as e:
        head_dt_ms = int((time.monotonic() - head_t0) * 1000)
        logger.warning(f"Fallback source {source['name']} HEAD failed: {e}")
        attempts.append(build_fallback_attempt(source, network=e))
        record_source_hop(
            source,
            method="HEAD",
            upstream_path=kohaku_path,
            duration_ms=head_dt_ms,
            transport_decision="NETWORK_ERROR",
            error=str(e) or type(e).__name__,
        )
        return None

    head_dt_ms = int((time.monotonic() - head_t0) * 1000)
    decision = classify_upstream(response)
    record_source_hop(
        source,
        method="HEAD",
        upstream_path=kohaku_path,
        response=response,
        decision=decision,
        duration_ms=head_dt_ms,
    )

    if decision is FallbackDecision.TRY_NEXT_SOURCE:
        logger.warning(
            f"Fallback {source['name']}: HEAD {response.status_code} "
            f"{response.headers.get('x-error-code') or '(no X-Error-Code)'} "
            f"→ TRY_NEXT_SOURCE"
        )
        attempts.append(build_fallback_attempt(source, response=response))
        return None

    # BIND_AND_RESPOND or BIND_AND_PROPAGATE: this source has the repo.
    # Update cache so subsequent requests skip the chain probe.
    # ``safe_set`` rejects the write if any of the three generation
    # counters has been bumped since the snapshot at probe entry —
    # admin source mutation, this user's token rotation, or this
    # repo's local CRUD (create/delete/move/visibility) all bump
    # their respective counters and force a re-probe on the next call.
    cache.safe_set(
        user_id,
        tokens_hash,
        repo_type,
        namespace,
        name,
        source["url"],
        source["name"],
        source["source_type"],
        gens_at_start=gens,
        exists=True,
    )

    if decision is FallbackDecision.BIND_AND_PROPAGATE:
        # 4xx + EntryNotFound / RevisionNotFound / Disabled — the repo
        # is here, but the requested entry/revision is not (or the repo
        # is taken down here). Forward upstream verbatim so a hf_hub
        # client raises the right specific exception. Crucially, do NOT
        # try the next source — a sibling source's same-named repo
        # would be a different repo (#75).
        logger.info(
            f"Fallback BIND_AND_PROPAGATE: {repo_type}/{namespace}/{name} "
            f"at {source['name']} → upstream {response.status_code} "
            f"{response.headers.get('x-error-code') or response.headers.get('x-error-message') or ''}"
        )
        return _propagate_upstream_response(response, source)

    # BIND_AND_RESPOND: HEAD says repo+file present at this source.
    logger.info(
        f"Fallback SUCCESS: {repo_type}/{namespace}/{name} found at {source['name']}"
    )

    if method == "HEAD":
        # Asymmetry-by-design vs. the GET path below: HEAD does NOT
        # forward ``client_headers`` upstream. Two reasons —
        #   1) ``huggingface_hub`` HEAD-on-resolve never carries Range;
        #      partial-content semantics are a GET-only concern.
        #   2) ``apply_resolve_head_postprocess`` fires its own
        #      follow-HEAD with ``Accept-Encoding: identity`` to keep
        #      Content-Length intact (PR #21 — gzip auto-decompression
        #      in httpx silently strips Content-Length and breaks
        #      hf_hub's post-download size check). Forwarding a
        #      client-supplied ``Accept-Encoding: gzip`` upstream
        #      would re-engage that bug.
        # If you need ``If-None-Match`` 304 short-circuit on HEAD,
        # plumb a NARROWER whitelist into the binding HEAD probe ONLY
        # — never into the follow-HEAD inside the postprocess.
        # ``test_try_fallback_resolve_head_does_not_forward_client_headers``
        # is the regression-guard.
        return await _build_resolve_head_response(response, source, client)

    # GET phase. Once HEAD has bound this source we are committed:
    # the GET response — whether 200, 5xx, or anything else — is what
    # the user gets. Falling through to another source here is the
    # cross-source mixing bug #75 fixes (HEAD-200 at A, GET-502 at A,
    # then sneak over to B's same-named-but-different repo).
    #
    # Plan A invariants enforced here:
    #   • ``follow_redirects=False`` — never let httpx chase an upstream
    #     30x into a CDN body fetch. A 1.5 GB safetensors must not pass
    #     through the backend; the redirect Location is what we hand
    #     back to the client (mirrors local ``resolve_file_get`` 302).
    #   • ``headers=client_headers`` — forward the caller's whitelisted
    #     Range / If-* headers so partial-content semantics survive.
    #     The whitelist (Range, If-Match, If-None-Match,
    #     If-Modified-Since, If-Unmodified-Since, If-Range) is built by
    #     the ``with_repo_fallback`` decorator; Authorization / Cookie
    #     are filtered there and never reach this call.
    get_t0 = time.monotonic()
    try:
        get_response = await client.get(
            kohaku_path,
            repo_type,
            follow_redirects=False,
            headers=client_headers or None,
        )
    except httpx.TimeoutException as e:
        get_dt_ms = int((time.monotonic() - get_t0) * 1000)
        logger.warning(
            f"GET timed out at bound source {source['name']} after HEAD bind: {e}"
        )
        record_source_hop(
            source,
            method="GET",
            upstream_path=kohaku_path,
            duration_ms=get_dt_ms,
            transport_decision="TIMEOUT",
            error=str(e) or "request timed out",
        )
        # Bound, no upstream response to forward. Synthesize a 502 from
        # this single attempt; the aggregate-failure helper already
        # produces the right shape.
        return build_aggregate_failure_response(
            [build_fallback_attempt(source, timeout=e)]
        )
    except Exception as e:
        get_dt_ms = int((time.monotonic() - get_t0) * 1000)
        logger.warning(
            f"GET failed at bound source {source['name']} after HEAD bind: {e}"
        )
        record_source_hop(
            source,
            method="GET",
            upstream_path=kohaku_path,
            duration_ms=get_dt_ms,
            transport_decision="NETWORK_ERROR",
            error=str(e) or type(e).__name__,
        )
        return build_aggregate_failure_response(
            [build_fallback_attempt(source, network=e)]
        )

    get_dt_ms = int((time.monotonic() - get_t0) * 1000)
    record_source_hop(
        source,
        method="GET",
        upstream_path=kohaku_path,
        response=get_response,
        decision=classify_upstream(get_response),
        duration_ms=get_dt_ms,
    )

    # Plan A primary path: 30x → forward Location to the client; the
    # CDN/presigned target is the byte source, not this backend.
    if (
        300 <= get_response.status_code < 400
        and get_response.headers.get("location")
    ):
        logger.info(
            f"GET {get_response.status_code} → redirect-passthrough at "
            f"{source['name']} (Location forwarded to client; no body buffer)"
        )
        return _propagate_upstream_redirect(get_response, source)

    if get_response.status_code == 200:
        # Proxy the content with original headers, stripping the
        # compression-related ones since httpx has already decoded the
        # body and the next hop would otherwise try to decompress an
        # already-decompressed payload.
        resp_headers = dict(get_response.headers)
        resp_headers.pop("content-encoding", None)
        resp_headers.pop("content-length", None)
        resp_headers.pop("transfer-encoding", None)
        strip_xet_response_headers(resp_headers)
        resp_headers.update(
            add_source_headers(get_response, source["name"], source["url"])
        )
        return Response(
            status_code=get_response.status_code,
            content=get_response.content,
            headers=resp_headers,
        )

    # GET non-200 at a bound source: forward upstream's status verbatim.
    # No cross-source retry — see comment above.
    logger.warning(
        f"GET {get_response.status_code} at bound source {source['name']} "
        f"→ propagating (bound)"
    )
    return _propagate_upstream_response(get_response, source)


async def _build_resolve_head_response(
    response: httpx.Response, source: dict, client: "FallbackClient"
) -> Response:
    """Build the HEAD-method response delegated to ``apply_resolve_head_postprocess``.

    The actual Location-rewrite + non-LFS follow-HEAD + xet-strip +
    X-Source* logic now lives in ``utils.apply_resolve_head_postprocess``
    so the chain-tester ``probe_chain`` can reuse the same code path
    for byte-identical fidelity (#78 v3).
    """
    resp_headers = await apply_resolve_head_postprocess(
        response,
        source,
        follow_timeout=client.timeout,
        follow_token=client.token,
    )
    return Response(
        status_code=response.status_code,
        content=response.content,
        headers=resp_headers,
    )


async def try_fallback_info(
    repo_type: str,
    namespace: str,
    name: str,
    user_tokens: dict[str, str] | None = None,
    user=None,
) -> Optional[dict]:
    """Try to get repository info from fallback sources.

    Args:
        repo_type: "model", "dataset", or "space"
        namespace: Repository namespace
        name: Repository name
        user_tokens: User-provided external tokens (overrides admin tokens)
        user: Authenticated user (or None) for per-user cache isolation.

    Returns:
        Repository info dict or None if not found
    """
    cache = get_cache()
    sources = get_enabled_sources(namespace, user_tokens=user_tokens)

    if not sources:
        return None

    user_id = _resolve_user_id(user)
    tokens_hash = compute_tokens_hash(user_tokens)

    # Construct API path
    kohaku_path = f"/api/{repo_type}s/{namespace}/{name}"
    attempts: list[dict] = []

    async def _attempt(source, gens):
        t0 = time.monotonic()
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )
            response = await client.get(kohaku_path, repo_type)
        except httpx.TimeoutException as e:
            dt_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(f"Fallback info timed out at {source['name']}")
            attempts.append(build_fallback_attempt(source, timeout=e))
            record_source_hop(
                source,
                method="GET",
                upstream_path=kohaku_path,
                duration_ms=dt_ms,
                transport_decision="TIMEOUT",
                error=str(e) or "request timed out",
            )
            return None
        except Exception as e:
            dt_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(f"Fallback info failed for {source['name']}: {e}")
            attempts.append(build_fallback_attempt(source, network=e))
            record_source_hop(
                source,
                method="GET",
                upstream_path=kohaku_path,
                duration_ms=dt_ms,
                transport_decision="NETWORK_ERROR",
                error=str(e) or type(e).__name__,
            )
            return None

        dt_ms = int((time.monotonic() - t0) * 1000)
        decision = classify_upstream(response)
        record_source_hop(
            source,
            method="GET",
            upstream_path=kohaku_path,
            response=response,
            decision=decision,
            duration_ms=dt_ms,
        )
        if decision is FallbackDecision.TRY_NEXT_SOURCE:
            logger.warning(
                f"Fallback info {source['name']}: HTTP {response.status_code} "
                f"{response.headers.get('x-error-code') or ''} → TRY_NEXT_SOURCE"
            )
            attempts.append(build_fallback_attempt(source, response=response))
            return None

        # BIND — write cache for repo-grain reuse.
        cache.safe_set(
            user_id, tokens_hash,
            repo_type, namespace, name,
            source["url"], source["name"], source["source_type"],
            gens_at_start=gens,
            exists=True,
        )

        if decision is FallbackDecision.BIND_AND_PROPAGATE:
            # 4xx + EntryNotFound/RevisionNotFound on info is unusual
            # (info is a repo-level endpoint, EntryNotFound semantics
            # don't really apply). If it ever happens, propagate so
            # hf_hub raises the right exception rather than masking it
            # by trying another source.
            logger.info(
                f"Fallback info BIND_AND_PROPAGATE at {source['name']}: "
                f"upstream {response.status_code} "
                f"{response.headers.get('x-error-code') or ''}"
            )
            return _propagate_upstream_response(response, source)

        # BIND_AND_RESPOND — parse and tag.
        data = response.json()
        data["_source"] = source["name"]
        data["_source_url"] = source["url"]
        logger.info(
            f"Fallback info SUCCESS: {repo_type}/{namespace}/{name} from {source['name']}"
        )
        return data

    return await _run_cached_then_chain(
        repo_type, namespace, name,
        user_id, tokens_hash,
        sources, cache, attempts, _attempt,
        aggregate_scope="repo",
    )


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
    user=None,
) -> Optional[Response]:
    """Try to get repository tree from fallback sources.

    Args:
        repo_type: "model", "dataset", or "space"
        namespace: Repository namespace
        name: Repository name
        revision: Branch or commit
        path: Path within repository
        user_tokens: User-provided external tokens (overrides admin tokens)
        user: Authenticated user (or None) for per-user cache isolation.

    Returns:
        JSON response or None if not found
    """
    cache = get_cache()
    sources = get_enabled_sources(namespace, user_tokens=user_tokens)

    if not sources:
        return None

    user_id = _resolve_user_id(user)
    tokens_hash = compute_tokens_hash(user_tokens)

    # Construct API path (strip leading slash from path to avoid double slash)
    clean_path = path.lstrip("/") if path else ""
    kohaku_path = f"/api/{repo_type}s/{namespace}/{name}/tree/{revision}/{clean_path}"
    attempts: list[dict] = []

    params: dict = {"recursive": recursive, "expand": expand}
    if limit is not None:
        params["limit"] = limit
    if cursor:
        params["cursor"] = cursor

    async def _attempt(source, gens):
        t0 = time.monotonic()
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )
            response = await client.get(kohaku_path, repo_type, params=params)
        except httpx.TimeoutException as e:
            dt_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(f"Fallback tree timed out at {source['name']}")
            attempts.append(build_fallback_attempt(source, timeout=e))
            record_source_hop(
                source,
                method="GET",
                upstream_path=kohaku_path,
                duration_ms=dt_ms,
                transport_decision="TIMEOUT",
                error=str(e) or "request timed out",
            )
            return None
        except Exception as e:
            dt_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(f"Fallback tree failed for {source['name']}: {e}")
            attempts.append(build_fallback_attempt(source, network=e))
            record_source_hop(
                source,
                method="GET",
                upstream_path=kohaku_path,
                duration_ms=dt_ms,
                transport_decision="NETWORK_ERROR",
                error=str(e) or type(e).__name__,
            )
            return None

        dt_ms = int((time.monotonic() - t0) * 1000)
        decision = classify_upstream(response)
        record_source_hop(
            source,
            method="GET",
            upstream_path=kohaku_path,
            response=response,
            decision=decision,
            duration_ms=dt_ms,
        )
        if decision is FallbackDecision.TRY_NEXT_SOURCE:
            logger.warning(
                f"Fallback tree {source['name']}: HTTP {response.status_code} "
                f"{response.headers.get('x-error-code') or ''} → TRY_NEXT_SOURCE"
            )
            attempts.append(build_fallback_attempt(source, response=response))
            return None

        cache.safe_set(
            user_id, tokens_hash,
            repo_type, namespace, name,
            source["url"], source["name"], source["source_type"],
            gens_at_start=gens,
            exists=True,
        )

        if decision is FallbackDecision.BIND_AND_PROPAGATE:
            # tree on a path the repo doesn't have → 404 + EntryNotFound
            # at this source. Repo is bound here; sibling sources'
            # same-named repos are different repos, don't try them.
            logger.info(
                f"Fallback tree BIND_AND_PROPAGATE at {source['name']}: "
                f"upstream {response.status_code} "
                f"{response.headers.get('x-error-code') or ''}"
            )
            return _propagate_upstream_response(response, source)

        # BIND_AND_RESPOND — forward upstream's body with content-type +
        # Link (pagination cursor) intact.
        logger.info(
            f"Fallback tree SUCCESS: {repo_type}/{namespace}/{name}/tree "
            f"from {source['name']}"
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

    return await _run_cached_then_chain(
        repo_type, namespace, name,
        user_id, tokens_hash,
        sources, cache, attempts, _attempt,
        # Tree is a repo-level operation: scope="repo" so all-404 maps
        # to RepoNotFound (matches HF for a missing repo).
        aggregate_scope="repo",
    )


async def try_fallback_paths_info(
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    paths: list[str],
    expand: bool = False,
    user_tokens: dict[str, str] | None = None,
    user=None,
) -> Optional[list]:
    """Try to get paths info from fallback sources.

    Args:
        repo_type: "model", "dataset", or "space"
        namespace: Repository namespace
        name: Repository name
        revision: Branch or commit
        paths: List of paths to query
        user_tokens: User-provided external tokens (overrides admin tokens)
        user: Authenticated user (or None) for per-user cache isolation.

    Returns:
        List of path info objects or None if not found
    """
    cache = get_cache()
    sources = get_enabled_sources(namespace, user_tokens=user_tokens)

    if not sources:
        return None

    user_id = _resolve_user_id(user)
    tokens_hash = compute_tokens_hash(user_tokens)

    # Construct API path
    kohaku_path = f"/api/{repo_type}s/{namespace}/{name}/paths-info/{revision}"
    attempts: list[dict] = []

    async def _attempt(source, gens):
        t0 = time.monotonic()
        try:
            client = FallbackClient(
                source_url=source["url"],
                source_type=source["source_type"],
                token=source.get("token"),
            )
            response = await client.post(
                kohaku_path, repo_type, data={"paths": paths, "expand": expand}
            )
        except httpx.TimeoutException as e:
            dt_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(f"Fallback paths-info timed out at {source['name']}")
            attempts.append(build_fallback_attempt(source, timeout=e))
            record_source_hop(
                source,
                method="POST",
                upstream_path=kohaku_path,
                duration_ms=dt_ms,
                transport_decision="TIMEOUT",
                error=str(e) or "request timed out",
            )
            return None
        except Exception as e:
            dt_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(f"Fallback paths-info failed for {source['name']}: {e}")
            attempts.append(build_fallback_attempt(source, network=e))
            record_source_hop(
                source,
                method="POST",
                upstream_path=kohaku_path,
                duration_ms=dt_ms,
                transport_decision="NETWORK_ERROR",
                error=str(e) or type(e).__name__,
            )
            return None

        dt_ms = int((time.monotonic() - t0) * 1000)
        decision = classify_upstream(response)
        record_source_hop(
            source,
            method="POST",
            upstream_path=kohaku_path,
            response=response,
            decision=decision,
            duration_ms=dt_ms,
        )
        if decision is FallbackDecision.TRY_NEXT_SOURCE:
            logger.warning(
                f"Fallback paths-info {source['name']}: HTTP "
                f"{response.status_code} "
                f"{response.headers.get('x-error-code') or ''} → TRY_NEXT_SOURCE"
            )
            attempts.append(build_fallback_attempt(source, response=response))
            return None

        cache.safe_set(
            user_id, tokens_hash,
            repo_type, namespace, name,
            source["url"], source["name"], source["source_type"],
            gens_at_start=gens,
            exists=True,
        )

        if decision is FallbackDecision.BIND_AND_PROPAGATE:
            logger.info(
                f"Fallback paths-info BIND_AND_PROPAGATE at {source['name']}: "
                f"upstream {response.status_code} "
                f"{response.headers.get('x-error-code') or ''}"
            )
            return _propagate_upstream_response(response, source)

        # BIND_AND_RESPOND
        logger.info(
            f"Fallback paths-info SUCCESS: {repo_type}/{namespace}/{name} "
            f"from {source['name']}"
        )
        return response.json()

    return await _run_cached_then_chain(
        repo_type, namespace, name,
        user_id, tokens_hash,
        sources, cache, attempts, _attempt,
        # paths-info is per-file (answers "does file X exist at
        # revision R"), so all-404 stays scope="file" → EntryNotFound.
        aggregate_scope="file",
    )


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
