"""Integration tests: feed the response KohakuHub produces straight into
huggingface_hub's real metadata parser and assert the resulting
``HfFileMetadata`` is well-formed.

Scenarios (mirrored from the /resolve redirect survey — 426 probes, 100
repos on huggingface.co):

    A. 307-rel-resolve-cache  (72.3% of probes)
       Non-LFS text: HF returns 307 with a relative Location into
       /api/resolve-cache/... Without a backfill, Content-Length is the
       307 redirect-body length (~278B), not the file size.

    B. 302-xet-cas-bridge     (22.1% of probes)
       LFS blob: HF returns 302 with an absolute cas-bridge URL and an
       X-Linked-Size header that gives the real file size.

    C. direct-200             (3.5% of probes)
       Some README / YAML files: HF serves the resolve directly, no
       redirect at all. Content-Length is the real file size.

Each pattern is exercised through both `method="HEAD"` and `method="GET"`
on `try_fallback_resolve`, then fed back into huggingface_hub's real
`HfFileMetadata`, `_normalize_etag`, `_int_or_none`, and (when available)
`parse_xet_file_data_from_response`. Because those functions are unchanged
across 0.20.3 / 0.30.2 / 0.36.2 / 1.0.1 / 1.6.0 / latest, the tests run on
every cell in the CI matrix; xet-specific assertions are gated on the
xet module being importable (added in hf_hub 1.0).
"""
from __future__ import annotations

import inspect

import httpx
import pytest

from huggingface_hub import constants as hf_constants
from huggingface_hub.file_download import HfFileMetadata, _int_or_none, _normalize_etag

try:
    from huggingface_hub.utils._xet import parse_xet_file_data_from_response

    HAS_XET = True
except ImportError:  # pre-1.0 hf_hub matrix cells
    parse_xet_file_data_from_response = None  # type: ignore[assignment]
    HAS_XET = False

# hf_hub migrated its internal HTTP layer from `requests` to `httpx`
# in 1.0. That migration changed the type `hf_raise_for_status`
# accepts: older versions expect `requests.Response`, newer ones
# expect `httpx.Response`. The CLASSIFICATION logic (X-Error-Code →
# GatedRepoError / EntryNotFoundError / generic HfHubHTTPError) is
# identical across both branches — only the input type differs, so
# the pattern-D tests below build whichever Response type the
# installed hf_hub understands and assert the same classification on
# every matrix cell.
import huggingface_hub as _hf

_hf_version_tuple = tuple(
    int(p) for p in _hf.__version__.split(".")[:2] if p.isdigit()
)
_HF_USES_HTTPX = _hf_version_tuple >= (1, 0)


def _hf_error(name):
    """Resolve an hf_hub exception class across the matrix of installed
    versions. The public module graph reshuffled a few times:

    - 0.20.x: only ``huggingface_hub.utils.<Name>`` is importable
    - 0.30.x through latest: ``huggingface_hub.errors.<Name>`` is the
      documented location, also re-exported from ``huggingface_hub.utils``

    Try the modern path first; fall back to the utils path on old cells.
    """
    try:
        mod = __import__("huggingface_hub.errors", fromlist=[name])
        return getattr(mod, name)
    except (ImportError, AttributeError):
        pass
    mod = __import__("huggingface_hub.utils", fromlist=[name])
    return getattr(mod, name)

_HF_METADATA_FIELDS = set(inspect.signature(HfFileMetadata).parameters.keys())

import kohakuhub.api.fallback.operations as fallback_ops  # noqa: E402

from test.kohakuhub.api.fallback.test_operations import (  # noqa: E402
    AbsoluteHeadStub,
    DummyCache,
    FakeFallbackClient,
    _content_response,
)


HF_ENDPOINT = "https://hf.local"
KHUB_BASE = "http://khub.local"
REPO_PREFIX = "/models/owner/demo/resolve/main"


