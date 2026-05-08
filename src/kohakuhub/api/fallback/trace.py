"""Per-request chain trace, emitted as ``X-Chain-Trace`` response header.

Lets the admin chain-tester (and any external client) read the actual
chain a production request travelled — local backend first, then each
fallback source consulted, with status / X-Error-Code / decision /
duration per hop.

Storage is a ``contextvars.ContextVar`` so concurrent requests don't
overlap. The decorator (``with_repo_fallback``) bootstraps a fresh
trace for its request, records the local hop after calling the wrapped
handler, lets the fallback layer (``operations.try_fallback_*``)
append source hops via ``record_source_hop``, then encodes the final
trace into a single ``X-Chain-Trace: <base64-JSON>`` header on the
outgoing response.

Header format:

    X-Chain-Trace: <base64(json({"version": 1, "hops": [HOP, ...]}))>

Each HOP:

    {
        "kind": "local" | "fallback",
        "source_name": "local" | "<configured source name>",
        "source_url": null | "<source url>",
        "source_type": null | "huggingface" | "kohakuhub",
        "method": "GET" | "HEAD" | "POST",
        "upstream_path": null | "<probe path>",
        "status_code": 200 | null,
        "x_error_code": null | "EntryNotFound" | ...,
        "x_error_message": null | "...",
        "decision": "LOCAL_HIT" | "LOCAL_FILTERED" | "LOCAL_MISS"
                  | "LOCAL_OTHER_ERROR"
                  | "BIND_AND_RESPOND" | "BIND_AND_PROPAGATE"
                  | "TRY_NEXT_SOURCE" | "TIMEOUT" | "NETWORK_ERROR",
        "duration_ms": 12,
        "error": null | "<message>",
    }

The chain stops at the first ``LOCAL_HIT`` / ``LOCAL_FILTERED`` /
``BIND_AND_RESPOND`` / ``BIND_AND_PROPAGATE`` / non-404 ``LOCAL_OTHER_ERROR``.
``LOCAL_MISS`` is the gate condition that causes the fallback chain to
be consulted; subsequent hops are ``fallback`` ``kind``.
"""
from __future__ import annotations

import base64
import json
import re
from contextvars import ContextVar
from typing import Any, Optional

import httpx

from kohakuhub.logger import get_logger
from kohakuhub.api.fallback.utils import FallbackDecision, classify_upstream

logger = get_logger("FALLBACK_TRACE")

X_CHAIN_TRACE = "X-Chain-Trace"

# Header the chain tester sends to ask the backend to *also* set the
# trace as a per-probe cookie. The cookie is the chain tester's only
# reliable pickup channel after a redirect-follow round trip — see
# ``inject_trace_cookie`` for the rationale (W3C Fetch spec strips
# redirect-chain response headers from JS).
PROBE_ID_HEADER = "X-Khub-Probe-Id"

# Per-probe cookie name = ``COOKIE_NAME_PREFIX + sanitized probe_id``.
# Prefix is namespaced under ``_khub_`` so it's clearly a KohakuHub-
# internal debug cookie rather than a session/auth cookie.
COOKIE_NAME_PREFIX = "_khub_chain_trace_"

# Cookie ``Max-Age``. Set deliberately long (5 minutes) so a slow
# fallback (large redirect chain, sluggish upstream, operator pauses
# to look at devtools mid-probe) still has a usable trace cookie when
# the SPA finally reads it. Cleaned up explicitly by the SPA after
# pickup, so a long Max-Age doesn't leak.
COOKIE_MAX_AGE_SECONDS = 300

# Probe-id sanitization. Cookie names per RFC 6265 must avoid certain
# characters (semicolons, commas, whitespace, etc.). We constrain to
# ``[A-Za-z0-9_-]{1,64}`` which fits UUIDs, hex strings, and short
# generated tokens. Anything else gets dropped to ``None`` so a
# malicious / malformed probe id can't inject an extra Set-Cookie line
# or expand into an unbounded cookie name.
#
# Examples — accepted:
#     "d6d1f117-2977-4101-af89-caddb11ef394"  (uuid v4)
#     "abc-123_def"                            (alphanumerics + - _)
#     "a"                                       (single char)
#
# Examples — rejected (sanitize_probe_id returns None):
#     "abc;def=evil"           (cookie-attribute injection)
#     "abc def"                (whitespace)
#     "abc,def"                (comma)
#     "abc\nSet-Cookie:x=y"    (CRLF injection)
#     "x" * 65                  (over the 64-char cap)
#     ""                        (empty)
_PROBE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def sanitize_probe_id(probe_id: Optional[str]) -> Optional[str]:
    """Validate the probe id supplied via the X-Khub-Probe-Id header.

    Returns the id when it matches ``[A-Za-z0-9_-]{1,64}``, otherwise
    ``None`` (suppresses cookie injection).
    """
    if probe_id and _PROBE_ID_PATTERN.fullmatch(probe_id):
        return probe_id
    return None


