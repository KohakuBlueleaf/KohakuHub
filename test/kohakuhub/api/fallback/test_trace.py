"""Targeted unit tests for ``kohakuhub.api.fallback.trace``.

The chain-trace plumbing is exercised end-to-end by ``test_decorators.py``
and ``test_probe_local.py``, but several defensive / edge-case branches
in ``decode_trace_header``, ``record_local_hop``, ``inject_trace_header``
and ``inject_trace_cookie`` aren't reached on the happy path. These
tests pin those branches deliberately so a refactor that breaks
graceful degradation (e.g., raises on malformed input instead of
returning empty) gets caught here rather than in production.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from kohakuhub.api.fallback import trace


# ---------------------------------------------------------------------------
# record_local_hop — early-return when no trace context is active
# ---------------------------------------------------------------------------


def test_record_local_hop_no_trace_context_is_no_op():
    """When no enclosing ``start_trace()`` ran, ``current_trace()`` returns
    ``None`` and ``record_local_hop`` must early-return silently — never
    raise, never mutate any shared state. The chain tester is the only
    surface that explicitly opens a trace; every other request path
    invokes the helper through the fallback decorator and relies on the
    early-return to be safe in non-tester flows."""
    # Reset the ContextVar — start_trace is the only public way in,
    # and we deliberately don't call it.
    trace._chain_trace.set(None)

    # Should not raise; should not append anywhere.
    trace.record_local_hop(decision="LOCAL_HIT", status_code=200, duration_ms=12)


# ---------------------------------------------------------------------------
# decode_trace_header — graceful degradation across malformed inputs
# ---------------------------------------------------------------------------


def test_decode_trace_header_handles_invalid_base64():
    """Invalid base64 payload (e.g. wrong padding, non-ascii bytes)
    must return ``[]`` rather than propagate a ``binascii.Error`` or
    ``UnicodeDecodeError`` — the admin tester reads response headers
    without owning the encoder, so the decoder must tolerate junk."""
    assert trace.decode_trace_header("not-base64-at-all!!!") == []


def test_decode_trace_header_handles_non_json_payload():
    """Valid base64 but the decoded text isn't JSON. Any
    ``json.JSONDecodeError`` must collapse to ``[]``."""
    payload = base64.b64encode(b"plain text, not json").decode("ascii")
    assert trace.decode_trace_header(payload) == []


def test_decode_trace_header_handles_top_level_non_dict():
    """Decoded JSON is valid but not a dict (e.g. a bare list, string,
    number) — the envelope is `{"version": 1, "hops": [...]}` so any
    other top-level shape must classify as malformed."""
    payload = base64.b64encode(json.dumps([1, 2, 3]).encode("utf-8")).decode("ascii")
    assert trace.decode_trace_header(payload) == []


def test_decode_trace_header_handles_dict_without_hops_key():
    """Dict with no ``hops`` key (e.g. only ``version``) → empty list."""
    payload = base64.b64encode(json.dumps({"version": 1}).encode("utf-8")).decode("ascii")
    assert trace.decode_trace_header(payload) == []


def test_decode_trace_header_handles_dict_with_non_list_hops():
    """Dict with ``hops`` of the wrong type (e.g. a string) → empty list.
    The branch at line 267 returns ``hops if isinstance(hops, list) else []``
    so anything non-list collapses to empty."""
    payload = base64.b64encode(
        json.dumps({"version": 1, "hops": "not-a-list"}).encode("utf-8")
    ).decode("ascii")
    assert trace.decode_trace_header(payload) == []


def test_decode_trace_header_round_trips_well_formed_input():
    """Sanity check that the happy-path round-trip still works — paired
    with the malformed cases above so the test file documents the full
    contract."""
    hops = [{"kind": "local", "decision": "LOCAL_MISS"}, {"kind": "source", "decision": "TRY_NEXT_SOURCE"}]
    encoded = trace.encode_trace_header(hops)
    assert trace.decode_trace_header(encoded) == hops


# ---------------------------------------------------------------------------
# inject_trace_header — early-return on empty hops
# ---------------------------------------------------------------------------


def test_inject_trace_header_noop_on_empty_hops():
    """``inject_trace_header`` skips the work when ``hops`` is empty —
    no header gets set, no encode happens. Cheaper than encoding an
    empty envelope onto every response that didn't actually probe a
    chain."""
    response = SimpleNamespace(headers={})
    trace.inject_trace_header(response, [])
    assert trace.X_CHAIN_TRACE not in response.headers


# ---------------------------------------------------------------------------
# inject_trace_cookie — early-return paths
# ---------------------------------------------------------------------------


def test_inject_trace_cookie_noop_on_empty_hops():
    """No hops → no cookie. Same rationale as ``inject_trace_header``:
    don't pay the Set-Cookie bandwidth on requests that didn't probe."""

    class _Headers:
        def __init__(self):
            self.appended: list[tuple[str, str]] = []

        def append(self, name, value):
            self.appended.append((name, value))

    response = SimpleNamespace(headers=_Headers())
    trace.inject_trace_cookie(response, [], "probe-id-abc")
    assert response.headers.appended == []


def test_inject_trace_cookie_noop_when_probe_id_invalid():
    """``sanitize_probe_id`` rejects non-conforming probe ids (charset
    outside RFC-6265 cookie-name-safe). When sanitization returns
    ``None`` the cookie is suppressed entirely — auth-gate to keep
    chain config from leaking via Set-Cookie to anonymous callers."""

    class _Headers:
        def __init__(self):
            self.appended: list[tuple[str, str]] = []

        def append(self, name, value):
            self.appended.append((name, value))

    response = SimpleNamespace(headers=_Headers())
    # A clearly-invalid probe id: contains characters outside the
    # cookie-name-safe set (whitespace, equals signs, etc.).
    trace.inject_trace_cookie(
        response,
        [{"kind": "local"}],
        "invalid probe id with spaces and = signs",
    )
    assert response.headers.appended == []
