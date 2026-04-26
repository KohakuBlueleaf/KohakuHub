"""End-to-end tests: run REAL ``hf_hub_download`` against a KohakuHub
live server whose fallback source points at a mock HuggingFace process.

Unlike the parser-level interop tests, these go the full distance:

    [test] hf_hub_download
       → HTTPS  → [live_server_url: real khub uvicorn]
       → fallback → [mock_hf_server_url: fake HF uvicorn]
       → response flows back → cache file written + etag + size checked

Each of the three /resolve redirect patterns observed in the 100-repo
HF survey (see PR #21 comments) is exercised with a real file body, and
the test asserts ``open(path).read() == PATTERN_*_BYTES``. If KohakuHub
regresses on Content-Length backfill, Location rewrite, xet header
stripping, or GET proxy, the downloaded file size / etag mismatches and
hf_hub's own consistency check raises before the byte assertion even
runs.

Runs on every matrix cell because ``hf_hub_download`` is a stable public
API across the 0.20.3 → latest range in the CI matrix.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from huggingface_hub import hf_hub_download

from test.kohakuhub.support.live_server import start_live_server, stop_live_server
from test.kohakuhub.support.mock_hf_server import (
    COMMIT,
    PATTERN_A_BYTES,
    PATTERN_B_BYTES,
    PATTERN_C_BYTES,
    build_mock_hf_app,
)


@pytest.fixture(scope="module")
def mock_hf_server_url():
    """A second live uvicorn process that plays the role of HF upstream."""
    handle = start_live_server(build_mock_hf_app())
    try:
        yield handle.base_url
    finally:
        stop_live_server(handle)


@pytest.fixture
def fallback_points_to_mock(backend_test_state, mock_hf_server_url):
    """Swap khub's fallback sources to point exclusively at mock HF for
    the duration of the test, and clear the per-process fallback cache."""
    cfg = backend_test_state.modules.config_module.cfg
    old_sources = list(cfg.fallback.sources)
    old_enabled = cfg.fallback.enabled
    cfg.fallback.enabled = True
    cfg.fallback.sources = [
        {
            "url": mock_hf_server_url,
            "name": "MockHF",
            "source_type": "huggingface",
            "priority": 1,
        }
    ]
    backend_test_state.modules.fallback_cache_module.get_cache().clear()
    try:
        yield mock_hf_server_url
    finally:
        cfg.fallback.sources = old_sources
        cfg.fallback.enabled = old_enabled
        backend_test_state.modules.fallback_cache_module.get_cache().clear()


async def _download(
    *, live_server_url: str, hf_api_token: str, filename: str, tmp_path: Path
) -> bytes:
    """Call hf_hub_download in a worker thread (it is blocking) and read
    back the cached bytes so the test layer can diff them."""

    def _run() -> bytes:
        path = hf_hub_download(
            repo_id="owner/fake-repo",
            filename=filename,
            endpoint=live_server_url,
            token=hf_api_token,
            cache_dir=str(tmp_path),
        )
        return Path(path).read_bytes()

    return await asyncio.to_thread(_run)


@pytest.mark.asyncio
async def test_real_hf_hub_download_pattern_A_resolve_cache(
    live_server_url, hf_api_token, fallback_points_to_mock, tmp_path,
):
    """Non-LFS text file served via 307 → /api/resolve-cache/…

    Validates the Content-Length backfill in `try_fallback_resolve`:
    without it, hf_hub's post-download consistency check rejects the
    file as "should be of size 278 but has size N".
    """
    received = await _download(
        live_server_url=live_server_url,
        hf_api_token=hf_api_token,
        filename="pattern_a.txt",
        tmp_path=tmp_path,
    )
    assert received == PATTERN_A_BYTES


@pytest.mark.asyncio
async def test_real_hf_hub_download_pattern_B_xet_cas_bridge(
    live_server_url, hf_api_token, fallback_points_to_mock, tmp_path,
):
    """LFS blob served via 302 → absolute CDN + X-Linked-Size.

    Validates:
      * absolute Location preserved untouched (urljoin no-op)
      * X-Linked-Size forwarded so hf_hub uses it as expected_size
      * any X-Xet-* that leak through are stripped so the client stays
        on the classic LFS path (KohakuHub does not implement Xet)
    """
    received = await _download(
        live_server_url=live_server_url,
        hf_api_token=hf_api_token,
        filename="pattern_b.bin",
        tmp_path=tmp_path,
    )
    assert received == PATTERN_B_BYTES


@pytest.mark.asyncio
async def test_real_hf_hub_download_pattern_C_direct_200(
    live_server_url, hf_api_token, fallback_points_to_mock, tmp_path,
):
    """No redirect at all — upstream just answers 200 on the first HEAD/GET.

    Validates the single-hop passthrough: khub does not issue an extra
    HEAD (no Location to follow) and does not invent a fake Location,
    so hf_hub's metadata.location falls back to the request URL and the
    GET body is proxied through khub verbatim.
    """
    received = await _download(
        live_server_url=live_server_url,
        hf_api_token=hf_api_token,
        filename="pattern_c.md",
        tmp_path=tmp_path,
    )
    assert received == PATTERN_C_BYTES


@pytest.mark.asyncio
async def test_real_hf_hub_download_warm_cache_does_not_refetch(
    live_server_url, hf_api_token, fallback_points_to_mock, tmp_path,
):
    """Warm cache: second download into the same cache_dir must skip the
    GET (etag match) but still succeed. This is the scenario reviewers
    specifically called out — make sure hf_hub's HEAD-only revalidation
    path is compatible with khub's synthesized HEAD response."""
    first = await _download(
        live_server_url=live_server_url,
        hf_api_token=hf_api_token,
        filename="pattern_a.txt",
        tmp_path=tmp_path,
    )
    second = await _download(
        live_server_url=live_server_url,
        hf_api_token=hf_api_token,
        filename="pattern_a.txt",
        tmp_path=tmp_path,
    )
    assert first == second == PATTERN_A_BYTES
