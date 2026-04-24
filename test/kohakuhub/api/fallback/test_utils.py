"""Tests for fallback utility helpers."""

import httpx
import pytest

from kohakuhub.api.fallback.utils import (
    add_source_headers,
    extract_error_message,
    is_client_error,
    is_not_found_error,
    is_server_error,
    should_retry_source,
    strip_xet_response_headers,
)


def _response(status_code: int, *, json=None, text: str = "") -> httpx.Response:
    request = httpx.Request("GET", "https://fallback.local/resource")
    if json is not None:
        return httpx.Response(status_code, json=json, request=request)
    return httpx.Response(status_code, text=text, request=request)


@pytest.mark.parametrize(
    ("status_code", "not_found", "client_error", "server_error"),
    [
        (200, False, False, False),
        (404, True, True, False),
        (410, True, True, False),
        (429, False, True, False),
        (503, False, False, True),
    ],
)
def test_status_helpers_cover_common_ranges(status_code, not_found, client_error, server_error):
    response = _response(status_code)

    assert is_not_found_error(response) is not_found
    assert is_client_error(response) is client_error
    assert is_server_error(response) is server_error


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"error": "broken"}, "broken"),
        ({"message": "boom"}, "boom"),
        ({"detail": "not allowed"}, "not allowed"),
        ({"msg": "missing"}, "missing"),
        ({"detail": {"message": "nested"}}, "nested"),
        ({"unexpected": True}, "{'unexpected': True}"),
    ],
)
def test_extract_error_message_prefers_common_error_fields(payload, expected):
    assert extract_error_message(_response(400, json=payload)) == expected


def test_extract_error_message_falls_back_to_text_and_status():
    text_response = _response(500, text="plain failure")
    empty_response = _response(502)

    assert extract_error_message(text_response) == "plain failure"
    assert extract_error_message(empty_response) == "HTTP 502"


@pytest.mark.parametrize(
    ("status_code", "should_retry"),
    [
        (200, False),
        (400, False),
        (401, False),
        (403, False),
        (404, True),
        (408, True),
        (500, True),
        (504, True),
        (524, True),
    ],
)
def test_should_retry_source_uses_status_classification(status_code, should_retry):
    assert should_retry_source(_response(status_code)) is should_retry


def test_add_source_headers_reports_external_source_metadata():
    response = _response(206)

    assert add_source_headers(response, "Mirror", "https://mirror.local") == {
        "X-Source": "Mirror",
        "X-Source-URL": "https://mirror.local",
        "X-Source-Status": "206",
    }


def test_strip_xet_response_headers_removes_all_xet_signals():
    headers = {
        "etag": '"deadbeef"',
        "X-Xet-Hash": "abc123",
        "X-Xet-Refresh-Route": "/api/models/owner/repo/xet-read-token/sha",
        "X-Xet-Cas-Url": "https://cas-bridge.xethub.hf.co",
        "X-Xet-Access-Token": "cas-tok",
        "X-Xet-Expiration": "1800000000",
        "x-linked-etag": '"keep-me"',  # LFS-related, not xet; must stay
        "link": '<https://cas/auth>; rel="xet-auth", <https://next>; rel="next"',
    }

    strip_xet_response_headers(headers)

    assert "X-Xet-Hash" not in headers
    assert "X-Xet-Refresh-Route" not in headers
    assert "X-Xet-Cas-Url" not in headers
    assert "X-Xet-Access-Token" not in headers
    assert "X-Xet-Expiration" not in headers
    # Non-Xet headers untouched
    assert headers["etag"] == '"deadbeef"'
    assert headers["x-linked-etag"] == '"keep-me"'
    # Link relation "xet-auth" stripped, "next" kept
    assert "xet-auth" not in headers["link"].lower()
    assert 'rel="next"' in headers["link"]


def test_strip_xet_response_headers_case_insensitive_matching():
    headers = {
        "x-xet-hash": "abc",          # lowercase
        "X-XET-REFRESH-ROUTE": "/r",  # uppercase
        "X-Xet-Cas-Url": "https://c", # mixed
        "Content-Type": "application/json",
    }

    strip_xet_response_headers(headers)

    assert headers == {"Content-Type": "application/json"}


def test_strip_xet_response_headers_removes_sole_xet_link_entirely():
    headers = {
        "link": '<https://cas/auth>; rel="xet-auth"',
    }

    strip_xet_response_headers(headers)

    assert "link" not in headers  # link had only xet-auth, should be dropped