def cookie_name_for_probe(probe_id: str) -> str:
    """Per-probe cookie name namespaced under the chain-tester prefix.

    Concurrent probes use distinct names so reading one probe's cookie
    after redirect-follow never picks up a stale value from another
    probe in flight.
    """
    return f"{COOKIE_NAME_PREFIX}{probe_id}"

# ContextVar default = None means "no active trace; recording is a no-op".
# The decorator at request entry sets a fresh list. ``record_*`` helpers
# append to whatever list is active. The decorator at request exit reads
# the list and encodes the final header.
_chain_trace: ContextVar[Optional[list[dict]]] = ContextVar(
    "fallback_chain_trace", default=None
)


def start_trace() -> list[dict]:
    """Begin recording a chain trace for the current request.

    Returns the underlying list so the caller can read it later — the
    decorator pattern is:

        hops = start_trace()
        # ... handler runs, helpers append to hops ...
        inject_trace_header(response, hops)

    Calling ``start_trace`` while a trace is already active replaces it
    (no nesting — admin endpoints don't fan out).
    """
    hops: list[dict] = []
    _chain_trace.set(hops)
    return hops


def current_trace() -> Optional[list[dict]]:
    """Return the active hop list, or ``None`` if no trace is recording."""
    return _chain_trace.get()


def record_local_hop(
    *,
    decision: str,
    status_code: Optional[int],
    x_error_code: Optional[str] = None,
    x_error_message: Optional[str] = None,
    duration_ms: int,
    error: Optional[str] = None,
) -> None:
    """Record the local-backend hop. Always the first hop in the trace."""
    hops = current_trace()
    if hops is None:
        return
    hops.append(
        {
            "kind": "local",
            "source_name": "local",
            "source_url": None,
            "source_type": None,
            "method": None,
            "upstream_path": None,
            "status_code": status_code,
            "x_error_code": x_error_code,
            "x_error_message": x_error_message,
            "decision": decision,
            "duration_ms": duration_ms,
            "error": error,
        }
    )


def record_source_hop(
    source: dict,
    *,
    method: str,
    upstream_path: str,
    response: Optional[httpx.Response] = None,
    decision: Optional[FallbackDecision] = None,
    duration_ms: int,
    error: Optional[str] = None,
    transport_decision: Optional[str] = None,
) -> None:
    """Record one fallback-source attempt.

    Two call shapes:

    - Normal HTTP response: pass ``response`` (and optionally a
      pre-computed ``decision`` to avoid re-classifying); status,
      X-Error-Code, X-Error-Message are read from the response headers.

    - Transport failure: pass ``transport_decision`` (``"TIMEOUT"`` or
      ``"NETWORK_ERROR"``) plus ``error`` describing the exception;
      ``response`` is ``None``, status fields default to ``None``.
    """
    hops = current_trace()
    if hops is None:
        return

    if response is not None:
        decision_name = (decision or classify_upstream(response)).name
        status = response.status_code
        x_code = response.headers.get("x-error-code")
        x_msg = response.headers.get("x-error-message")
    else:
        decision_name = transport_decision or "NETWORK_ERROR"
        status = None
        x_code = None
        x_msg = None

    hops.append(
        {
            "kind": "fallback",
            "source_name": source.get("name") or source.get("url", "?"),
            "source_url": source.get("url"),
            "source_type": source.get("source_type"),
            "method": method,
            "upstream_path": upstream_path,
            "status_code": status,
            "x_error_code": x_code,
            "x_error_message": x_msg,
            "decision": decision_name,
            "duration_ms": duration_ms,
            "error": error,
        }
    )


def encode_trace_header(hops: list[dict]) -> str:
    """Encode a hop list to the ``X-Chain-Trace`` header value.

    Wraps the hop list in a small envelope so future versions can add
    fields without breaking parsers.
    """
    payload = json.dumps({"version": 1, "hops": hops}, separators=(",", ":"))
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def decode_trace_header(header_value: str) -> list[dict]:
    """Decode an ``X-Chain-Trace`` header value back into a hop list.

    Tolerates malformed input (returns ``[]``) so a caller never has to
    catch — particularly relevant for the admin tester UI that reads
    response headers without owning the encoder.
    """
    try:
        decoded = base64.b64decode(header_value.encode("ascii")).decode("utf-8")
        data = json.loads(decoded)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    hops = data.get("hops")
    return hops if isinstance(hops, list) else []


