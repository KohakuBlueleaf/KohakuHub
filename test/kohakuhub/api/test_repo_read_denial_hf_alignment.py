"""KohakuHub-as-fallback-source error contract for read-permission denials.

Closes the gap audited in #76. The four standard HF X-Error-Code values
already align with HuggingFace; the remaining gap is the
permission-denial path. ``check_repo_read_permission`` historically raised
``HTTPException(401)`` for anonymous-on-private and ``HTTPException(403)``
for authed-no-access — both produce FastAPI's default ``{"detail": ...}``
JSON envelope with no ``X-Error-Code``. A ``huggingface_hub`` client
hitting a private hkub repo therefore got a generic ``HfHubHTTPError``
instead of ``RepositoryNotFoundError``.

Per the issue's recommended Option A (privacy-preserving / matches HF's
authed branch), permission denials now emit ``404 + X-Error-Code:
RepoNotFound`` regardless of whether the caller is anonymous or
authenticated. This:

- Makes hkub-as-fallback-source for another hkub instance speak the same
  HF contract every other hkub upstream surface already speaks.
- Maps cleanly through ``huggingface_hub.utils._http.hf_raise_for_status``
  to ``RepositoryNotFoundError`` on the client.
- Hides the private-repo enumeration leak (anon callers can no longer
  distinguish "doesn't exist" from "private" by status code).

Two test groups in this module:

1. **Wire-shape tests** — drive the four read-side surfaces (info / tree
   / paths-info / resolve HEAD/GET) for {anonymous, outsider} callers,
   assert the response is ``404 + X-Error-Code: RepoNotFound`` with an
   empty body. Owner regression guards confirm legitimate access still
   resolves to 200.

2. **huggingface_hub interop tests** — drive a real ``HfApi`` /
   ``hf_hub_download`` client against the live test server and assert it
   raises ``RepositoryNotFoundError`` (not generic ``HfHubHTTPError``).

The empirical anchor for the wire shapes is the live HF probe captured
2026-05-05 (and previously 2026-04-30 in the PR #77 design):

| HF wire shape (probed)                              | hf_hub raises          |
|-----------------------------------------------------|------------------------|
| 401 bare + ``Invalid username or password.`` (anon) | RepositoryNotFoundError|
| 404 + ``X-Error-Code: RepoNotFound`` (authed)       | RepositoryNotFoundError|

We pick the authed shape (Option A) for hkub: same exception class on
the client, no enumeration leak.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest
from huggingface_hub import HfApi, hf_hub_download

# ``huggingface_hub.errors`` landed around v0.22; v0.20.3 (still in the CI
# matrix) keeps these exceptions under ``huggingface_hub.utils``. The utils
# path is the version-portable import that works against every client
# version we target.
from huggingface_hub.utils import RepositoryNotFoundError


# ---------------------------------------------------------------------------
# Wire-shape tests — assert the 404 + X-Error-Code: RepoNotFound contract
# across every read-side surface that ``check_repo_read_permission`` gates.
#
# All requests carry ``?fallback=false`` so we test the *local* response
# shape in isolation. Fallback chain interaction is covered separately by
# the fallback test suite — that's a different concern from "what shape
# does our local layer emit for a permission denial".
# ---------------------------------------------------------------------------


def _assert_repo_not_found(response, *, expected_message_contains: str = "private-dataset"):
    """Assert a response matches HF's RepoNotFound contract on the wire.

    Empty body, 404, ``X-Error-Code: RepoNotFound``, ``X-Error-Message``
    present and non-trivial. Mirrors the helper checks in
    ``test_error_contract.py`` for the EntryNotFound / RevisionNotFound
    paths.
    """
    assert response.status_code == 404, (
        f"expected 404 (HF authed-style RepoNotFound), got {response.status_code} "
        f"body={response.content[:200]!r} headers={dict(response.headers)}"
    )
    assert response.headers.get("x-error-code") == "RepoNotFound", (
        f"X-Error-Code missing or wrong; got {response.headers.get('x-error-code')!r}. "
        "huggingface_hub.hf_raise_for_status keys off this header to dispatch "
        "RepositoryNotFoundError; without it the client falls through to "
        "generic HfHubHTTPError."
    )
    assert response.headers.get("x-error-message"), (
        "X-Error-Message missing — becomes HfHubHTTPError.server_message; "
        "leaving it blank degrades the user's traceback message."
    )
    assert expected_message_contains in response.headers["x-error-message"]
    # HF's contract is empty body — error data lives in headers. The
    # FastAPI default ``{"detail": ...}`` envelope is what we're moving away from.
    assert response.content == b"", (
        f"expected empty body (HF contract — error data in headers), "
        f"got {response.content[:200]!r}"
    )


# ---- info surface ---------------------------------------------------------


async def test_info_anon_on_private_dataset_returns_repo_not_found(client):
    response = await client.get(
        "/api/datasets/acme-labs/private-dataset",
        params={"fallback": "false"},
    )
    _assert_repo_not_found(response)


async def test_info_anon_on_private_dataset_returns_repo_not_found_with_fallback_enabled(
    client, monkeypatch
):
    """Production-shape regression guard for the fallback-decorator
    interaction.

    Other tests in this module pass ``?fallback=false`` to isolate the
    *local* response shape from any chain-probe behavior. This test
    deliberately does not — it pins the wire shape on the actual
    production code path where ``cfg.fallback.enabled=True`` (the
    production default) and the decorator wraps the endpoint with its
    full try/except machinery.

    The decorator catches generic ``Exception`` to route LakeFS / DB /
    timeout failures to ``LOCAL_OTHER_ERROR`` + 500. A naive
    ``RepoReadDeniedError`` design would be swallowed by that branch and
    re-raised as a 500 — letting the global handler in ``main.py``
    never run, and silently regressing this PR's wire-shape promise
    for every fallback-enabled deployment. Pin the contract so a
    future refactor of the decorator can't unwire the privacy
    translation invisibly.
    """
    from kohakuhub.config import cfg

    monkeypatch.setattr(cfg.fallback, "enabled", True)

    response = await client.get(
        "/api/datasets/acme-labs/private-dataset",
        # NOTE: deliberately no ``?fallback=false`` — we want the
        # wrapper's full code path.
    )
    _assert_repo_not_found(response)


async def test_info_outsider_on_private_dataset_returns_repo_not_found(outsider_client):
    response = await outsider_client.get(
        "/api/datasets/acme-labs/private-dataset",
        params={"fallback": "false"},
    )
    _assert_repo_not_found(response)


async def test_info_member_on_private_dataset_still_succeeds(member_client):
    """Regression guard — legitimate access must still resolve to 200."""
    response = await member_client.get(
        "/api/datasets/acme-labs/private-dataset",
        params={"fallback": "false"},
    )
    assert response.status_code == 200


# ---- tree surface ---------------------------------------------------------


async def test_tree_anon_on_private_dataset_returns_repo_not_found(client):
    response = await client.get(
        "/api/datasets/acme-labs/private-dataset/tree/main",
        params={"fallback": "false"},
    )
    _assert_repo_not_found(response)


async def test_tree_outsider_on_private_dataset_returns_repo_not_found(outsider_client):
    response = await outsider_client.get(
        "/api/datasets/acme-labs/private-dataset/tree/main",
        params={"fallback": "false"},
    )
    _assert_repo_not_found(response)


async def test_tree_member_on_private_dataset_still_succeeds(member_client):
    response = await member_client.get(
        "/api/datasets/acme-labs/private-dataset/tree/main",
        params={"fallback": "false"},
    )
    assert response.status_code == 200


# ---- paths-info surface ---------------------------------------------------


async def test_paths_info_anon_on_private_dataset_returns_repo_not_found(client):
    response = await client.post(
        "/api/datasets/acme-labs/private-dataset/paths-info/main",
        params={"fallback": "false"},
        # ``paths-info`` is a Form endpoint per huggingface_hub's wire
        # protocol; HF accepts repeated ``paths`` values, not a JSON body.
        data={"paths": "train.jsonl"},
    )
    _assert_repo_not_found(response)


async def test_paths_info_outsider_on_private_dataset_returns_repo_not_found(outsider_client):
    response = await outsider_client.post(
        "/api/datasets/acme-labs/private-dataset/paths-info/main",
        params={"fallback": "false"},
        data={"paths": "train.jsonl"},
    )
    _assert_repo_not_found(response)


# ---- resolve HEAD / GET surfaces ------------------------------------------


async def test_resolve_head_anon_on_private_dataset_returns_repo_not_found(client):
    response = await client.head(
        "/datasets/acme-labs/private-dataset/resolve/main/train.jsonl",
        params={"fallback": "false"},
    )
    _assert_repo_not_found(response)


async def test_resolve_head_outsider_on_private_dataset_returns_repo_not_found(outsider_client):
    response = await outsider_client.head(
        "/datasets/acme-labs/private-dataset/resolve/main/train.jsonl",
        params={"fallback": "false"},
    )
    _assert_repo_not_found(response)


async def test_resolve_get_anon_on_private_dataset_returns_repo_not_found(client):
    response = await client.get(
        "/datasets/acme-labs/private-dataset/resolve/main/train.jsonl",
        params={"fallback": "false"},
    )
    _assert_repo_not_found(response)


async def test_resolve_get_outsider_on_private_dataset_returns_repo_not_found(outsider_client):
    response = await outsider_client.get(
        "/datasets/acme-labs/private-dataset/resolve/main/train.jsonl",
        params={"fallback": "false"},
    )
    _assert_repo_not_found(response)


# ---------------------------------------------------------------------------
# huggingface_hub interop — drive a real client and assert the dispatched
# exception class. This is the proof that our wire shape actually maps to
# the right named exception on the client side.
# ---------------------------------------------------------------------------


async def test_hf_api_dataset_info_anon_raises_repository_not_found(live_server_url):
    """Anonymous ``HfApi.dataset_info`` against a private hkub repo must raise
    ``RepositoryNotFoundError`` — not the generic ``HfHubHTTPError`` we
    emitted before #76 closed."""
    api = HfApi(endpoint=live_server_url)  # no token → anonymous
    with pytest.raises(RepositoryNotFoundError):
        await asyncio.to_thread(
            api.dataset_info,
            "acme-labs/private-dataset",
        )