@pytest.fixture(autouse=True)
def _reset_fallback_env(monkeypatch):
    monkeypatch.setattr(fallback_ops.cfg.fallback, "enabled", True)
    FakeFallbackClient.reset()
    monkeypatch.setattr(fallback_ops, "FallbackClient", FakeFallbackClient)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _to_httpx(response, *, request_url: str) -> httpx.Response:
    """Rehydrate a FastAPI ``Response`` as an httpx ``Response`` so it can
    flow straight into hf_hub's parsers without any further adaptation."""
    raw_headers = response.raw_headers  # list[tuple[bytes, bytes]]
    headers = httpx.Headers(
        [(k.decode("latin-1"), v.decode("latin-1")) for k, v in raw_headers]
    )
    return httpx.Response(
        status_code=response.status_code,
        headers=headers,
        content=response.body or b"",
        request=httpx.Request("HEAD", request_url),
    )


def _to_hf_response(response, *, request_url: str):
    """Rehydrate a FastAPI ``Response`` as the Response type the
    installed hf_hub's ``hf_raise_for_status`` expects.

    - hf_hub >= 1.0 → ``httpx.Response``
    - hf_hub <  1.0 → ``requests.Response``

    Used only by pattern-D tests that drive ``hf_raise_for_status``;
    other tests in this file keep using ``_to_httpx`` because they
    read fields (status, headers) that exist on both shapes.
    """
    raw_pairs = [
        (k.decode("latin-1"), v.decode("latin-1"))
        for k, v in response.raw_headers
    ]
    body = response.body or b""
    if _HF_USES_HTTPX:
        return httpx.Response(
            status_code=response.status_code,
            headers=httpx.Headers(raw_pairs),
            content=body,
            request=httpx.Request("HEAD", request_url),
        )
    # requests branch (0.x hf_hub). Import lazily so the module import
    # does not fail on a hypothetical httpx-only install.
    import requests
    from requests.structures import CaseInsensitiveDict

    r = requests.Response()
    r.status_code = response.status_code
    r.headers = CaseInsensitiveDict(raw_pairs)
    r.url = request_url
    r._content = body
    r.encoding = "utf-8"
    return r


def _hf_metadata(hx: httpx.Response) -> HfFileMetadata:
    """Construct HfFileMetadata using hf_hub's own header conventions.

    Mirrors the real call in `get_hf_file_metadata` at
    huggingface_hub/file_download.py. Works on every matrix pin because
    pre-1.0 `HfFileMetadata` lacks the `xet_file_data` field — we only
    pass it when present in the dataclass signature.
    """
    kwargs = dict(
        commit_hash=hx.headers.get(hf_constants.HUGGINGFACE_HEADER_X_REPO_COMMIT),
        etag=_normalize_etag(
            hx.headers.get(hf_constants.HUGGINGFACE_HEADER_X_LINKED_ETAG)
            or hx.headers.get("ETag")
        ),
        location=hx.headers.get("Location") or str(hx.request.url),
        size=_int_or_none(
            hx.headers.get(hf_constants.HUGGINGFACE_HEADER_X_LINKED_SIZE)
            or hx.headers.get("Content-Length")
        ),
    )
    if HAS_XET and "xet_file_data" in _HF_METADATA_FIELDS:
        kwargs["xet_file_data"] = parse_xet_file_data_from_response(hx)
    return HfFileMetadata(**kwargs)


def _assert_client_stays_on_classic_lfs(hx: httpx.Response) -> None:
    """hf_hub switches to the Xet protocol when any of:
      * `X-Xet-Hash` present
      * Link header carries rel="xet-auth"
      * `parse_xet_file_data_from_response` returns non-None
    This helper asserts none of those would fire — the client stays on
    the classic LFS / direct-HTTP path (which KohakuHub actually speaks)."""
    lower = {k.lower() for k in hx.headers.keys()}
    assert not any(k.startswith("x-xet-") for k in lower), hx.headers
    link = hx.headers.get("link") or hx.headers.get("Link") or ""
    assert "xet-auth" not in link.lower(), link
    if HAS_XET:
        assert parse_xet_file_data_from_response(hx) is None


# ---------------------------------------------------------------------------
# Pattern A. 307 → relative /api/resolve-cache/... (non-LFS text)
# ---------------------------------------------------------------------------


