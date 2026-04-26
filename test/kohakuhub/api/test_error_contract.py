"""HuggingFace-compatible error contract tests.

The ``huggingface_hub`` client surfaces errors through
``hf_raise_for_status``, which reads three specific headers from the
backend response:

* ``X-Error-Code`` — drives named-exception dispatch (``RepositoryNotFoundError``,
  ``RevisionNotFoundError``, ``EntryNotFoundError``, ``GatedRepoError``).
* ``X-Error-Message`` — becomes ``HfHubHTTPError.server_message``.
* ``X-Request-Id`` (falling back to ``X-Amzn-Trace-Id`` / ``X-Amz-Cf-Id``) —
  becomes ``HfHubHTTPError.request_id``, shown in the user traceback as
  ``(Request ID: …)``.

This module pins the contract across a representative set of 4xx paths
(plus a 200 sanity check for X-Request-Id). Regressions here break
observability and break the named-exception codepath, both of which
degrade the user experience silently — by the time someone notices, a
release has already shipped.
"""

from __future__ import annotations

import httpx
import pytest


# ---------------------------------------------------------------------------
# X-Request-Id — middleware must stamp every response
# ---------------------------------------------------------------------------


async def test_request_id_present_on_successful_response(owner_client):
    """Every successful response must carry X-Request-Id. The huggingface_hub
    client stores this on the exception if one is later raised from the
    same session — missing request ids make production debugging painful."""
    response = await owner_client.get("/api/whoami-v2")
    assert response.status_code == 200
    assert response.headers.get("x-request-id"), (
        "X-Request-Id missing on 200 whoami-v2; middleware regression"
    )


async def test_request_id_present_on_error_response(client):
    """401 from an unauthenticated whoami-v2 must still carry a request id
    — HF's HfHubHTTPError pulls it into the formatted message precisely
    *because* errors are when you need correlation the most."""
    response = await client.get("/api/whoami-v2")
    assert response.status_code == 401
    assert response.headers.get("x-request-id"), (
        "X-Request-Id missing on 401 whoami-v2; middleware must apply to "
        "errors too, not just success responses"
    )


async def test_request_id_echoes_incoming_header(client):
    """When an upstream proxy (nginx / cloudflare / gateway) supplies its
    own ``X-Request-Id``, we must echo that value back rather than mint a
    new one. Otherwise correlating logs across the stack becomes
    impossible — the proxy's log line and the KohakuHub log line would
    refer to the same request by different ids."""
    incoming = "upstream-trace-deadbeef-1234"
    response = await client.get(
        "/api/whoami-v2",
        headers={"X-Request-Id": incoming},
    )
    assert response.headers.get("x-request-id") == incoming


# ---------------------------------------------------------------------------
# Named HF error codes on the resolve / revision paths
# ---------------------------------------------------------------------------


async def test_resolve_missing_file_emits_entry_not_found(owner_client):
    """HF's client converts ``X-Error-Code: EntryNotFound`` into
    ``EntryNotFoundError``. ``transformers`` reads this in
    ``utils/hub.has_file`` to distinguish "file missing" from "repo
    missing" — without the correct code, its fallback probe loop misfires."""
    response = await owner_client.head(
        "/models/owner/demo-model/resolve/main/nonexistent/path.bin"
    )
    assert response.status_code == 404
    assert response.headers.get("x-error-code") == "EntryNotFound"
    assert response.headers.get("x-error-message")
    assert response.headers.get("x-request-id")


async def test_resolve_missing_repo_emits_repo_not_found(client):
    response = await client.head(
        "/models/no-such-namespace/no-such-repo/resolve/main/README.md"
    )
    assert response.status_code == 404
    assert response.headers.get("x-error-code") == "RepoNotFound"
    assert response.headers.get("x-error-message")


async def test_not_implemented_carries_all_three_error_fields(owner_client):
    """The 501 not-implemented catch-alls must also emit the full contract
    so downstream observability treats them identically to other errors."""
    response = await owner_client.get("/api/models/owner/demo-model/discussions")
    assert response.status_code == 501
    assert response.headers.get("x-error-code") == "NotImplemented"
    assert response.headers.get("x-error-message")
    assert response.headers.get("x-request-id")


# ---------------------------------------------------------------------------
# End-to-end: huggingface_hub.HfHubHTTPError.server_message wiring
# ---------------------------------------------------------------------------


async def test_hf_client_surfaces_x_error_message_as_server_message(
    live_server_url, hf_api_token
):
    """``HfHubHTTPError.server_message`` is what propagates into user
    tracebacks. Pin that it equals the ``X-Error-Message`` value we emit,
    so when we tune messages for clarity the user sees the new text
    immediately — no second round-trip through exception formatting."""
    from huggingface_hub import HfApi
    from huggingface_hub.utils import RepositoryNotFoundError

    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    with pytest.raises(RepositoryNotFoundError) as excinfo:
        import asyncio as _a

        await _a.to_thread(api.repo_info, "ghost-org/ghost-repo")
    assert excinfo.value.server_message, (
        "RepositoryNotFoundError.server_message must be populated from "
        "X-Error-Message; got empty"
    )
    assert excinfo.value.request_id, (
        "RepositoryNotFoundError.request_id must be populated from the "
        "X-Request-Id header; got empty"
    )