async def test_hf_api_dataset_info_outsider_raises_repository_not_found(
    live_server_url, outsider_client
):
    """Authed-no-access caller is intentionally indistinguishable from
    anonymous on the wire (privacy-preserving Option A from #76).
    ``HfApi`` driven by the outsider's token must raise
    ``RepositoryNotFoundError``."""
    # Pull the outsider's token by minting one via the API token endpoint.
    # We reuse the session cookie the fixture already established to
    # request a fresh API token; pretty close to the real-world flow
    # where a user creates a token in settings.
    token_response = await outsider_client.post(
        "/api/auth/tokens/create",
        json={"name": f"hf-interop-{id(object())}"},
    )
    token_response.raise_for_status()
    token = token_response.json()["token"]

    api = HfApi(endpoint=live_server_url, token=token)
    with pytest.raises(RepositoryNotFoundError):
        await asyncio.to_thread(
            api.dataset_info,
            "acme-labs/private-dataset",
        )


async def test_hf_hub_download_anon_on_private_dataset_raises_repository_not_found(
    live_server_url, tmp_path
):
    """``hf_hub_download`` is the read path most users hit. Anonymous
    download from a private repo must raise ``RepositoryNotFoundError`` —
    the contract that lets ``transformers``, ``datasets``, etc. produce
    actionable error messages instead of "HTTP 401 / 403"."""
    with pytest.raises(RepositoryNotFoundError):
        await asyncio.to_thread(
            hf_hub_download,
            repo_id="acme-labs/private-dataset",
            filename="train.jsonl",
            repo_type="dataset",
            endpoint=live_server_url,
            cache_dir=str(tmp_path / "hf-cache"),
        )