def _setup_resolve_cache_source(monkeypatch):
    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": HF_ENDPOINT, "name": "HF", "source_type": "huggingface"},
        ],
    )


@pytest.mark.asyncio
async def test_pattern_A_resolve_cache_HEAD(monkeypatch):
    """307 → /api/resolve-cache: HEAD returns 307 + absolute Location, the
    extra HEAD backfills the real Content-Length so hf_hub's post-download
    consistency check passes."""
    _setup_resolve_cache_source(monkeypatch)
    path = f"{REPO_PREFIX}/selected_tags.csv"

    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", path,
        _content_response(
            307,
            headers={
                "location": "/api/resolve-cache/models/owner/demo/abc123/selected_tags.csv",
                "content-length": "278",      # redirect body — wrong for the file
                "etag": 'W/"placeholder"',
                "x-repo-commit": "abc123",
                "x-linked-etag": '"deadbeef"',
            },
            url=f"{HF_ENDPOINT}{REPO_PREFIX}/selected_tags.csv",
        ),
    )
    stub = AbsoluteHeadStub()
    stub.queue(
        _content_response(
            200,
            headers={
                "content-length": "308468",
                "etag": '"deadbeef"',
                "content-type": "text/plain; charset=utf-8",
            },
        ),
    )
    monkeypatch.setattr(httpx.AsyncClient, "head", stub.__call__)

    resp = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "selected_tags.csv", method="HEAD",
    )

    hx = _to_httpx(resp, request_url=f"{KHUB_BASE}{REPO_PREFIX}/selected_tags.csv")
    meta = _hf_metadata(hx)

    # hf_hub sees the REAL size, not the 278-byte redirect body
    assert meta.size == 308468
    assert meta.etag == "deadbeef"            # from the final 200 hop
    assert meta.commit_hash == "abc123"        # preserved from the 307
    assert meta.location.startswith("https://hf.local/api/resolve-cache/")
    _assert_client_stays_on_classic_lfs(hx)
    # Exactly one extra HEAD was fired, with Accept-Encoding: identity
    assert len(stub.calls) == 1
    assert stub.calls[0][1]["headers"]["Accept-Encoding"] == "identity"


@pytest.mark.asyncio
async def test_pattern_A_resolve_cache_GET(monkeypatch):
    """307 → /api/resolve-cache: GET streams the file through khub (httpx
    follows the 307 server-side) and hf_hub sees a 200 with the real body."""
    _setup_resolve_cache_source(monkeypatch)
    path = f"{REPO_PREFIX}/selected_tags.csv"
    # khub's HEAD probe happens first (inside try_fallback_resolve)
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", path,
        _content_response(
            307,
            headers={
                "location": "/api/resolve-cache/models/owner/demo/abc123/selected_tags.csv",
                "content-length": "278",
                "x-repo-commit": "abc123",
                "x-linked-etag": '"deadbeef"',
            },
            url=f"{HF_ENDPOINT}{REPO_PREFIX}/selected_tags.csv",
        ),
    )
    # Then the real GET — fake httpx as having followed the 307 already
    fake_body = b"tag_id,name,category,count\n" + b"a,b,0,1\n" * 100_000
    FakeFallbackClient.queue(
        HF_ENDPOINT, "GET", path,
        _content_response(
            200,
            content=fake_body,
            headers={
                "content-type": "text/plain; charset=utf-8",
                "etag": '"deadbeef"',
                "x-repo-commit": "abc123",
            },
            url=f"{HF_ENDPOINT}/api/resolve-cache/models/owner/demo/abc123/selected_tags.csv",
        ),
    )

    resp = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "selected_tags.csv", method="GET",
    )
    assert resp.status_code == 200
    assert resp.body == fake_body


