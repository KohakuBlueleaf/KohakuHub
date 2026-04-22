"""Contract tests for Hugging Face Hub features KohakuHub explicitly does not
support.

These surfaces appear in the PR #18 compatibility matrix as "Not Supported /
Not Claimed": Discussions / Pull Requests, Space runtime management,
Collections, and Webhooks. Gradio / datasets / transformers can probe these
endpoints during deploy / load flows, so the *shape* of the "no" matters:

* HTTP 501 (not 404 / 500) so the intent is readable on the wire.
* ``X-Error-Code: NotImplemented`` so downstream observability and error
  dashboards can bucket these consistently rather than mixing them with
  real bugs.
* ``X-Error-Message`` with a concrete, actionable explanation — this is
  what ends up inside the ``HfHubHTTPError`` the user sees.
* ``X-Request-Id`` for correlation with backend logs (the middleware applies
  to every response, including these).

Any regression here is a compat-UX regression: the user still gets "your
request failed", but loses the "because KohakuHub intentionally does not
implement feature X" hint.

This module also verifies the *client-visible* half of the contract: when
the user calls `huggingface_hub.HfApi.restart_space(...)` etc. against
KohakuHub, the traceback they get must contain our `X-Error-Message`
verbatim. Without that, the raw HTTP 501 is indistinguishable from a
network glitch.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError


@pytest.mark.parametrize(
    "method, path, feature_hint",
    [
        # Discussions / PRs
        ("GET", "/api/models/owner/demo-model/discussions", "discussions"),
        ("POST", "/api/models/owner/demo-model/discussions", "discussions"),
        ("GET", "/api/datasets/owner/demo-dataset/discussions/1", "discussions"),
        (
            "POST",
            "/api/models/owner/demo-model/discussions/1/comment",
            "discussions",
        ),
        (
            "POST",
            "/api/models/owner/demo-model/discussions/1/merge",
            "discussions",
        ),
        # Space runtime management
        ("GET", "/api/spaces/owner/demo-space/runtime", "runtime"),
        ("POST", "/api/spaces/owner/demo-space/restart", "restart"),
        ("POST", "/api/spaces/owner/demo-space/sleep", "sleep"),
        ("POST", "/api/spaces/owner/demo-space/hardware", "hardware"),
        ("POST", "/api/spaces/owner/demo-space/secrets", "secrets"),
        ("POST", "/api/spaces/owner/demo-space/variables", "variables"),
        # Collections
        ("GET", "/api/collections", "collections"),
        ("POST", "/api/collections", "collections"),
        ("GET", "/api/collections/some-slug-123", "collections"),
        ("POST", "/api/collections/some-slug-123/item", "collections"),
        # Webhooks
        ("GET", "/api/settings/webhooks", "webhooks"),
        ("POST", "/api/settings/webhooks", "webhooks"),
        ("DELETE", "/api/settings/webhooks/abc", "webhooks"),
    ],
)
async def test_unsupported_feature_returns_501_with_hf_headers(
    owner_client, method, path, feature_hint
):
    response = await owner_client.request(method, path)
    assert response.status_code == 501, (
        f"{method} {path} should be 501 Not Implemented, got {response.status_code}: "
        f"{response.text[:200]}"
    )
    assert response.headers.get("x-error-code") == "NotImplemented", (
        f"{method} {path} missing X-Error-Code header; got headers: "
        f"{dict(response.headers)}"
    )
    error_message = response.headers.get("x-error-message", "")
    assert feature_hint.lower() in error_message.lower(), (
        f"{method} {path} X-Error-Message must mention the feature "
        f"({feature_hint!r}); got: {error_message!r}"
    )
    # Middleware always stamps a request id — every response, success or error.
    assert response.headers.get("x-request-id"), (
        f"{method} {path} missing X-Request-Id; expected middleware to stamp it"
    )


async def test_unsupported_route_does_not_shadow_real_endpoints(owner_client):
    """Catch-all 501 routes must not intercept legitimate endpoints.
    ``/api/settings/webhooks`` would be adjacent to ``/api/settings/user`` in
    the path tree; regression here would 501 real settings too."""
    # Real settings endpoint: the user-settings GET — exists and must not 501.
    response = await owner_client.get("/api/settings/user")
    assert response.status_code != 501, (
        "Real /api/settings/user must not be caught by the not-implemented "
        f"catch-all; got {response.status_code}"
    )


async def test_unsupported_feature_error_has_json_safe_header_message(
    owner_client,
):
    """``X-Error-Message`` is a plain ASCII HTTP header — no newlines,
    no tabs. The sanitizer in hf.py collapses whitespace, so regressions
    that bypass the helper would show up as multi-line headers, which
    most HTTP clients reject silently."""
    response = await owner_client.get("/api/models/owner/demo-model/discussions")
    message = response.headers.get("x-error-message", "")
    assert "\n" not in message and "\r" not in message and "\t" not in message
    assert message, "X-Error-Message must not be empty on a 501"


# ---------------------------------------------------------------------------
# Real huggingface_hub-driven checks
# ---------------------------------------------------------------------------
#
# The HTTP-level parametrized block above verifies the server contract.
# These tests verify the *user-visible* half: when someone calls a real
# `HfApi` method that maps to one of these endpoints, the `HfHubHTTPError`
# they see in their traceback must contain our X-Error-Message verbatim.
# If the text is swallowed, the user is left with a bare 501 and no clue
# why — which is exactly the UX we are trying to avoid.


def _assert_server_message_mentions(
    exc: HfHubHTTPError, feature_hint: str
) -> None:
    """Pin both `HfHubHTTPError.server_message` (read from X-Error-Message
    by the huggingface_hub client) and the final `str(exc)` traceback.

    `server_message` is the canonical place to check — huggingface_hub
    surfaces it on the exception instance. `str(exc)` is the secondary
    check because it's literally what prints in tracebacks; if that
    doesn't mention the feature, the end-user sees a useless error.
    """
    server_msg = (exc.server_message or "").lower()
    traceback_msg = str(exc).lower()
    hint = feature_hint.lower()
    assert hint in server_msg or hint in traceback_msg, (
        f"HfHubHTTPError must mention {feature_hint!r} in either "
        f"server_message or traceback text; got server_message="
        f"{exc.server_message!r}, str={str(exc)[:300]!r}"
    )
    assert "kohakuhub" in server_msg or "kohakuhub" in traceback_msg, (
        "The user-visible error must identify KohakuHub as the reason for "
        "the 'not implemented' — otherwise users cannot tell whether this "
        "is a hub-side refusal or a library bug. Got: "
        f"server_message={exc.server_message!r}"
    )


async def test_hf_api_get_repo_discussions_raises_readable_not_implemented(
    live_server_url, hf_api_token
):
    """`HfApi.get_repo_discussions` is called by gradio / community-tab
    UIs. Users running against KohakuHub need to see a clear "discussions
    is not supported" message — not a generic 501 traceback."""
    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    with pytest.raises(HfHubHTTPError) as excinfo:
        await asyncio.to_thread(
            lambda: list(api.get_repo_discussions(repo_id="owner/demo-model"))
        )
    _assert_server_message_mentions(excinfo.value, "discussions")


