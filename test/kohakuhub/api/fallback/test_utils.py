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