# ---------------------------------------------------------------------------
# Pattern B. 302 → absolute cas-bridge.xethub.hf.co (LFS-via-xet)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pattern_B_xet_cas_bridge_HEAD(monkeypatch):
    """302 → cas-bridge with X-Linked-Size. khub must: preserve the absolute
    Location, forward X-Linked-Size as the real file size, and strip all
    X-Xet-* headers so hf_hub stays on the classic LFS code path (the
    fallback layer does not speak the Xet protocol)."""
    _setup_resolve_cache_source(monkeypatch)
    path = f"{REPO_PREFIX}/weights.safetensors"

    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", path,
        _content_response(
            302,
            headers={
                "location": "https://cas-bridge.xethub.hf.co/shard/deadbeef?sig=xyz",
                "content-length": "1369",
                "x-linked-size": "67840504",
                "x-linked-etag": '"sha256-deadbeef"',
                "x-repo-commit": "abc123",
                # Xet trap flags — must be dropped
                "x-xet-hash": "shardhash",
                "x-xet-refresh-route": (
                    "/api/models/owner/demo/xet-read-token/abc123"
                ),
                "link": '<https://cas-server/auth>; rel="xet-auth", <https://next>; rel="next"',
            },
            url=f"{HF_ENDPOINT}{REPO_PREFIX}/weights.safetensors",
        ),
    )

    resp = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "weights.safetensors", method="HEAD",
    )
    hx = _to_httpx(resp, request_url=f"{KHUB_BASE}{REPO_PREFIX}/weights.safetensors")
    meta = _hf_metadata(hx)

    assert meta.size == 67840504                              # X-Linked-Size
    assert meta.etag == "sha256-deadbeef"                      # X-Linked-Etag
    assert meta.commit_hash == "abc123"
    assert meta.location == (
        "https://cas-bridge.xethub.hf.co/shard/deadbeef?sig=xyz"
    )
    _assert_client_stays_on_classic_lfs(hx)
    assert 'rel="next"' in hx.headers.get("link", "")


@pytest.mark.asyncio
async def test_pattern_B_xet_cas_bridge_GET(monkeypatch):
    """GET: for LFS blobs we don't proxy the body through khub — the client
    takes metadata.location (cas-bridge URL) and goes direct. On the khub
    side the only thing we verify is that the HEAD probe bookkeeping above
    is consistent, and that a plain GET request still passes through the
    xet-stripping logic."""
    _setup_resolve_cache_source(monkeypatch)
    path = f"{REPO_PREFIX}/weights.safetensors"

    # HEAD (sets cache)
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", path,
        _content_response(
            200,
            headers={"x-linked-size": "67840504", "x-repo-commit": "abc123"},
        ),
    )
    FakeFallbackClient.queue(
        HF_ENDPOINT, "GET", path,
        _content_response(
            200,
            content=b"safetensor-bytes-here",
            headers={
                "content-type": "application/octet-stream",
                "x-repo-commit": "abc123",
                "x-xet-hash": "should-be-stripped",
            },
            url=f"{HF_ENDPOINT}{REPO_PREFIX}/weights.safetensors",
        ),
    )
    resp = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "weights.safetensors", method="GET",
    )
    assert resp.status_code == 200
    assert resp.body == b"safetensor-bytes-here"
    hx = _to_httpx(resp, request_url=f"{KHUB_BASE}{REPO_PREFIX}/weights.safetensors")
    _assert_client_stays_on_classic_lfs(hx)


# ---------------------------------------------------------------------------
# Pattern C. direct 200 (no redirect)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pattern_C_direct_200_HEAD(monkeypatch):
    """Some HF repos serve small text (e.g. README.md) directly with 200
    and no redirect. There is no Location to rewrite and no X-Linked-Size
    to back-fill — the first-hop Content-Length IS the real file size."""
    _setup_resolve_cache_source(monkeypatch)
    path = f"{REPO_PREFIX}/README.md"

    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", path,
        _content_response(
            200,
            headers={
                "content-length": "8421",
                "etag": '"direct-etag"',
                "x-repo-commit": "abc123",
                "content-type": "text/markdown; charset=utf-8",
            },
            url=f"{HF_ENDPOINT}{REPO_PREFIX}/README.md",
        ),
    )

    resp = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "README.md", method="HEAD",
    )
    hx = _to_httpx(resp, request_url=f"{KHUB_BASE}{REPO_PREFIX}/README.md")
    meta = _hf_metadata(hx)

    assert meta.size == 8421
    assert meta.etag == "direct-etag"
    assert meta.commit_hash == "abc123"
    # No Location means hf_hub uses request.url (khub) as metadata.location
    assert "huggingface.co" not in meta.location
    _assert_client_stays_on_classic_lfs(hx)


