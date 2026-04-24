"""Utility functions for fallback system."""

from typing import Optional

import httpx
from fastapi.responses import JSONResponse

from kohakuhub.logger import get_logger

logger = get_logger("FALLBACK_UTILS")


# Category labels attached to each fallback attempt, used for aggregation.
# Kept intentionally coarse — finer upstream semantics (e.g. "repo gated"
# vs "org access revoked") ride in the `message` field pulled from the
# upstream body, because there is no portable way to distinguish them.
CATEGORY_AUTH = "auth"  # HTTP 401 — authentication required
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
    del error_message  # reserved for a later refinement; keep in the signature
    if error_code == "GatedRepo":
        return CATEGORY_AUTH
    if error_code in ("RepoNotFound", "EntryNotFound", "RevisionNotFound"):
        return CATEGORY_NOT_FOUND
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
