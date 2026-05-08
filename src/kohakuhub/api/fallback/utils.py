"""Utility functions for fallback system."""

import enum
from typing import Optional
from urllib.parse import urljoin

import httpx
from fastapi.responses import JSONResponse

from kohakuhub.logger import get_logger

logger = get_logger("FALLBACK_UTILS")


class FallbackDecision(enum.Enum):
    """Per-source decision for one upstream response.

    Drives the repo-grain binding rule (issue #75): once a source confirms
    the repo exists at this layer (BIND_AND_*), the loop stops and serves
    the upstream's answer instead of cycling through more sources for the
    same ``repo_id``. Mixing reads of one repo across sources produces an
    inconsistent view (info from A, tree from B, file from C all describe
    a "bert-base-uncased" but they are three different repos).
    """

    BIND_AND_RESPOND = "bind_and_respond"
    """Source has the repo and supplies the answer; return upstream's body."""

    BIND_AND_PROPAGATE = "bind_and_propagate"
    """Source has the repo, upstream's error is the right answer; do not try
    further sources, forward upstream verbatim. Used for EntryNotFound /
    RevisionNotFound / "Access to this resource is disabled." — these say
    *the repo is here, the entry/revision is not* (or the repo is taken
    down here), and a sibling source's same-named repo would be a
    different repo."""

    TRY_NEXT_SOURCE = "try_next_source"
    """Source can't serve; advance the loop. Includes 401/403 (incl
    GatedRepo — try a source where the user might have access; aggregate
    layer preserves the GatedRepo signal if every source ends up gated),
    404+RepoNotFound, bare 401/403/404, 5xx, timeout, network errors."""


# Category labels attached to each fallback attempt, used for aggregation.
# Kept intentionally coarse — finer upstream semantics (e.g. "repo gated"
# vs "org access revoked") ride in the `message` field pulled from the
# upstream body, because there is no portable way to distinguish them.
CATEGORY_AUTH = "auth"  # HTTP 401 — authentication required
CATEGORY_DISABLED = "disabled"  # X-Error-Message magic-string: HF moderation takedown
CATEGORY_FORBIDDEN = "forbidden"  # HTTP 403 — explicit deny
CATEGORY_NOT_FOUND = "not-found"  # HTTP 404 / 410
CATEGORY_SERVER = "server"  # HTTP 5xx
CATEGORY_TIMEOUT = "timeout"  # httpx.TimeoutException
CATEGORY_NETWORK = "network"  # other transport-level failure
CATEGORY_OTHER = "other"  # anything not covered above (4xx edge cases)

# Cap per-attempt message length so a misbehaving upstream returning a
# multi-megabyte error page can't blow up response headers or the body
# schema. The message is advisory; full upstream detail is preserved in
# logs if anyone needs it.
MAX_ATTEMPT_MESSAGE_LEN = 500


def is_not_found_error(response: httpx.Response) -> bool:
    """Check if response indicates resource not found.

    Args:
        response: HTTP response

    Returns:
        True if 404 or similar "not found" error
    """
    return response.status_code in (404, 410)  # 404 Not Found, 410 Gone


def is_client_error(response: httpx.Response) -> bool:
    """Check if response is a client error (4xx).

    Args:
        response: HTTP response

    Returns:
        True if status code is 4xx
    """
    return 400 <= response.status_code < 500


def is_server_error(response: httpx.Response) -> bool:
    """Check if response is a server error (5xx).

    Args:
        response: HTTP response

    Returns:
        True if status code is 5xx
    """
    return 500 <= response.status_code < 600


def extract_error_message(response: httpx.Response) -> str:
    """Extract error message from response.

    Args:
        response: HTTP response

    Returns:
        Error message string
    """
    try:
        error_data = response.json()
        if isinstance(error_data, dict):
            # Try common error field names
            for field in ("error", "message", "detail", "msg"):
                if field in error_data:
                    msg = error_data[field]
                    if isinstance(msg, str):
                        return msg
                    elif isinstance(msg, dict) and "message" in msg:
                        return msg["message"]
        return str(error_data)
    except Exception:
        return response.text or f"HTTP {response.status_code}"