def test_strip_xet_response_headers_is_noop_without_xet_signals():
    original = {
        "etag": '"abc"',
        "x-repo-commit": "sha",
        "link": '<https://next>; rel="next"',
        "content-type": "text/plain",
    }
    headers = dict(original)

    strip_xet_response_headers(headers)

    assert headers == original


# -------------------------------------------------------------------------
# Aggregated fallback failure helpers (new for the upstream-error-
# classification fix). The loop-level behavior is already covered by
# test_operations; these unit tests cover the edge-case branches that
# only defensive callers would hit.
# -------------------------------------------------------------------------


from kohakuhub.api.fallback.utils import (
    CATEGORY_AUTH,
    CATEGORY_FORBIDDEN,
    CATEGORY_NETWORK,
    CATEGORY_NOT_FOUND,
    CATEGORY_OTHER,
    CATEGORY_SERVER,
    CATEGORY_TIMEOUT,
    build_aggregate_failure_response,
    build_fallback_attempt,
)


def _plain_response(status: int, body: bytes = b"") -> httpx.Response:
    return httpx.Response(
        status,
        content=body,
        request=httpx.Request("HEAD", "https://src.local/f"),
    )


def test_build_fallback_attempt_categorizes_known_status_codes():
    src = {"name": "S", "url": "https://s"}
    assert (
        build_fallback_attempt(src, response=_plain_response(401))["category"]
        == CATEGORY_AUTH
    )
    assert (
        build_fallback_attempt(src, response=_plain_response(403))["category"]
        == CATEGORY_FORBIDDEN
    )
    assert (
        build_fallback_attempt(src, response=_plain_response(404))["category"]
        == CATEGORY_NOT_FOUND
    )
    assert (
        build_fallback_attempt(src, response=_plain_response(410))["category"]
        == CATEGORY_NOT_FOUND
    )
    assert (
        build_fallback_attempt(src, response=_plain_response(503))["category"]
        == CATEGORY_SERVER
    )


def test_build_fallback_attempt_falls_through_on_unclassifiable_status():
    """Any status that isn't in the enumerated buckets (e.g. an
    I'm-a-teapot or an odd client error a mirror might invent) gets the
    ``CATEGORY_OTHER`` label so the aggregate still has a consistent
    shape and the caller can still display the message."""
    src = {"name": "S", "url": "https://s"}
    attempt = build_fallback_attempt(src, response=_plain_response(418))
    assert attempt["category"] == CATEGORY_OTHER
    assert attempt["status"] == 418


def test_build_fallback_attempt_contract_violation_returns_safe_default():
    """If the caller passes none of response/timeout/network we still
    return a well-formed attempt dict with CATEGORY_OTHER so the
    aggregate loop can't swallow an exception path silently."""
    attempt = build_fallback_attempt({"name": "S", "url": "https://s"})
    assert attempt["status"] is None
    assert attempt["category"] == CATEGORY_OTHER
    assert attempt["message"] == ""
    assert attempt["name"] == "S"


def test_build_fallback_attempt_truncates_very_long_upstream_messages():
    """A pathological upstream that returns a multi-MB error body
    cannot be allowed to blow up response headers or body size. The
    per-attempt message is capped (see MAX_ATTEMPT_MESSAGE_LEN)."""
    src = {"name": "S", "url": "https://s"}
    huge = "x" * 5000
    attempt = build_fallback_attempt(
        src, response=_plain_response(500, body=huge.encode())
    )
    assert len(attempt["message"]) <= 600  # cap is 500, allow a bit of slack


def test_build_fallback_attempt_records_timeout_without_http_status():
    import httpx

    src = {"name": "S", "url": "https://s"}
    attempt = build_fallback_attempt(src, timeout=httpx.TimeoutException("slow"))
    assert attempt["status"] is None
    assert attempt["category"] == CATEGORY_TIMEOUT
    assert "slow" in attempt["message"]


def test_build_fallback_attempt_records_generic_network_error():
    src = {"name": "S", "url": "https://s"}
    attempt = build_fallback_attempt(src, network=ConnectionResetError("reset"))
    assert attempt["status"] is None
    assert attempt["category"] == CATEGORY_NETWORK
    assert "reset" in attempt["message"]


def test_build_aggregate_failure_response_empty_attempts_is_generic_502():
    """No recorded attempts is a contract violation (caller should
    return None in that case), but we still produce a well-formed 502
    rather than a KeyError or a nonsensical 401."""
    resp = build_aggregate_failure_response([])
    assert resp.status_code == 502
    assert resp.headers.get("x-error-code") is None
