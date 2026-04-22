"""Integration tests: feed the HEAD response KohakuHub produces straight
into huggingface_hub's own metadata parser and assert the resulting
``HfFileMetadata`` is well-formed.

These tests fail if a regression breaks any of the client-side invariants
that real downloads rely on — Content-Length → expected_size, ETag
normalization, xet suppression, commit hash presence, etc. Unlike the
pure-unit tests that check our response shape directly, these wire the
response into huggingface_hub 1.x's real functions (the same ones
``hf_hub_download`` uses) so any future hf_hub change that narrows the
contract surfaces here immediately.
"""
from __future__ import annotations

import httpx
import pytest

from huggingface_hub import constants as hf_constants
from huggingface_hub.file_download import HfFileMetadata, _int_or_none, _normalize_etag
from huggingface_hub.utils._xet import parse_xet_file_data_from_response

import kohakuhub.api.fallback.operations as fallback_ops

from test.kohakuhub.api.fallback.test_operations import (  # noqa: E402
    AbsoluteHeadStub,
    DummyCache,
    FakeFallbackClient,
    _content_response,
)


HF_ENDPOINT = "https://hf.local"
REPO = "/models/owner/demo/resolve/main"


@pytest.fixture(autouse=True)
def _reset_fallback_env(monkeypatch):
    monkeypatch.setattr(fallback_ops.cfg.fallback, "enabled", True)
    FakeFallbackClient.reset()
    monkeypatch.setattr(fallback_ops, "FallbackClient", FakeFallbackClient)


def _to_httpx_response(
    fastapi_response,
    *,
    request_url: str,
) -> httpx.Response:
    """Re-wrap a FastAPI ``Response`` as an httpx ``Response`` so we can
    hand it off to hf_hub's parsing routines unmodified."""
    raw_headers = fastapi_response.raw_headers  # list[tuple[bytes, bytes]]
    headers = httpx.Headers(
        [(k.decode("latin-1"), v.decode("latin-1")) for k, v in raw_headers]
    )
    return httpx.Response(
        status_code=fastapi_response.status_code,
        headers=headers,
        content=fastapi_response.body or b"",
        request=httpx.Request("HEAD", request_url),
    )


def _hf_metadata(httpx_response: httpx.Response, endpoint: str) -> HfFileMetadata:
    """Call the same metadata-extraction logic hf_hub uses internally in
    ``get_hf_file_metadata`` (see ``huggingface_hub/file_download.py:1597``)."""
    return HfFileMetadata(
        commit_hash=httpx_response.headers.get(
            hf_constants.HUGGINGFACE_HEADER_X_REPO_COMMIT
        ),
        etag=_normalize_etag(
            httpx_response.headers.get(hf_constants.HUGGINGFACE_HEADER_X_LINKED_ETAG)
            or httpx_response.headers.get("ETag")
        ),
        location=httpx_response.headers.get("Location")
        or str(httpx_response.request.url),
        size=_int_or_none(
            httpx_response.headers.get(hf_constants.HUGGINGFACE_HEADER_X_LINKED_SIZE)
            or httpx_response.headers.get("Content-Length")
        ),
        xet_file_data=parse_xet_file_data_from_response(
            httpx_response, endpoint=endpoint
        ),
    )


def _configure(monkeypatch):
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": HF_ENDPOINT, "name": "HF", "source_type": "huggingface"},
        ],
    )


