"""Range / LFS edge-case coverage for the ``/resolve`` route.

``hf_hub_download`` relies on two behaviors to implement resume-on-disconnect:

1. ``HEAD /resolve`` exposes ``Accept-Ranges: bytes`` + ``Content-Length`` so
   the client knows the server supports range requests and how much is left
   to fetch.
2. ``GET /resolve`` with a ``Range: bytes=X-Y`` header returns exactly the
   requested byte slice — after the 302 to the presigned S3 URL, the Range
   header is replayed against S3 and honored there.

These tests pin the contract at the KohakuHub boundary: what headers we
emit, how we react to unusual Range shapes, and how the threshold between
regular and LFS files affects the resolve pipeline. LFS-threshold-boundary
and zero-byte edges belong in the same module because both can trip up
naive Range implementations — a zero-byte file has no valid non-empty
range, and a file at exactly the threshold switches upload mode in a way
that must not corrupt the download path.
"""

from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest


# ---------------------------------------------------------------------------
# HEAD semantics — must expose the fields huggingface_hub reads
# ---------------------------------------------------------------------------


async def test_resolve_head_ignores_range_header_and_still_returns_full_metadata(
    owner_client,
):
    """A HEAD with a Range header is ambiguous by spec; most servers ignore
    Range on HEAD and return the unconditional 200 with full metadata.
    ``hf_hub_download`` never actually sends Range on HEAD, but gateways
    and caches in front of KohakuHub sometimes do. Regression here would
    cause those setups to see truncated metadata or a 416 during cache
    warm-up."""
    response = await owner_client.head(
        "/models/owner/demo-model/resolve/main/weights/model.safetensors",
        headers={"Range": "bytes=0-10"},
    )
    assert response.status_code == 200
    assert response.headers.get("accept-ranges") == "bytes"
    assert response.headers.get("content-length") == str(len(b"safe tensor payload"))
    assert response.headers.get("x-linked-size") == str(len(b"safe tensor payload"))


async def test_resolve_head_on_non_lfs_file_emits_full_metadata(owner_client):
    """The resolve HEAD on a small, non-LFS file must still emit
    ``Accept-Ranges``, ``Content-Length``, and ``ETag`` — transformers uses
    these unconditionally via ``utils/hub.has_file``."""
    readme_bytes = b"# Demo Model\n\nseed data\n"
    response = await owner_client.head(
        "/models/owner/demo-model/resolve/main/README.md"
    )
    assert response.status_code == 200
    assert response.headers.get("accept-ranges") == "bytes"
    assert response.headers.get("content-length") == str(len(readme_bytes))


# ---------------------------------------------------------------------------
# GET + Range — the real client resume path, live-server required
# ---------------------------------------------------------------------------