def inject_trace_header(response: Any, hops: list[dict]) -> None:
    """Set ``X-Chain-Trace`` on a Response (best-effort).

    Works on Starlette / FastAPI ``Response`` and on objects with a
    mutable ``headers`` mapping. No-ops gracefully if the target
    doesn't have a ``headers`` attribute we can write to (e.g. some
    third-party response stand-ins).
    """
    if not hops:
        return
    header_value = encode_trace_header(hops)
    try:
        response.headers[X_CHAIN_TRACE] = header_value
    except Exception:  # pragma: no cover — defensive only
        logger.debug("inject_trace_header: response has no writable headers")


def inject_trace_into_exception_headers(
    exc_headers: Optional[dict],
    hops: list[dict],
    probe_id: Optional[str] = None,
) -> dict:
    """Build a headers dict for an ``HTTPException`` re-raise, gated on
    the caller having sent ``X-Khub-Probe-Id``.

    ``HTTPException`` carries a flat ``headers`` mapping. The chain
    tester reads the trace from the re-raised exception's headers, so
    we attach ``X-Chain-Trace`` plus a per-probe ``Set-Cookie`` only
    when ``probe_id`` is present — same auth gate the success path
    uses (see ``_attach_trace_to_result``). Without the gate, an
    anonymous caller hitting an error path could decode the chain
    config from the header.
    """
    out = dict(exc_headers or {})
    sanitized = sanitize_probe_id(probe_id)
    if hops and sanitized:
        encoded = encode_trace_header(hops)
        out[X_CHAIN_TRACE] = encoded
        out["Set-Cookie"] = _build_trace_cookie_value(sanitized, encoded)
    return out


def _build_trace_cookie_value(probe_id: str, encoded_trace: str) -> str:
    """Construct one ``Set-Cookie`` header value carrying the trace.

    Attributes:
    - ``Max-Age=300``: 5 minutes — long enough to outlive a slow
      fallback chain or a paused devtools session.
    - ``Path=/``: visible to any SPA route on the same origin.
    - ``SameSite=Lax``: not sent on cross-site requests; chain tester
      runs same-origin so this is harmless.
    """
    return (
        f"{cookie_name_for_probe(probe_id)}={encoded_trace}; "
        f"Max-Age={COOKIE_MAX_AGE_SECONDS}; Path=/; SameSite=Lax"
    )


def inject_trace_cookie(
    response: Any, hops: list[dict], probe_id: Optional[str]
) -> None:
    """Set the per-probe trace cookie on ``response`` (best effort).

    Two-channel design rationale: ``inject_trace_header`` already puts
    the encoded trace on ``X-Chain-Trace`` for everyone (curl, hf_hub,
    other debug tooling) — that's the universal channel. But the W3C
    Fetch spec explicitly strips redirect-chain response headers from
    JS visibility (``opaqueredirect`` filtered response, status=0,
    headers list empty), and there's no browser API that bypasses it
    (verified across fetch / XHR / Service Worker / iframe / Resource
    Timing / Server-Timing — see the PR review thread). So once a
    probe walks through a 3xx, the SPA can never read X-Chain-Trace
    via JS. A cookie sidesteps the spec restriction because it lives
    in the cookie jar rather than on the response object.

    The cookie name is namespaced per ``probe_id`` so concurrent
    probes don't trample each other's traces; the SPA generates a
    fresh UUID per call and only reads its own.

    Set-only when ``probe_id`` was supplied — non-tester clients
    don't set the header, don't pay the Set-Cookie bandwidth.
    """
    if not hops:
        return
    sanitized = sanitize_probe_id(probe_id)
    if not sanitized:
        return
    encoded = encode_trace_header(hops)
    cookie_line = _build_trace_cookie_value(sanitized, encoded)
    try:
        # Append the raw Set-Cookie header line directly rather than
        # going through ``response.set_cookie`` (Starlette's helper
        # delegates to ``http.cookies.SimpleCookie`` which wraps
        # values containing ``=`` in double quotes — RFC 6265 allows
        # ``=`` unquoted in cookie-octets but SimpleCookie's
        # ``_LegalChars`` set is overly conservative). Quoted values
        # would force the SPA to strip them before ``atob``, which
        # the SPA does defensively anyway, but cleaner wire bytes
        # don't hurt.
        if hasattr(response, "headers") and hasattr(response.headers, "append"):
            response.headers.append("Set-Cookie", cookie_line)
        else:  # pragma: no cover — defensive fallback for plain dicts
            response.headers["Set-Cookie"] = cookie_line
    except Exception:  # pragma: no cover — defensive
        logger.debug("inject_trace_cookie: failed to set cookie")