@pytest.mark.asyncio
async def test_hf_hub_metadata_non_lfs_307_has_real_content_length(monkeypatch):
    """hf_hub must see the **real** 308468-byte size after khub's extra
    HEAD — not the 278-byte redirect body length. This is the consistency
    check bug that breaks get_wd14_tags."""
    _configure(monkeypatch)
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", f"{REPO}/selected_tags.csv",
        _content_response(
            307,
            headers={
                "location": "/api/resolve-cache/models/owner/demo/sha/selected_tags.csv",
                "content-length": "278",
                "etag": 'W/"placeholder"',
                "x-repo-commit": "abc123",
                "x-linked-etag": '"deadbeef"',
            },
            url=f"{HF_ENDPOINT}{REPO}/selected_tags.csv",
        ),
    )
    stub = AbsoluteHeadStub()
    stub.queue(
        _content_response(
            200,
            headers={
                "content-length": "308468",
                "etag": '"deadbeef"',
            },
        ),
    )
    monkeypatch.setattr(httpx.AsyncClient, "head", stub.__call__)

    khub_response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "selected_tags.csv", method="HEAD",
    )

    hx = _to_httpx_response(
        khub_response, request_url=f"http://khub.local{REPO}/selected_tags.csv"
    )
    meta = _hf_metadata(hx, endpoint="http://khub.local")

    assert meta.size == 308468, (
        "hf_hub would otherwise use 278 (redirect-body length) as expected size "
        "and fail the post-download consistency check"
    )
    assert meta.etag == "deadbeef"
    assert meta.commit_hash == "abc123"
    assert meta.xet_file_data is None
    # Accept-Encoding: identity was passed to the upstream HEAD
    assert stub.calls[0][1]["headers"]["Accept-Encoding"] == "identity"


@pytest.mark.asyncio
async def test_hf_hub_metadata_lfs_307_uses_x_linked_size_directly(monkeypatch):
    """LFS 3xx: hf_hub prefers X-Linked-Size, so no extra HEAD is needed
    and meta.size is the real file size even though Content-Length is the
    redirect-body length."""
    _configure(monkeypatch)
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", f"{REPO}/weights.safetensors",
        _content_response(
            307,
            headers={
                "location": "https://cas-bridge.xethub.hf.co/shard/deadbeef",
                "content-length": "1369",            # 307 body length
                "x-linked-size": "67840504",         # real file size
                "x-linked-etag": '"sha256-deadbeef"',
                "x-repo-commit": "abc123",
            },
            url=f"{HF_ENDPOINT}{REPO}/weights.safetensors",
        ),
    )

    khub_response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "weights.safetensors", method="HEAD",
    )

    hx = _to_httpx_response(
        khub_response, request_url=f"http://khub.local{REPO}/weights.safetensors"
    )
    meta = _hf_metadata(hx, endpoint="http://khub.local")

    assert meta.size == 67840504          # from X-Linked-Size
    assert meta.etag == "sha256-deadbeef"  # from X-Linked-Etag, W/ stripped if any
    assert meta.commit_hash == "abc123"
    assert meta.xet_file_data is None       # no X-Xet-Hash
    assert meta.location == "https://cas-bridge.xethub.hf.co/shard/deadbeef"


@pytest.mark.asyncio
async def test_hf_hub_xet_suppression_keeps_client_on_classic_lfs(monkeypatch):
    """Any upstream X-Xet-* must be stripped, otherwise
    parse_xet_file_data_from_response returns a XetFileData and hf_hub
    jumps to the xet protocol (which we don't implement)."""
    _configure(monkeypatch)
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", f"{REPO}/weights.safetensors",
        _content_response(
            307,
            headers={
                "location": "https://cas-bridge.xethub.hf.co/shard/deadbeef",
                "x-linked-size": "67840504",
                "x-linked-etag": '"deadbeef"',
                "x-repo-commit": "abc123",
                "x-xet-hash": "shard-hash",
                "x-xet-refresh-route": (
                    "/api/models/owner/demo/xet-read-token/abc123"
                ),
                "link": '<https://cas-server/auth>; rel="xet-auth"',
            },
            url=f"{HF_ENDPOINT}{REPO}/weights.safetensors",
        ),
    )

    khub_response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "weights.safetensors", method="HEAD",
    )

    hx = _to_httpx_response(
        khub_response, request_url=f"http://khub.local{REPO}/weights.safetensors"
    )
    assert parse_xet_file_data_from_response(hx, endpoint="http://khub.local") is None
    meta = _hf_metadata(hx, endpoint="http://khub.local")
    assert meta.xet_file_data is None