def should_retry_source(response: httpx.Response) -> bool:
    """Determine if request should be retried with next source.

    Args:
        response: HTTP response

    Returns:
        True if should try next source, False if should give up
    """
    # Retry on 404 (not found) - might be in another source
    if response.status_code == 404:
        return True

    # Retry on server errors (5xx) - source might be temporarily down
    if is_server_error(response):
        return True

    # Retry on timeout/connection errors
    if response.status_code in (408, 504, 524):  # Timeout, Gateway Timeout
        return True

    # Don't retry on other client errors (401, 403, 400, etc.)
    # These indicate permission/validation issues
    if is_client_error(response):
        return False

    # Success - don't retry
    if 200 <= response.status_code < 300:
        return False

    # Default: don't retry
    return False


# ``X-Error-Message`` value HF emits for repos disabled by moderation.
# `huggingface_hub.utils._http.hf_raise_for_status` keys off this exact
# string to raise ``DisabledRepoError`` (no ``X-Error-Code`` is set on
# these responses), so we have to compare equality, not contains.
_HF_DISABLED_MESSAGE = "Access to this resource is disabled."


def classify_upstream(response_or_exc) -> FallbackDecision:
    """Classify one upstream response (or transport exception) for the loop.

    The matrix below mirrors the priority of
    ``huggingface_hub.utils._http.hf_raise_for_status`` (X-Error-Code is
    consulted before the numeric status code), so that a hf_hub client
    talking *through* KohakuHub's fallback gets the same exception type
    it would have gotten talking to the upstream directly.

    Empirically anchored against ``https://huggingface.co`` responses
    captured 2026-04-30 (see #75 for the full table). Two contract shifts
    that are easy to miss:

    - HF returns ``401`` (no ``X-Error-Code``, message
      ``"Invalid username or password."``) to *anonymous* callers asking
      about a non-existent repo (anti-enumeration). The same probe with
      a valid token returns ``404 + X-Error-Code: RepoNotFound``. Both
      classify as ``TRY_NEXT_SOURCE`` here.
    - ``X-Error-Code: GatedRepo`` rides on **either** 401 (anon /
      bad-token) or 403 (authed but not in the access list). hf_hub
      checks the header, not the status, so we do too — and we send
      *both* to ``TRY_NEXT_SOURCE`` because another source might serve
      this repo without gating. The aggregate layer
      (`build_aggregate_failure_response`) preserves the GatedRepo
      category if every source ends up gated.

    Args:
        response_or_exc: Either an ``httpx.Response`` to classify, or an
            exception raised while making the request (timeout, network).

    Returns:
        ``FallbackDecision`` member. Callers should ``match`` on the
        result and act per the docstrings on each enum value.
    """
    if isinstance(response_or_exc, BaseException):
        # Timeout / connection refused / DNS / etc. — we cannot tell
        # whether the repo lives here. Move on.
        return FallbackDecision.TRY_NEXT_SOURCE

    response: httpx.Response = response_or_exc

    # Header lookups are case-insensitive on httpx.Headers; ``.get``
    # returns ``None`` if absent, which all branches below tolerate.
    error_code = (
        response.headers.get("x-error-code") if response.headers else None
    )
    error_message = (
        response.headers.get("x-error-message") if response.headers else None
    )

    # 2xx (success) and 3xx (redirect — common for HF's canonical-name
    # redirects and the resolve-cache redirect). Either way, the repo is
    # at this source and we want to serve.
    if 200 <= response.status_code < 400:
        return FallbackDecision.BIND_AND_RESPOND

    # X-Error-Code wins over status, mirroring hf_hub. EntryNotFound and
    # RevisionNotFound are *positive* signals that the repo is at this
    # source — only the entry/revision is missing — so we bind and
    # forward the 404 verbatim. Continuing to the next source here is
    # exactly the cross-source mixing bug #75 is fixing.
    if error_code == "EntryNotFound":
        return FallbackDecision.BIND_AND_PROPAGATE
    if error_code == "RevisionNotFound":
        return FallbackDecision.BIND_AND_PROPAGATE

    # GatedRepo, RepoNotFound, and the "disabled" X-Error-Message
    # marker are *negative* signals from THIS source's perspective —
    # the source can't serve, but a different source's same-named repo
    # might. Try the next one; the aggregate layer preserves the
    # category, so an all-gated / all-disabled chain still surfaces
    # the right ``X-Error-Code`` / ``X-Error-Message`` to the hf_hub
    # client and raises ``GatedRepoError`` / ``DisabledRepoError``.
    #
    # Note ``disabled`` lives here, not under BIND_AND_PROPAGATE: a
    # repo that HF disabled (moderation takedown) on one source might
    # still be available on another KohakuHub mirror that wasn't
    # asked to disable it. Same logic as gated.
    if error_code == "GatedRepo":
        return FallbackDecision.TRY_NEXT_SOURCE
    if error_code == "RepoNotFound":
        return FallbackDecision.TRY_NEXT_SOURCE
    if error_message == _HF_DISABLED_MESSAGE:
        return FallbackDecision.TRY_NEXT_SOURCE

    # No actionable X-Error-Code: fall back to status semantics. Bare
    # 401 (HF anti-enum or just broken token), bare 403 (no GatedRepo
    # marker), bare 404, 5xx, 4xx (other) all mean "this source can't
    # serve" — TRY_NEXT_SOURCE.
    return FallbackDecision.TRY_NEXT_SOURCE


