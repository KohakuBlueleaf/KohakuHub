"""Tests for fallback utility helpers."""

import httpx
import pytest

from kohakuhub.api.fallback.utils import (
    add_source_headers,
    classify_upstream,
    extract_error_message,
    FallbackDecision,
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
        (302, False),  # 3xx falls through to the default "don't retry" branch
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


def _plain_response(
    status: int, body: bytes = b"", headers: dict | None = None
) -> httpx.Response:
    return httpx.Response(
        status,
        content=body,
        headers=headers or {},
        request=httpx.Request("HEAD", "https://src.local/f"),
    )


def test_build_fallback_attempt_categorizes_known_status_codes():
    src = {"name": "S", "url": "https://s"}
    # Plain 401 (no X-Error-Code) is HF's repo-doesn't-exist shape and
    # must classify as NOT_FOUND; the AUTH path is guarded by the
    # X-Error-Code=GatedRepo header (see the dedicated test below).
    assert (
        build_fallback_attempt(src, response=_plain_response(401))["category"]
        == CATEGORY_NOT_FOUND
    )
    assert (
        build_fallback_attempt(
            src,
            response=_plain_response(401, headers={"X-Error-Code": "GatedRepo"}),
        )["category"]
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


def test_build_fallback_attempt_reads_x_error_code_header():
    """The aggregate layer needs the upstream's X-Error-Code to
    distinguish 'real gated' (401 + GatedRepo) from 'repo missing'
    (bare 401, HF's anti-enumeration shape). Persist it on the
    attempt so `build_aggregate_failure_response` can branch on it."""
    src = {"name": "S", "url": "https://s"}
    attempt = build_fallback_attempt(
        src,
        response=_plain_response(
            401,
            headers={
                "X-Error-Code": "GatedRepo",
                "X-Error-Message": "need auth",
            },
        ),
    )
    assert attempt["category"] == CATEGORY_AUTH
    assert attempt["error_code"] == "GatedRepo"


def test_build_fallback_attempt_persists_not_found_error_code():
    """Upstream EntryNotFound / RepoNotFound / RevisionNotFound ride
    through on the attempt so the aggregate can use them verbatim."""
    src = {"name": "S", "url": "https://s"}
    for code in ("EntryNotFound", "RepoNotFound", "RevisionNotFound"):
        attempt = build_fallback_attempt(
            src,
            response=_plain_response(
                404, headers={"X-Error-Code": code}
            ),
        )
        assert attempt["category"] == CATEGORY_NOT_FOUND
        assert attempt["error_code"] == code


def test_build_aggregate_failure_response_escalates_bare_401_to_repo_not_found():
    """When every attempt returned bare 401 (no GatedRepo code), the
    aggregate is 404 RepoNotFound — hf_hub's own heuristic. Even on
    scope='file' the escalation applies because the upstream is
    telling us the repo itself does not exist."""
    src = {"name": "S", "url": "https://s"}
    attempts = [
        build_fallback_attempt(src, response=_plain_response(401)),
    ]
    resp = build_aggregate_failure_response(attempts, scope="file")
    assert resp.status_code == 404
    assert resp.headers.get("x-error-code") == "RepoNotFound"


def test_build_aggregate_failure_response_plain_404_stays_entry_not_found():
    """For scope='file', a genuine 404 (no bare-401 repo-miss signal)
    still maps to EntryNotFound so the client raises EntryNotFoundError."""
    src = {"name": "S", "url": "https://s"}
    attempts = [build_fallback_attempt(src, response=_plain_response(404))]
    resp = build_aggregate_failure_response(attempts, scope="file")
    assert resp.status_code == 404
    assert resp.headers.get("x-error-code") == "EntryNotFound"


def test_build_aggregate_failure_response_real_gated_stays_auth():
    """A 401 with X-Error-Code=GatedRepo must keep the AUTH path
    (401 GatedRepo) so the client raises GatedRepoError, not
    RepositoryNotFoundError."""
    src = {"name": "S", "url": "https://s"}
    attempts = [
        build_fallback_attempt(
            src,
            response=_plain_response(
                401, headers={"X-Error-Code": "GatedRepo"}
            ),
        )
    ]
    resp = build_aggregate_failure_response(attempts, scope="file")
    assert resp.status_code == 401
    assert resp.headers.get("x-error-code") == "GatedRepo"


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


# ---------------------------------------------------------------------------
# classify_upstream — the matrix from #75. Each row is a parametrized
# case here, anchored to *real* HuggingFace responses captured
# 2026-04-30 (status / X-Error-Code / X-Error-Message). The matrix
# mirrors hf_raise_for_status' priority: X-Error-Code wins over
# numeric status, then 2xx/3xx → BIND_AND_RESPOND, else
# TRY_NEXT_SOURCE.
# ---------------------------------------------------------------------------


_MATRIX_CASES = [
    # ---- BIND_AND_RESPOND (2xx / 3xx) ----
    pytest.param(
        200, {}, FallbackDecision.BIND_AND_RESPOND,
        id="200-ok",
    ),
    pytest.param(
        # HF redirects bert-base-uncased → google-bert/bert-base-uncased.
        307, {"location": "/api/models/google-bert/bert-base-uncased"},
        FallbackDecision.BIND_AND_RESPOND,
        id="307-canonical-name-redirect",
    ),
    pytest.param(
        # HF resolve-cache redirect on a known file.
        307,
        {
            "location": "/api/resolve-cache/models/openai-community/gpt2/abc/config.json",
            "x-linked-size": "1234",
        },
        FallbackDecision.BIND_AND_RESPOND,
        id="307-resolve-cache-redirect-with-x-linked-size",
    ),
    pytest.param(
        302, {"location": "https://elsewhere.example/x"},
        FallbackDecision.BIND_AND_RESPOND,
        id="302-generic-redirect",
    ),
    # ---- BIND_AND_PROPAGATE (X-Error-Code says repo IS here) ----
    pytest.param(
        404,
        {"x-error-code": "EntryNotFound", "x-error-message": "Entry not found"},
        FallbackDecision.BIND_AND_PROPAGATE,
        id="404-EntryNotFound-file-missing-but-repo-here",
    ),
    pytest.param(
        404,
        {
            "x-error-code": "RevisionNotFound",
            "x-error-message": "Invalid rev id: refs",
        },
        FallbackDecision.BIND_AND_PROPAGATE,
        id="404-RevisionNotFound-revision-missing-but-repo-here",
    ),
    pytest.param(
        # HF emits the disabled marker via X-Error-Message *only* (no
        # X-Error-Code is set on these responses); hf_hub matches on
        # the exact string. Routed to TRY_NEXT_SOURCE — a moderation
        # takedown on one source doesn't mean a sibling source can't
        # serve the same-named repo (different KohakuHub mirrors are
        # under different moderation policies). The aggregate layer
        # preserves the disabled marker so an all-disabled chain
        # still raises DisabledRepoError on the hf_hub client.
        403,
        {"x-error-message": "Access to this resource is disabled."},
        FallbackDecision.TRY_NEXT_SOURCE,
        id="disabled-repo-via-magic-x-error-message",
    ),
    pytest.param(
        # X-Error-Code wins over status: same EntryNotFound on 410
        # (Gone) should still bind and propagate.
        410,
        {"x-error-code": "EntryNotFound"},
        FallbackDecision.BIND_AND_PROPAGATE,
        id="410-EntryNotFound-still-binds",
    ),
    # ---- TRY_NEXT_SOURCE — explicit X-Error-Code says "not here" ----
    pytest.param(
        # Authed caller → HF returns 404 + RepoNotFound (not the anon
        # anti-enum 401).
        404,
        {"x-error-code": "RepoNotFound", "x-error-message": "Repository not found"},
        FallbackDecision.TRY_NEXT_SOURCE,
        id="404-RepoNotFound-authed",
    ),
    pytest.param(
        # Anon → HF returns 401 + GatedRepo on a gated repo's resolve URL.
        401,
        {
            "x-error-code": "GatedRepo",
            "x-error-message": "Access to model X is restricted...",
        },
        FallbackDecision.TRY_NEXT_SOURCE,
        id="401-GatedRepo-anonymous",
    ),
    pytest.param(
        # Authed-but-not-in-access-list → HF returns 403 + GatedRepo.
        # Both 401 and 403 forms with GatedRepo header must classify
        # the same way.
        403,
        {
            "x-error-code": "GatedRepo",
            "x-error-message": "...you are not in the authorized list...",
        },
        FallbackDecision.TRY_NEXT_SOURCE,
        id="403-GatedRepo-authed-no-access",
    ),
    # ---- TRY_NEXT_SOURCE — bare statuses ----
    pytest.param(
        # HF anti-enum to anonymous callers asking about a missing
        # repo. The exact message string matters — see hf_hub's
        # `_http.py` "401 is misleading" branch.
        401, {"x-error-message": "Invalid username or password."},
        FallbackDecision.TRY_NEXT_SOURCE,
        id="401-bare-anti-enum-anonymous",
    ),
    pytest.param(
        # Real auth failure (token format invalid). hf_hub specifically
        # excludes this string from its 401→RepoNotFound mapping.
        401,
        {"x-error-message": "Invalid credentials in Authorization header"},
        FallbackDecision.TRY_NEXT_SOURCE,
        id="401-bare-invalid-credentials",
    ),
    pytest.param(403, {}, FallbackDecision.TRY_NEXT_SOURCE, id="403-bare"),
    pytest.param(404, {}, FallbackDecision.TRY_NEXT_SOURCE, id="404-bare"),
    pytest.param(429, {}, FallbackDecision.TRY_NEXT_SOURCE, id="429-rate-limited"),
    pytest.param(500, {}, FallbackDecision.TRY_NEXT_SOURCE, id="500-server-error"),
    pytest.param(502, {}, FallbackDecision.TRY_NEXT_SOURCE, id="502-bad-gateway"),
    pytest.param(
        503, {}, FallbackDecision.TRY_NEXT_SOURCE, id="503-service-unavailable"
    ),
    pytest.param(
        504, {}, FallbackDecision.TRY_NEXT_SOURCE, id="504-gateway-timeout"
    ),
]


@pytest.mark.parametrize(("status", "headers", "expected"), _MATRIX_CASES)
def test_classify_upstream_matrix(status, headers, expected):
    """Every row of the #75 status-code matrix must classify exactly as
    spelled out in the issue. Anchored to actual HuggingFace responses
    captured by direct probe."""
    request = httpx.Request("GET", "https://fallback.local/api/models/x/y")
    response = httpx.Response(status, headers=headers, request=request)
    assert classify_upstream(response) is expected


def test_classify_upstream_timeout_exception_is_try_next_source():
    """Transport-level timeout: no response to look at, so we move on
    to the next source. The aggregate layer maps an all-timeout chain
    to 502."""
    assert (
        classify_upstream(httpx.TimeoutException("read timed out"))
        is FallbackDecision.TRY_NEXT_SOURCE
    )


def test_classify_upstream_connect_error_is_try_next_source():
    """Same for any other transport failure (DNS, refused, reset)."""
    assert (
        classify_upstream(httpx.ConnectError("connection refused"))
        is FallbackDecision.TRY_NEXT_SOURCE
    )


def test_classify_upstream_x_error_code_wins_over_status():
    """The defining property: a 404 with EntryNotFound is
    BIND_AND_PROPAGATE *because* of the header (not the status), while
    a 404 with RepoNotFound is TRY_NEXT_SOURCE — same status, opposite
    decision based purely on X-Error-Code. A bare 404 (no header)
    defaults to TRY_NEXT_SOURCE."""
    request = httpx.Request("GET", "https://fallback.local/x")
    bind_propagate = classify_upstream(
        httpx.Response(
            404, headers={"x-error-code": "EntryNotFound"}, request=request
        )
    )
    next_source = classify_upstream(
        httpx.Response(
            404, headers={"x-error-code": "RepoNotFound"}, request=request
        )
    )
    bare = classify_upstream(httpx.Response(404, request=request))
    assert bind_propagate is FallbackDecision.BIND_AND_PROPAGATE
    assert next_source is FallbackDecision.TRY_NEXT_SOURCE
    assert bare is FallbackDecision.TRY_NEXT_SOURCE


def test_classify_upstream_disabled_message_routes_to_try_next_source():
    """hf_hub matches the disabled-repo X-Error-Message via *equality*
    on the exact string. We route it to TRY_NEXT_SOURCE (matching
    GatedRepo semantics: this layer can't serve, try next). The
    aggregate layer preserves the marker so an all-disabled chain
    still raises DisabledRepoError on the hf_hub client. A
    near-miss (different casing or extra whitespace) doesn't trigger
    DisabledRepoError on hf_hub, so we don't carry a special category
    for it either — it falls into the generic 403 forbidden bucket.
    """
    request = httpx.Request("GET", "https://fallback.local/x")
    exact = httpx.Response(
        403,
        headers={"x-error-message": "Access to this resource is disabled."},
        request=request,
    )
    near_miss = httpx.Response(
        403,
        headers={"x-error-message": "Access to this resource is DISABLED."},
        request=request,
    )
    # Both route to TRY_NEXT_SOURCE — but only ``exact`` carries the
    # CATEGORY_DISABLED tag in the attempt aggregator; the near-miss
    # falls into the generic 403/forbidden bucket. That distinction
    # is exercised by the aggregator-side tests below.
    assert classify_upstream(exact) is FallbackDecision.TRY_NEXT_SOURCE
    assert classify_upstream(near_miss) is FallbackDecision.TRY_NEXT_SOURCE


def test_aggregate_preserves_disabled_marker_so_hf_hub_raises_DisabledRepoError():
    """Companion of the classifier-routes-disabled-to-TRY_NEXT rule:
    when every probed source falls through and at least one had the
    disabled marker, the aggregate must re-emit
    ``X-Error-Message: "Access to this resource is disabled."`` (the
    exact string ``hf_raise_for_status`` keys off) so the hf_hub
    client raises ``DisabledRepoError`` end-to-end."""
    src = {"name": "S", "url": "https://s"}
    disabled_attempt = build_fallback_attempt(
        src,
        response=_plain_response(
            403,
            headers={"X-Error-Message": "Access to this resource is disabled."},
        ),
    )
    # Mix in a generic RepoNotFound to prove disabled wins over
    # not-found in the aggregate priority.
    not_found_attempt = build_fallback_attempt(
        src,
        response=_plain_response(404, headers={"X-Error-Code": "RepoNotFound"}),
    )

    resp = build_aggregate_failure_response([not_found_attempt, disabled_attempt])
    assert resp.status_code == 403
    # No X-Error-Code: hf_hub keys off X-Error-Message for disabled.
    assert resp.headers.get("x-error-code") is None
    assert (
        resp.headers.get("x-error-message")
        == "Access to this resource is disabled."
    )


def test_aggregate_disabled_loses_to_gated_repo():
    """Priority order: AUTH (GatedRepo) > DISABLED. Rationale: a user
    who hits a chain where one source is gated and another is
    disabled has an actionable next step (attach a token for the
    gated source) — that wins over the moderation-takedown signal,
    which isn't directly resolvable by the user."""
    src = {"name": "S", "url": "https://s"}
    gated = build_fallback_attempt(
        src,
        response=_plain_response(401, headers={"X-Error-Code": "GatedRepo"}),
    )
    disabled = build_fallback_attempt(
        src,
        response=_plain_response(
            403,
            headers={"X-Error-Message": "Access to this resource is disabled."},
        ),
    )
    resp = build_aggregate_failure_response([disabled, gated])
    assert resp.status_code == 401
    assert resp.headers.get("x-error-code") == "GatedRepo"


# ===========================================================================
# apply_resolve_head_postprocess (#78 v3): the production HEAD-on-resolve
# response shaping, extracted into utils.py so the chain-tester probe
# can reuse it for byte-identical fidelity.
# ===========================================================================


from kohakuhub.api.fallback.utils import apply_resolve_head_postprocess


def _httpx_response(
    status: int,
    *,
    headers: dict | None = None,
    body: bytes = b"",
    request_url: str = "https://hf.example/api/path",
) -> httpx.Response:
    """Build a stand-in ``httpx.Response`` with the request URL bound.

    The postprocess uses ``response.request.url`` for the urljoin
    base, so we have to wire that up properly — bare ``httpx.Response``
    construction without a request leaves it ``None``.
    """
    req = httpx.Request("HEAD", request_url)
    return httpx.Response(
        status_code=status,
        headers=headers or {},
        content=body,
        request=req,
    )


@pytest.mark.asyncio
async def test_resolve_postprocess_rewrites_relative_location_to_absolute():
    """HF returns 307 with a relative ``Location`` like
    ``/api/resolve-cache/...`` that only resolves on the HF origin.
    Postprocess must rewrite it to absolute against the upstream URL
    so a downstream client can follow it."""
    upstream_resp = _httpx_response(
        307,
        headers={
            "location": "/api/resolve-cache/models/x/y/sha/config.json",
            "x-linked-size": "12345",  # LFS — skips follow-HEAD branch
        },
        request_url="https://huggingface.co/models/x/y/resolve/main/config.json",
    )
    out = await apply_resolve_head_postprocess(
        upstream_resp, {"name": "HF", "url": "https://huggingface.co"},
    )
    assert (
        out["location"]
        == "https://huggingface.co/api/resolve-cache/models/x/y/sha/config.json"
    )


@pytest.mark.asyncio
async def test_resolve_postprocess_skips_follow_head_for_lfs():
    """LFS files carry ``X-Linked-Size``; hf_hub prefers that over
    Content-Length and PR #21's follow-HEAD is unnecessary. Postprocess
    must skip the follow to avoid a wasted upstream HEAD."""
    follow_called = []

    class _SpyClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def head(self, *a, **k):  # pragma: no cover — must NOT run
            follow_called.append((a, k))
            raise AssertionError("follow HEAD must not run for LFS")

    upstream_resp = _httpx_response(
        307,
        headers={
            "location": "https://cdn-lfs.example/path",
            "x-linked-size": "12345",
        },
        request_url="https://huggingface.co/models/x/y/resolve/main/big.bin",
    )
    out = await apply_resolve_head_postprocess(
        upstream_resp, {"name": "HF", "url": "https://huggingface.co"},
    )
    assert follow_called == []
    # X-Source* still added, xet stripped.
    assert out.get("X-Source") == "HF"


@pytest.mark.asyncio
async def test_resolve_postprocess_runs_follow_head_for_non_lfs(monkeypatch):
    """Non-LFS 307 (no X-Linked-Size) → follow-HEAD against the
    rewritten Location to backfill real Content-Length / ETag /
    X-Repo-Commit. PR #21 fix; without it hf_hub trusts the redirect
    body's bogus Content-Length and fails a download consistency check.
    """
    captured_url = []

    class _MockAsyncClient:
        def __init__(self, *a, **k):
            self.timeout = k.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def head(self, url, *, headers=None, follow_redirects=False):
            captured_url.append(url)
            return httpx.Response(
                200,
                headers={
                    "content-length": "999999",
                    "etag": '"real-etag"',
                    "x-repo-commit": "real-sha",
                },
            )

    monkeypatch.setattr(
        "kohakuhub.api.fallback.utils.httpx.AsyncClient", _MockAsyncClient
    )

    upstream_resp = _httpx_response(
        307,
        headers={
            "location": "/api/resolve-cache/models/x/y/sha/config.json",
            "content-length": "278",  # bogus redirect body length
            "etag": '"redirect-etag"',
        },
        request_url="https://huggingface.co/models/x/y/resolve/main/config.json",
    )
    out = await apply_resolve_head_postprocess(
        upstream_resp, {"name": "HF", "url": "https://huggingface.co"},
    )
    # Follow-HEAD ran against the rewritten absolute Location.
    assert captured_url == [
        "https://huggingface.co/api/resolve-cache/models/x/y/sha/config.json"
    ]
    # Real values from follow-HEAD overwrote the redirect-body bogus ones.
    assert out.get("content-length") == "999999"
    assert out.get("etag") == '"real-etag"'
    assert out.get("x-repo-commit") == "real-sha"


@pytest.mark.asyncio
async def test_resolve_postprocess_swallows_follow_head_failure(monkeypatch):
    """If the follow-HEAD itself errors (network / timeout), postprocess
    must return the partially-rewritten headers rather than blow up —
    no worse than pre-PR-#21 behavior."""

    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def head(self, *a, **k):
            raise httpx.ConnectError("synthetic")

    monkeypatch.setattr(
        "kohakuhub.api.fallback.utils.httpx.AsyncClient", _BoomClient
    )

    upstream_resp = _httpx_response(
        307,
        headers={"location": "/api/resolve-cache/x/y/sha/config.json"},
        request_url="https://hf.example/x/y/resolve/main/config.json",
    )
    out = await apply_resolve_head_postprocess(
        upstream_resp, {"name": "HF", "url": "https://hf.example"},
    )
    # Should still get back the rewritten Location at least.
    assert (
        out["location"]
        == "https://hf.example/api/resolve-cache/x/y/sha/config.json"
    )


@pytest.mark.asyncio
async def test_resolve_postprocess_strips_xet_and_adds_x_source():
    """Universal post-processing every fallback HEAD response gets:
    strip ``X-Xet-*`` so hf_hub stays on the classic LFS path, and
    add ``X-Source*`` for telemetry."""
    upstream_resp = _httpx_response(
        200,
        headers={
            "x-xet-hash": "abc",
            "x-xet-foo": "bar",
            "etag": '"plain"',
        },
    )
    out = await apply_resolve_head_postprocess(
        upstream_resp,
        {"name": "Mirror", "url": "https://mirror.example"},
    )
    assert "x-xet-hash" not in {k.lower() for k in out}
    assert "x-xet-foo" not in {k.lower() for k in out}
    assert out.get("X-Source") == "Mirror"
    assert out.get("X-Source-URL") == "https://mirror.example"
    assert out.get("X-Source-Status") == "200"