@pytest.mark.asyncio
async def test_hf_hub_weak_etag_is_normalized(monkeypatch):
    """hf_hub strips the W/ weak-etag marker; make sure whatever we
    forward makes it through that helper intact. Covers both cases:
    the initial 307 may carry a weak etag, the extra HEAD's 200 may
    carry a strong one — whichever wins still needs to round-trip."""
    _configure(monkeypatch)
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", f"{REPO}/selected_tags.csv",
        _content_response(
            307,
            headers={
                "location": "/api/resolve-cache/models/owner/demo/sha/selected_tags.csv",
                "content-length": "278",
                "etag": 'W/"placeholder-weak"',
                "x-repo-commit": "abc123",
            },
            url=f"{HF_ENDPOINT}{REPO}/selected_tags.csv",
        ),
    )
    stub = AbsoluteHeadStub()
    stub.queue(
        _content_response(
            200,
            headers={
                "content-length": "308468",
                "etag": 'W/"real-weak-etag"',   # final hop: weak
            },
        ),
    )
    monkeypatch.setattr(httpx.AsyncClient, "head", stub.__call__)

    khub_response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "selected_tags.csv", method="HEAD",
    )

    hx = _to_httpx_response(
        khub_response, request_url=f"http://khub.local{REPO}/selected_tags.csv"
    )
    meta = _hf_metadata(hx, endpoint="http://khub.local")
    # W/ marker stripped, size + commit preserved
    assert meta.etag == "real-weak-etag"
    assert meta.size == 308468
    assert meta.commit_hash == "abc123"


@pytest.mark.asyncio
async def test_hf_hub_metadata_single_hop_200_no_redirect(monkeypatch):
    """Some HF repos answer the initial /resolve HEAD with a straight 200
    (non-LFS, no redirect). Make sure we don't break that baseline path."""
    _configure(monkeypatch)
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", f"{REPO}/config.json",
        _content_response(
            200,
            headers={
                "content-length": "512",
                "etag": '"feedface"',
                "x-repo-commit": "abc123",
            },
            url=f"{HF_ENDPOINT}{REPO}/config.json",
        ),
    )

    khub_response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "config.json", method="HEAD",
    )

    hx = _to_httpx_response(
        khub_response, request_url=f"http://khub.local{REPO}/config.json"
    )
    meta = _hf_metadata(hx, endpoint="http://khub.local")
    assert meta.size == 512
    assert meta.etag == "feedface"
    assert meta.commit_hash == "abc123"
    assert meta.xet_file_data is None


@pytest.mark.asyncio
async def test_hf_hub_metadata_when_follow_fails_degrades_but_stays_parsable(monkeypatch):
    """If the extra HEAD fails, we still produce a response hf_hub can
    parse — metadata.size ends up as the 307 body length (stale), but
    commit_hash and etag are intact so downloads can still be attempted."""
    _configure(monkeypatch)
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", f"{REPO}/selected_tags.csv",
        _content_response(
            307,
            headers={
                "location": "/api/resolve-cache/models/owner/demo/sha/selected_tags.csv",
                "content-length": "278",
                "etag": '"placeholder"',
                "x-repo-commit": "abc123",
                "x-linked-etag": '"deadbeef"',
            },
            url=f"{HF_ENDPOINT}{REPO}/selected_tags.csv",
        ),
    )
    stub = AbsoluteHeadStub()
    stub.queue(httpx.ConnectError("upstream gone"))
    monkeypatch.setattr(httpx.AsyncClient, "head", stub.__call__)

    khub_response = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "selected_tags.csv", method="HEAD",
    )

    hx = _to_httpx_response(
        khub_response, request_url=f"http://khub.local{REPO}/selected_tags.csv"
    )
    meta = _hf_metadata(hx, endpoint="http://khub.local")
    assert meta.commit_hash == "abc123"
    assert meta.etag == "deadbeef"     # X-Linked-Etag wins over "placeholder"
    assert meta.size == 278             # stale but parsable