def strip_xet_response_headers(headers: dict) -> None:
    """Remove Xet-protocol hints from a fallback response's headers in place.

    KohakuHub does not natively speak the huggingface.co Xet protocol. When a
    downstream client (`huggingface_hub >= 1.x`) sees `X-Xet-*` response
    headers or a `Link: <...>; rel="xet-auth"` relation, it switches to the
    Xet code path and calls endpoints we do not implement (`/api/models/...
    /xet-read-token/...`) — breaking the entire download. Stripping these
    signals puts the client back on the classic LFS path, which is served by
    the fallback's standard 3xx Location redirect.

    See `huggingface_hub.utils._xet.parse_xet_file_data_from_response` and
    `huggingface_hub.constants.HUGGINGFACE_HEADER_X_XET_*` for the upstream
    trigger list. This mutates `headers` in place and is a no-op for
    responses that carry no Xet signals.
    """
    for key in list(headers.keys()):
        if key.lower().startswith("x-xet-"):
            headers.pop(key, None)

    link_key = next(
        (k for k in headers.keys() if k.lower() == "link"), None
    )
    if not link_key:
        return

    kept = []
    for chunk in headers[link_key].split(","):
        if 'rel="xet-auth"' in chunk.lower() or "rel=xet-auth" in chunk.lower():
            continue
        kept.append(chunk)
    new_link = ",".join(kept).strip().strip(",").strip()
    if new_link:
        headers[link_key] = new_link
    else:
        headers.pop(link_key, None)


def _categorize_status(
    status: int,
    error_code: str | None = None,
    error_message: str | None = None,
) -> str:
    """Map an upstream response onto one of the CATEGORY_* buckets.

    401 is deliberately ambiguous on HuggingFace: the same status is
    used for "repo is gated and you're not authed" AND "repo doesn't
    exist at all" (anti-enumeration policy — see
    ``huggingface_hub.utils._http.hf_raise_for_status``'s inline
    comment "401 is misleading..."). The two cases are distinguished
    by the ``X-Error-Code: GatedRepo`` header, which HF only sets
    when the repo actually exists and is gated.

    Classification priority mirrors hf_hub's own rules:

    - Explicit ``X-Error-Code: GatedRepo`` → ``auth``.
    - Explicit ``X-Error-Code`` in
      {RepoNotFound, EntryNotFound, RevisionNotFound} → ``not-found``.
    - Bare 401 (no ``X-Error-Code``) → ``not-found``, because HF
      returns 401 for non-existent repos. The aggregate layer will
      then emit ``X-Error-Code: RepoNotFound`` so the client raises
      ``RepositoryNotFoundError`` — exactly what hf_hub does for
      the same input.
    - ``error_message`` is reserved for future disambiguation (e.g.
      "Invalid credentials in Authorization header" → genuine auth
      failure). Accepted today for API stability; unused for now.
    """
    if error_code == "GatedRepo":
        return CATEGORY_AUTH
    if error_code in ("RepoNotFound", "EntryNotFound", "RevisionNotFound"):
        return CATEGORY_NOT_FOUND
    # Disabled-repo marker (HF moderation takedown). Detected via the
    # exact X-Error-Message string ``hf_raise_for_status`` keys off.
    # Has its own category so the aggregate can preserve the marker
    # all the way back to the hf_hub client (which raises
    # ``DisabledRepoError``).
    if error_message == _HF_DISABLED_MESSAGE:
        return CATEGORY_DISABLED
    if status == 401:
        # No GatedRepo code → HF is telling us the repo doesn't exist
        # (or at best is indistinguishable from missing to an
        # un-authed caller). Classify as not-found so the aggregate
        # response maps to RepositoryNotFoundError on the client.
        return CATEGORY_NOT_FOUND
    if status == 403:
        return CATEGORY_FORBIDDEN
    if status in (404, 410):
        return CATEGORY_NOT_FOUND
    if 500 <= status < 600:
        return CATEGORY_SERVER
    return CATEGORY_OTHER