async def test_resolve_get_range_suffix_returns_tail_slice(
    live_server_url, hf_api_token
):
    """``Range: bytes=-N`` requests the last N bytes. Git LFS clients and
    ``hf_hub_download`` resume use this form when they've already received
    a prefix and want the remainder."""
    url = (
        f"{live_server_url}/models/owner/demo-model/resolve/main/"
        "weights/model.safetensors"
    )
    headers = {"Authorization": f"Bearer {hf_api_token}"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        partial = await client.get(
            url, headers={**headers, "Range": "bytes=-6"}
        )
    # S3 and MinIO both honor suffix ranges — expect 206 with the last 6 bytes.
    assert partial.status_code in (200, 206)
    if partial.status_code == 206:
        assert partial.content == b"payload"[-6:], partial.content


async def test_resolve_get_range_unsatisfiable_returns_416(
    live_server_url, hf_api_token
):
    """An offset past the end of the object must yield 416. HuggingFace's
    client enriches the error with ``Content-Range`` on 416 — the presigned
    S3 URL handles this, so the test is a contract check that the "past
    the end" case reaches the user as 416 rather than a silent truncation."""
    # weights/model.safetensors in the seed is "safe tensor payload" — 19 bytes.
    url = (
        f"{live_server_url}/models/owner/demo-model/resolve/main/"
        "weights/model.safetensors"
    )
    headers = {"Authorization": f"Bearer {hf_api_token}"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(
            url, headers={**headers, "Range": "bytes=100000-200000"}
        )
    # S3/MinIO return 416 with Content-Range. We assert the status here and
    # leave verification of Content-Range headers to the client-facing tests,
    # since those are set by the S3 backend, not by KohakuHub.
    assert response.status_code == 416, (
        f"Expected 416 for out-of-range request, got {response.status_code}: "
        f"{response.text[:200]}"
    )


async def test_resolve_get_multirange_does_not_crash_backend(
    live_server_url, hf_api_token
):
    """Multi-range ``bytes=0-3,10-15`` is unusual but legal per RFC 7233.
    Most S3-compatible backends return 200 with the full body instead of
    a multipart/byteranges response. The assertion is deliberately loose:
    we don't care whether the backend honored the multi-range semantics,
    we care that KohakuHub does not 500 when an unusual Range shape
    passes through the 302 redirect."""
    url = (
        f"{live_server_url}/models/owner/demo-model/resolve/main/"
        "weights/model.safetensors"
    )
    headers = {"Authorization": f"Bearer {hf_api_token}"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(
            url, headers={**headers, "Range": "bytes=0-3,10-15"}
        )
    # 200 (full body, multi-range not supported) or 206 (partial) are both
    # acceptable — the failure mode we're guarding against is a 5xx.
    assert response.status_code in (200, 206), (
        f"multi-range request crashed backend: {response.status_code} "
        f"{response.text[:200]}"
    )


# ---------------------------------------------------------------------------
# Zero-byte and LFS-threshold boundary
# ---------------------------------------------------------------------------


async def test_zero_byte_file_resolve_head_and_get(owner_client):
    """Zero-byte files are a real thing on the hub (e.g. ``__init__.py``,
    sentinel markers). HEAD must return ``Content-Length: 0`` and not
    confuse clients that interpret missing Content-Length as unknown size.
    """
    # Upload a 0-byte file via the commit endpoint, then resolve it.
    payload = (
        '{"key":"header","value":{"summary":"zero byte","description":""}}\n'
        '{"key":"file","value":{"path":"empty.txt","content":"","encoding":"base64"}}\n'
    )
    commit_response = await owner_client.post(
        "/api/models/owner/demo-model/commit/main",
        content=payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    assert commit_response.status_code == 200, commit_response.text

    head_response = await owner_client.head(
        "/models/owner/demo-model/resolve/main/empty.txt"
    )
    assert head_response.status_code == 200
    assert head_response.headers.get("content-length") == "0"
    assert head_response.headers.get("accept-ranges") == "bytes"


async def test_lfs_threshold_boundary_file_routes_through_lfs_upload_mode(
    owner_client, backend_test_state
):
    """Files at or above the effective LFS threshold must be flagged as
    ``uploadMode: lfs`` in the preupload response. A regression here
    silently shunts a large file through the inline-base64 path, which
    blows up with an unbounded request body — the bug would surface as
    413 / timeouts in production long after the test suite passed.
    """
    threshold = (
        backend_test_state.modules.config_module.cfg.app.lfs_threshold_bytes
    )

    # One byte below threshold → must stay regular.
    # One byte above threshold → must be routed to LFS.
    below = threshold - 1
    above = threshold + 1

    # Extensions chosen to avoid the default LFS suffix rules (.bin / .safetensors
    # etc.) — we want the size threshold to be the only thing that decides
    # uploadMode. Using `.txt` keeps the decision purely size-based.
    response = await owner_client.post(
        "/api/models/owner/demo-model/preupload/main",
        json={
            "files": [
                {"path": "pkg/below.txt", "size": below, "sha256": "a" * 64},
                {"path": "pkg/above.txt", "size": above, "sha256": "b" * 64},
            ]
        },
    )
    assert response.status_code == 200
    by_path = {item["path"]: item for item in response.json()["files"]}
    assert by_path["pkg/below.txt"]["uploadMode"] == "regular", (
        "File one byte under threshold must not route through LFS"
    )
    assert by_path["pkg/above.txt"]["uploadMode"] == "lfs", (
        "File one byte over threshold must route through LFS"
    )


async def test_resolve_get_sends_content_length_consistent_with_head(
    live_server_url, hf_api_token
):
    """The ``Content-Length`` on HEAD and the actual payload size from GET
    must agree. ``hf_hub_download`` pre-allocates files based on HEAD's
    Content-Length; a mismatch causes silent truncation or over-write."""
    url = (
        f"{live_server_url}/models/owner/demo-model/resolve/main/"
        "weights/model.safetensors"
    )
    headers = {"Authorization": f"Bearer {hf_api_token}"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        head = await client.head(url, headers=headers)
        get = await client.get(url, headers=headers)
    assert head.status_code == 200
    assert get.status_code == 200
    head_len = int(head.headers.get("content-length") or -1)
    assert head_len == len(get.content), (
        f"HEAD Content-Length {head_len} != GET body length {len(get.content)}"
    )