@pytest.mark.asyncio
async def test_pattern_C_direct_200_GET(monkeypatch):
    """Direct-200 GET: body proxied through khub verbatim."""
    _setup_resolve_cache_source(monkeypatch)
    path = f"{REPO_PREFIX}/README.md"
    body = b"# KohakuHub\n\nHello world.\n" + b"line\n" * 500

    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", path,
        _content_response(
            200,
            headers={"content-length": str(len(body)), "x-repo-commit": "abc123"},
            url=f"{HF_ENDPOINT}{REPO_PREFIX}/README.md",
        ),
    )
    FakeFallbackClient.queue(
        HF_ENDPOINT, "GET", path,
        _content_response(
            200,
            content=body,
            headers={
                "content-type": "text/markdown; charset=utf-8",
                "etag": '"direct-etag"',
                "x-repo-commit": "abc123",
            },
            url=f"{HF_ENDPOINT}{REPO_PREFIX}/README.md",
        ),
    )
    resp = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "README.md", method="GET",
    )
    assert resp.status_code == 200
    assert resp.body == body


# ---------------------------------------------------------------------------
# Extra: graceful degradation when the backfill HEAD fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pattern_A_resolve_cache_HEAD_fallback_on_error(monkeypatch):
    _setup_resolve_cache_source(monkeypatch)
    path = f"{REPO_PREFIX}/selected_tags.csv"
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", path,
        _content_response(
            307,
            headers={
                "location": "/api/resolve-cache/abc",
                "content-length": "278",
                "x-repo-commit": "abc123",
                "x-linked-etag": '"deadbeef"',
            },
            url=f"{HF_ENDPOINT}{REPO_PREFIX}/selected_tags.csv",
        ),
    )
    stub = AbsoluteHeadStub()
    stub.queue(httpx.ConnectError("upstream 502"))
    monkeypatch.setattr(httpx.AsyncClient, "head", stub.__call__)

    resp = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "selected_tags.csv", method="HEAD",
    )
    hx = _to_httpx(resp, request_url=f"{KHUB_BASE}{REPO_PREFIX}/selected_tags.csv")
    meta = _hf_metadata(hx)
    # Degrades to the 307 headers (size stale), but still parsable.
    assert meta.commit_hash == "abc123"
    assert meta.etag == "deadbeef"
    assert meta.size == 278


# ---------------------------------------------------------------------------
# Pattern D. All sources fail — aggregated error must flow through
# huggingface_hub's `hf_raise_for_status` with the *right* exception
# subclass. Without these tests a regression that drops `X-Error-Code`
# would silently downgrade `GatedRepoError` → generic `RepositoryNotFoundError`
# (the "all 401 look like misses" fallback in hf_hub/utils/_http.py).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pattern_D_all_401_raises_GatedRepoError(monkeypatch):
    """Single gated source → aggregate 401 + X-Error-Code=GatedRepo. When
    fed to `hf_raise_for_status`, huggingface_hub must raise
    `GatedRepoError` specifically, not the generic 401→RepositoryNotFound
    fallback. This is the regression-guard for the fallback bug repro
    against `animetimm/mobilenetv3_large_150d.dbv4-full`."""
    GatedRepoError = _hf_error("GatedRepoError")
    from huggingface_hub.utils import hf_raise_for_status

    _setup_resolve_cache_source(monkeypatch)
    path = f"{REPO_PREFIX}/model.safetensors"

    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", path,
        _content_response(
            401,
            content=(
                b"Access to model owner/demo is restricted. You must "
                b"have access to it and be authenticated to access it. "
                b"Please log in."
            ),
            headers={"content-type": "text/plain; charset=utf-8"},
            url=f"{HF_ENDPOINT}{REPO_PREFIX}/model.safetensors",
        ),
    )

    resp = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "model.safetensors", method="HEAD",
    )
    hx = _to_hf_response(resp, request_url=f"{KHUB_BASE}{REPO_PREFIX}/model.safetensors")
    assert hx.status_code == 401
    assert hx.headers.get("x-error-code") == "GatedRepo"

    with pytest.raises(GatedRepoError):
        hf_raise_for_status(hx)