def build_fallback_attempt(
    source: dict,
    *,
    response: httpx.Response | None = None,
    timeout: BaseException | None = None,
    network: BaseException | None = None,
) -> dict:
    """Normalize one probe against one fallback source into a serializable dict.

    Exactly one of ``response`` / ``timeout`` / ``network`` must be set:

    - ``response`` → HTTP response that arrived (status + body available).
    - ``timeout`` → the request tripped the client timeout before responding.
    - ``network`` → any other transport-level failure (DNS, refused, etc.).

    Shape is public contract: the same dict is embedded verbatim in the
    aggregate failure body and is what the SPA / any CLI client will see
    under ``body.sources[*]``. ``error_code`` captures the upstream's
    ``X-Error-Code`` header so the aggregate layer can distinguish
    "401 with GatedRepo" (real gated) from "401 without GatedRepo"
    (repo doesn't exist) per hf_hub's own heuristic.
    """
    base = {
        "name": source.get("name"),
        "url": source.get("url"),
        "status": None,
        "category": CATEGORY_OTHER,
        "error_code": None,
        "message": "",
    }

    if response is not None:
        # httpx.Headers is case-insensitive; `.get()` handles either
        # capitalization. Missing header returns None, which the
        # categorizer understands.
        error_code = None
        error_message = None
        if response.headers:
            error_code = response.headers.get("x-error-code")
            error_message = response.headers.get("x-error-message")
        base["status"] = response.status_code
        base["error_code"] = error_code
        base["category"] = _categorize_status(
            response.status_code, error_code, error_message
        )
        msg = extract_error_message(response) or ""
        base["message"] = msg[:MAX_ATTEMPT_MESSAGE_LEN]
        return base

    if timeout is not None:
        base["category"] = CATEGORY_TIMEOUT
        base["message"] = str(timeout) or "request timed out"
        return base

    if network is not None:
        base["category"] = CATEGORY_NETWORK
        base["message"] = str(network) or type(network).__name__
        return base

    # Caller violated the contract; keep the default "other" category so
    # the aggregate still reports something rather than swallowing it.
    return base