async def test_hf_api_create_discussion_raises_readable_not_implemented(
    live_server_url, hf_api_token
):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    with pytest.raises(HfHubHTTPError) as excinfo:
        await asyncio.to_thread(
            api.create_discussion,
            repo_id="owner/demo-model",
            title="some discussion",
            description="body",
        )
    _assert_server_message_mentions(excinfo.value, "discussions")


async def test_hf_api_restart_space_raises_readable_not_implemented(
    live_server_url, hf_api_token
):
    """`HfApi.restart_space` is called by Space lifecycle automation. The
    user-facing error must say "Space restart is not supported" so their
    deploy scripts can fail loudly and clearly instead of retrying
    against a generic 501."""
    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    # No need to actually create a Space — the 501 catch-all fires before
    # any lookup, and that's the whole point.
    with pytest.raises(HfHubHTTPError) as excinfo:
        await asyncio.to_thread(api.restart_space, "owner/demo-space")
    _assert_server_message_mentions(excinfo.value, "restart")


async def test_hf_api_pause_space_raises_readable_not_implemented(
    live_server_url, hf_api_token
):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    with pytest.raises(HfHubHTTPError) as excinfo:
        await asyncio.to_thread(api.pause_space, "owner/demo-space")
    # hf_hub calls POST /spaces/.../pause — our catch-all fires with the
    # generic space-runtime reason, which still names "Space" explicitly.
    _assert_server_message_mentions(excinfo.value, "space")


async def test_hf_api_add_space_secret_raises_readable_not_implemented(
    live_server_url, hf_api_token
):
    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    with pytest.raises(HfHubHTTPError) as excinfo:
        await asyncio.to_thread(
            api.add_space_secret, "owner/demo-space", "MY_SECRET", "value"
        )
    _assert_server_message_mentions(excinfo.value, "secrets")


async def test_direct_webhooks_http_surface_returns_not_implemented(
    live_server_url, hf_api_token
):
    """`HfApi.list_webhooks` in huggingface_hub ignores the caller-supplied
    ``endpoint`` and hard-codes ``constants.ENDPOINT`` (huggingface.co),
    so it never actually reaches KohakuHub. The direct-HTTP contract
    below is the only meaningful pin for the webhooks-not-implemented
    surface at the KohakuHub boundary.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{live_server_url}/api/settings/webhooks",
            headers={"Authorization": f"Bearer {hf_api_token}"},
        )
    assert response.status_code == 501
    assert response.headers.get("x-error-code") == "NotImplemented"
    message = response.headers.get("x-error-message", "")
    assert "webhook" in message.lower()
    assert "kohakuhub" in message.lower()


async def test_direct_discussions_http_surface_surfaces_x_error_fields(
    live_server_url, hf_api_token
):
    """Some library code paths (and humans with curl) hit these endpoints
    directly via httpx rather than through `HfApi`. Pin that the raw
    headers carry both X-Error-Code and X-Error-Message on the 501 so
    that error dashboards, log ingesters, and client retry logic can
    distinguish "we don't implement this" from "transient backend
    failure"."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{live_server_url}/api/models/owner/demo-model/discussions",
            headers={"Authorization": f"Bearer {hf_api_token}"},
        )
    assert response.status_code == 501
    assert response.headers.get("x-error-code") == "NotImplemented"
    message = response.headers.get("x-error-message", "")
    assert "discussion" in message.lower()
    assert "kohakuhub" in message.lower()
    # Middleware applies to live_server_url responses too.
    assert response.headers.get("x-request-id"), (
        "Live-server responses must also carry X-Request-Id"
    )
