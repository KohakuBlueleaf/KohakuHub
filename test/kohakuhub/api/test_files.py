"""API tests for file endpoints."""

import asyncio
import http.client
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from huggingface_hub import hf_hub_download


class _HeadToGetProxyHandler(BaseHTTPRequestHandler):
    """Reproduction of a CDN converting HEAD to GET.

    Cloudflare has a documented history of converting HEAD requests to
    GET on cold-cache paths (and of serving a cached GET 302 to a later
    HEAD). Both surface to the client as: the origin's GET response,
    body stripped, delivered as the HEAD response. This handler
    mechanically replays that behaviour in-process so the test can
    drive huggingface_hub through it without needing Cloudflare in the
    loop.
    """

    origin_url: str = ""

    def _forward(self, upstream_method: str, strip_body: bool) -> None:
        url = urllib.parse.urlparse(self.origin_url + self.path)
        connection = http.client.HTTPConnection(url.hostname, url.port)
        forward_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in ("host", "content-length")
        }
        body = None
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            body = self.rfile.read(length)
        connection.request(
            upstream_method,
            url.path + (f"?{url.query}" if url.query else ""),
            body=body,
            headers=forward_headers,
        )
        response = connection.getresponse()
        payload = response.read()
        self.send_response(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() in ("transfer-encoding", "connection"):
                continue
            if strip_body and key.lower() == "content-length":
                self.send_header(key, "0")
                continue
            self.send_header(key, value)
        self.end_headers()
        if not strip_body:
            self.wfile.write(payload)

    def do_HEAD(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        self._forward("GET", strip_body=True)

    def do_GET(self):  # noqa: N802
        self._forward("GET", strip_body=False)

    def log_message(self, *_args, **_kwargs):
        # Keep pytest output clean.
        pass


def _start_head_to_get_proxy(origin_url: str):
    handler_cls = type(
        "_BoundHeadToGetHandler",
        (_HeadToGetProxyHandler,),
        {"origin_url": origin_url.rstrip("/")},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://{host}:{port}"


async def test_preupload_respects_lfs_rules(owner_client):
    response = await owner_client.post(
        "/api/models/owner/demo-model/preupload/main",
        json={
            "files": [
                {"path": "README.md", "size": 20, "sha256": "1" * 64},
                {"path": "weights/new-model.safetensors", "size": 20, "sha256": "2" * 64},
            ]
        },
    )

    assert response.status_code == 200
    payload = {item["path"]: item for item in response.json()["files"]}
    assert payload["README.md"]["uploadMode"] == "regular"
    assert payload["weights/new-model.safetensors"]["uploadMode"] == "lfs"


async def test_get_revision_and_resolve_file_routes(client, backend_test_state):
    revision_response = await client.get("/api/models/owner/demo-model/revision/main")
    assert revision_response.status_code == 200
    assert revision_response.json()["id"] == "owner/demo-model"

    head_response = await client.head("/api/models/owner/demo-model/resolve/main/README.md")
    assert head_response.status_code == 200
    assert head_response.headers["X-Linked-Size"] == str(len(b"# Demo Model\n\nseed data\n"))

    get_response = await client.get("/api/models/owner/demo-model/resolve/main/README.md")
    assert get_response.status_code == 302
    location = get_response.headers["location"]
    bucket = backend_test_state.modules.config_module.cfg.s3.bucket
    public_endpoint = (
        backend_test_state.modules.config_module.cfg.s3.public_endpoint.rstrip("/")
    )
    assert location.startswith("https://fake-s3.local/") or location.startswith(
        f"{public_endpoint}/{bucket}/"
    )

    await asyncio.sleep(0)


async def test_resolve_head_exposes_hf_client_headers(owner_client):
    """A HEAD on ``/resolve`` must carry the headers
    ``huggingface_hub.file_download`` relies on for metadata checks:
    ``X-Repo-Commit``, ``X-Linked-Etag``, ``X-Linked-Size``, ``ETag``,
    ``Content-Length``, ``Accept-Ranges``. transformers'
    ``utils/hub.has_file`` also reads these in place of
    ``HfApi.file_exists`` — regressions here break every library download
    path simultaneously."""
    response = await owner_client.head(
        "/models/owner/demo-model/resolve/main/weights/model.safetensors"
    )
    assert response.status_code == 200
    assert response.headers.get("x-linked-size") == str(len(b"safe tensor payload"))
    assert response.headers.get("x-linked-etag"), "LFS file must carry ETag"
    assert response.headers.get("accept-ranges") == "bytes"
    assert response.headers.get("x-repo-commit"), "HEAD must return commit sha"


async def test_resolve_get_302_carries_hf_metadata_and_no_store_cache(owner_client):
    """A GET on ``/resolve`` that issues a 302 redirect must carry the
    same HF metadata headers a HEAD on the same URL does, plus
    ``Cache-Control: no-store``.

    HuggingFace's own ``/resolve`` GET returns these on its 302, and
    ``huggingface_hub.file_download.get_hf_file_metadata`` reads the
    headers off the un-followed 302 response when deciding whether a
    file is cache-consistent. Any downstream layer that observes the
    302 as a HEAD response (Cloudflare converting HEAD to GET on a cold
    cache path, or serving a previously cached GET 302 to a later HEAD)
    would otherwise hand the client a redirect with no
    ``X-Repo-Commit`` / ``X-Linked-Etag`` / ``X-Linked-Size`` —
    producing the exact ``FileMetadataError`` ->
    ``ValueError: Force download failed due to the above error.``
    we see in deepghs/KohakuHub#24.

    The ``no-store`` directive is the belt-and-suspenders half of the
    same fix: presigned URLs expire and are per-user, so the 302 must
    never be cached by an intermediate proxy.
    """
    response = await owner_client.get(
        "/models/owner/demo-model/resolve/main/weights/model.safetensors"
    )
    assert response.status_code == 302
    assert response.headers.get("location"), "302 must carry Location"
    assert response.headers.get("x-repo-commit"), (
        "GET /resolve 302 must carry X-Repo-Commit for huggingface_hub compatibility"
    )
    assert response.headers.get("x-linked-etag"), (
        "GET /resolve 302 must carry X-Linked-Etag for huggingface_hub compatibility"
    )
    assert response.headers.get("x-linked-size") == str(len(b"safe tensor payload")), (
        "GET /resolve 302 must carry X-Linked-Size for huggingface_hub compatibility"
    )
    cache_control = (response.headers.get("cache-control") or "").lower()
    assert "no-store" in cache_control, (
        "GET /resolve 302 must forbid caching so presigned URLs and "
        "per-user redirects cannot be reused"
    )


async def test_resolve_get_302_exposes_cors_headers_for_browser_preview(
    owner_client,
):
    """A cross-origin GET on ``/resolve`` must surface the HF metadata
    headers to the browser so the pure-client safetensors/parquet
    preview (deepghs/KohakuHub#27) can read them. Without an explicit
    ``Access-Control-Expose-Headers`` list the browser silently strips
    every header beyond the CORS-safelisted set, hiding ``X-Linked-*``,
    ``X-Repo-Commit``, ``Content-Range``, and ``Location`` from JS.
    This pins the CORS contract so a future ``expose_headers`` change
    cannot regress the preview path without a failing test.
    """
    response = await owner_client.get(
        "/models/owner/demo-model/resolve/main/weights/model.safetensors",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 302
    exposed = {
        token.strip().lower()
        for token in (
            response.headers.get("access-control-expose-headers") or ""
        ).split(",")
        if token.strip()
    }
    required = {
        "accept-ranges",
        "content-range",
        "content-length",
        "etag",
        "location",
        "x-repo-commit",
        "x-linked-etag",
        "x-linked-size",
    }
    missing = required - exposed
    assert not missing, (
        "Access-Control-Expose-Headers on /resolve GET must surface "
        f"{sorted(required)}; missing {sorted(missing)}"
    )


async def test_hf_hub_download_survives_cdn_head_to_get(
    live_server_url, hf_api_token, tmp_path
):
    """End-to-end regression for deepghs/KohakuHub#24.

    When a CDN (Cloudflare in the production incident) converts HEAD to
    GET on a cold-cache path, ``huggingface_hub`` sees the origin's GET
    response in place of the HEAD response. If the GET response is a
    bare 302 with no HF metadata headers, ``get_hf_file_metadata``
    returns ``commit_hash=None`` and ``hf_hub_download`` crashes with
    ``FileMetadataError`` -> ``ValueError: Force download failed due
    to the above error.`` — the exact client exception that surfaced
    against ``hub.deepghs.org``.

    The header-presence unit test above asserts the direct contract of
    ``resolve_file_get``; this test proves the fix actually survives an
    hf_hub_download round-trip through the failure-mode-reproducing
    HEAD->GET proxy, so the bug cannot silently regress on the client
    side.
    """
    server, thread, proxy_url = _start_head_to_get_proxy(live_server_url)
    try:
        downloaded_path = await asyncio.to_thread(
            hf_hub_download,
            repo_id="owner/demo-model",
            filename="weights/model.safetensors",
            repo_type="model",
            endpoint=proxy_url,
            token=hf_api_token,
            cache_dir=str(tmp_path / "cache"),
            local_dir=str(tmp_path / "out"),
            force_download=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    with open(downloaded_path, "rb") as fp:
        assert fp.read() == b"safe tensor payload"


async def test_resolve_get_full_download_and_range_request(
    live_server_url, hf_api_token
):
    """Full GET + Range GET on the resolve route. Unlike the HEAD path,
    these require a real HTTP socket because the server issues a 302 to
    the S3 presigned URL — the follow step must hit MinIO over the
    network, which the ASGI transport cannot do.

    This covers both ends of the contract the kohaku-hub-ui dataset
    viewer + the ``huggingface_hub`` chunked downloader care about:
    a no-Range GET produces the full payload, and a Range GET returns
    exactly the requested byte slice (with 206 Partial Content where
    the backend supports it)."""
    url = (
        f"{live_server_url}/models/owner/demo-model/resolve/main/"
        "weights/model.safetensors"
    )
    headers = {"Authorization": f"Bearer {hf_api_token}"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        full = await client.get(url, headers=headers)
    assert full.status_code == 200
    assert full.content == b"safe tensor payload"

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        partial = await client.get(
            url, headers={**headers, "Range": "bytes=0-3"}
        )
    assert partial.status_code in (200, 206)
    assert partial.content.startswith(b"safe"), partial.content