def build_aggregate_failure_response(
    attempts: list[dict],
    *,
    scope: str = "file",
) -> JSONResponse:
    """Combine per-source attempts into one HTTP response.

    Status priority (highest first): 401 > 403 > 404 > 502. The
    rationale is user-actionability — an auth failure is the most
    specific next step ("attach a token"), an explicit 403 is next, a
    real "not found" after that, and 5xx / timeout / network get
    collapsed to 502 Bad Gateway.

    X-Error-Code values are intentionally **aligned with
    huggingface_hub.utils._http.hf_raise_for_status**:

    - 401 → ``GatedRepo`` → ``GatedRepoError`` on the client
    - 404 (all attempts) → ``EntryNotFound`` / ``RepoNotFound``
      depending on ``scope`` (see below) → ``EntryNotFoundError`` or
      ``RepositoryNotFoundError``
    - 403, 502 → no ``X-Error-Code`` (HF client falls back to generic
      ``HfHubHTTPError``; for 5xx its retry path handles transient
      upstream issues).

    ``scope`` picks the right 404 classification for the caller:

    - ``"file"`` (default) — per-file operation (``resolve``,
      ``paths-info``). All-404 → ``EntryNotFound`` so the client
      raises ``EntryNotFoundError``.
    - ``"repo"`` — repo-level operation (``info``, ``tree``).
      All-404 → ``RepoNotFound`` so the client raises
      ``RepositoryNotFoundError``.

    Putting the code in the header (not just the body) matters because
    ``huggingface_hub`` reads ``X-Error-Code`` to decide which exception
    subclass to raise — inventing our own codes here would downgrade
    gated-repo downloads to a generic 4xx error and lose the actionable
    exception type that users already handle.
    """
    categories = {a.get("category") for a in attempts}

    if CATEGORY_AUTH in categories:
        status_code = 401
        error_code = "GatedRepo"
        detail = (
            "Upstream source requires authentication - likely a gated "
            "repository. Attach an access token for that source in "
            "KohakuHub account settings."
        )
    elif CATEGORY_DISABLED in categories:
        # HF moderation marker — every probed source either disabled
        # this resource or fell through with a category that ranks
        # below disabled. Re-emit the exact ``X-Error-Message`` that
        # ``hf_raise_for_status`` keys off so the hf_hub client raises
        # ``DisabledRepoError`` exactly as it would talking to HF
        # directly.
        status_code = 403
        error_code = None  # disabled is signaled via X-Error-Message
        detail = _HF_DISABLED_MESSAGE
    elif CATEGORY_FORBIDDEN in categories:
        status_code = 403
        error_code = None  # HF has no specific code for plain 403.
        detail = "Upstream source denied access."
    elif attempts and categories <= {CATEGORY_NOT_FOUND}:
        status_code = 404
        # "Bare 401" (401 with no X-Error-Code) is HF's way of telling
        # an un-authed caller that the REPO itself does not exist —
        # see hf_hub's `_http.py` "401 is misleading" comment. If any
        # attempt is of that shape, escalate the aggregate to
        # RepoNotFound even on a per-file op: the right HF-native
        # exception is RepositoryNotFoundError, not EntryNotFoundError.
        repo_miss = any(
            (a.get("status") == 401 and not a.get("error_code"))
            or a.get("error_code") == "RepoNotFound"
            for a in attempts
        )
        if scope == "repo" or repo_miss:
            error_code = "RepoNotFound"
            detail = "No fallback source serves this repository."
        else:
            error_code = "EntryNotFound"
            detail = "No fallback source serves this file."
    else:
        # 5xx / timeout / network mix (or an edge-case "other" category).
        status_code = 502
        error_code = None
        detail = "All fallback sources failed - upstream unavailable."

    body = {
        "error": error_code or "UpstreamFailure",
        "detail": detail,
        "sources": list(attempts),
    }
    headers = {
        "X-Source-Count": str(len(attempts)),
        # HF client echoes X-Error-Message into its exception text, so the
        # CLI user ends up with something readable even without the body.
        "X-Error-Message": detail,
    }
    if error_code:
        headers["X-Error-Code"] = error_code

    return JSONResponse(status_code=status_code, content=body, headers=headers)


def add_source_headers(
    response: httpx.Response, source_name: str, source_url: str
) -> dict:
    """Generate source attribution headers.

    Args:
        response: Original response from external source
        source_name: Display name of the source
        source_url: Base URL of the source

    Returns:
        Dict of headers to add to the response
    """
    return {
        "X-Source": source_name,
        "X-Source-URL": source_url,
        "X-Source-Status": str(response.status_code),
    }