@pytest.mark.asyncio
async def test_pattern_D_all_404_raises_EntryNotFoundError(monkeypatch):
    """All sources legitimately 404 → aggregate 404 + X-Error-Code=EntryNotFound.
    hf_hub must raise `EntryNotFoundError` (per-file miss), not
    `RepositoryNotFoundError` — the repo exists on at least one source
    per the tree endpoint, it's just this particular file that is
    missing."""
    EntryNotFoundError = _hf_error("EntryNotFoundError")
    from huggingface_hub.utils import hf_raise_for_status

    monkeypatch.setattr(fallback_ops, "get_cache", DummyCache)
    monkeypatch.setattr(
        fallback_ops,
        "get_enabled_sources",
        lambda namespace, user_tokens=None: [
            {"url": HF_ENDPOINT, "name": "HF", "source_type": "huggingface"},
            {"url": "https://mirror.local", "name": "Mirror", "source_type": "huggingface"},
        ],
    )
    path = f"{REPO_PREFIX}/nope.bin"
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", path,
        _content_response(404, url=f"{HF_ENDPOINT}{REPO_PREFIX}/nope.bin"),
    )
    FakeFallbackClient.queue(
        "https://mirror.local", "HEAD", path,
        _content_response(404, url=f"https://mirror.local{REPO_PREFIX}/nope.bin"),
    )

    resp = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "nope.bin", method="HEAD",
    )
    hx = _to_hf_response(resp, request_url=f"{KHUB_BASE}{REPO_PREFIX}/nope.bin")
    assert hx.status_code == 404
    assert hx.headers.get("x-error-code") == "EntryNotFound"

    with pytest.raises(EntryNotFoundError):
        hf_raise_for_status(hx)


@pytest.mark.asyncio
async def test_pattern_D_all_5xx_raises_generic_HfHubHTTPError(monkeypatch):
    """Aggregate of 5xx / timeout is a 502 with no X-Error-Code so hf_hub
    raises its generic `HfHubHTTPError` (caller usually treats this as
    a retryable upstream issue). The important property: it is NOT
    mis-classified as GatedRepo / EntryNotFound / RepoNotFound."""
    EntryNotFoundError = _hf_error("EntryNotFoundError")
    GatedRepoError = _hf_error("GatedRepoError")
    HfHubHTTPError = _hf_error("HfHubHTTPError")
    RepositoryNotFoundError = _hf_error("RepositoryNotFoundError")
    from huggingface_hub.utils import hf_raise_for_status

    _setup_resolve_cache_source(monkeypatch)
    path = f"{REPO_PREFIX}/transient.bin"
    FakeFallbackClient.queue(
        HF_ENDPOINT, "HEAD", path,
        _content_response(503, url=f"{HF_ENDPOINT}{REPO_PREFIX}/transient.bin"),
    )

    resp = await fallback_ops.try_fallback_resolve(
        "model", "owner", "demo", "main", "transient.bin", method="HEAD",
    )
    hx = _to_hf_response(resp, request_url=f"{KHUB_BASE}{REPO_PREFIX}/transient.bin")
    assert hx.status_code == 502
    assert hx.headers.get("x-error-code") is None

    with pytest.raises(HfHubHTTPError) as excinfo:
        hf_raise_for_status(hx)
    # Must NOT match the specific subclasses reserved for gated /
    # missing-entry / missing-repo — those would signal the wrong
    # remediation to a downstream user.
    assert not isinstance(excinfo.value, GatedRepoError)
    assert not isinstance(excinfo.value, EntryNotFoundError)
    assert not isinstance(excinfo.value, RepositoryNotFoundError)