async def apply_resolve_head_postprocess(
    response: httpx.Response,
    source: dict,
    *,
    follow_timeout: float = 30.0,
    follow_token: Optional[str] = None,
) -> dict:
    """Reproduce the production resolve-HEAD response-shaping in one place.

    Two HF-specific quirks the production fallback layer fixes up
    before forwarding HEAD-on-resolve to the client (originally in
    ``operations._build_resolve_head_response``):

    1. **Relative ``Location`` → absolute.** HF returns 3xx redirects
       with paths like ``/api/resolve-cache/...`` that only resolve on
       the HF origin. Rewriting against ``response.request.url`` keeps
       clients following the redirect on the upstream rather than
       bouncing it back to KohakuHub.
    2. **Extra HEAD on non-LFS 3xx for Content-Length/ETag.** HF's 307
       on a small file carries the redirect body's Content-Length
       (~278 bytes), not the file's. Without ``X-Linked-Size`` the
       hf_hub client trusts that bogus Content-Length and fails its
       post-download consistency check (observed in
       ``imgutils.get_wd14_tags`` on ``selected_tags.csv``). A second
       HEAD against the rewritten Location picks up the real
       ``content-length`` / ``etag`` / ``x-repo-commit``. LFS files
       already carry ``X-Linked-Size``; hf_hub prefers it over
       Content-Length so we skip the follow there.

    Plus the universal post-processing every fallback HEAD response
    gets: ``strip_xet_response_headers`` to keep hf_hub on the classic
    LFS path, then ``add_source_headers`` for telemetry.

    Returns a dict of headers caller can then attach to whatever
    ``Response``-like object they want (production builds a Starlette
    ``Response``; the chain-tester probe attaches them to a
    ``ProbeAttempt`` for the timeline UI). Caller decides what to do
    with the returned dict — this function deliberately stays
    transport-agnostic so production + simulate can share the exact
    same logic.

    Args:
        response: The upstream HEAD response we just received from the
            fallback source.
        source: The fallback-source dict (used for ``X-Source*`` headers).
        follow_timeout: Timeout for the optional non-LFS follow-HEAD.
            Defaults to 30s; production passes ``FallbackClient.timeout``.
        follow_token: Bearer token to attach to the follow-HEAD if the
            target requires auth. Production passes the source's token.

    Returns:
        New headers dict suitable for forwarding to the client.
    """
    resp_headers = dict(response.headers)
    location = resp_headers.get("location") or resp_headers.get("Location")
    if location:
        upstream_url = str(response.request.url)
        absolute_location = urljoin(upstream_url, location)
        for k in list(resp_headers.keys()):
            if k.lower() == "location":
                resp_headers.pop(k, None)
        resp_headers["location"] = absolute_location

    if (
        300 <= response.status_code < 400
        and location
        and not any(k.lower() == "x-linked-size" for k in resp_headers)
    ):
        try:
            async with httpx.AsyncClient(timeout=follow_timeout) as hc:
                # ``identity`` asks HF not to gzip the (empty) HEAD body;
                # otherwise httpx's auto-decoding strips Content-Length
                # from the response and we lose the value we came here
                # to fetch.
                extra_headers = {"Accept-Encoding": "identity"}
                if follow_token:
                    extra_headers["Authorization"] = f"Bearer {follow_token}"
                follow_resp = await hc.head(
                    resp_headers["location"],
                    headers=extra_headers,
                    follow_redirects=False,
                )
            # ``content-length`` and ``etag`` always come from the
            # follow_resp — the 307 itself only carries the redirect
            # body length, which is wrong for the file. ``x-repo-commit``
            # is *additive*: HF's resolve-cache 307 already carries
            # the right value (PR #21 design) and the resolve-cache
            # CDN HEAD response often does not include it, so we
            # only backfill from the follow when the original 307
            # didn't carry it. This keeps the existing PR #21 path
            # working unchanged while supporting non-HF mirrors that
            # emit x-repo-commit-less 307s on resolve.
            replace_keys = ("content-length", "etag")
            for k in [
                k for k in list(resp_headers) if k.lower() in replace_keys
            ]:
                resp_headers.pop(k)
            for k, v in follow_resp.headers.items():
                if k.lower() in replace_keys:
                    resp_headers[k] = v
            has_commit = any(
                k.lower() == "x-repo-commit" for k in resp_headers
            )
            if not has_commit:
                for k, v in follow_resp.headers.items():
                    if k.lower() == "x-repo-commit":
                        resp_headers[k] = v
        except httpx.HTTPError:
            # Extra HEAD failed — return what we have; no worse than
            # the original PR #21 behavior.
            pass

    strip_xet_response_headers(resp_headers)
    resp_headers.update(
        add_source_headers(response, source["name"], source["url"])
    )
    return resp_headers
