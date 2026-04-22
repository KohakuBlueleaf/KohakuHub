"""Minimal HuggingFace-shaped server used as a fallback upstream in
end-to-end tests that exercise real `hf_hub_download` through KohakuHub.

Exposes three file shapes that mirror the redirect patterns found in the
100-repo /resolve survey (see PR #21 comments):

    pattern_a.txt  — 307 → relative /api/resolve-cache/... (non-LFS)
    pattern_b.bin  — 302 → absolute CDN + X-Linked-Size    (LFS via xet)
    pattern_c.md   — direct 200, no redirect               (edge case)

The server runs under the same uvicorn helper we use for `live_server_url`,
so tests get a real TCP port they can point KohakuHub's fallback source at.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response

# Deterministic bodies for byte-level assertions. Keep them small (< 1MB)
# so the whole integration run stays quick in CI.
PATTERN_A_BYTES = (b"tag_id,name,category,count\n" + b"100,foo,0,1\n") * 800
PATTERN_B_BYTES = b"FAKE-SAFETENSORS-PAYLOAD-" + bytes(range(256)) * 200
PATTERN_C_BYTES = b"# KohakuHub test fixture\n\nhello world.\n" * 50

PATTERN_A_ETAG = "pattern-a-sha256-deadbeef"
PATTERN_B_ETAG = "pattern-b-sha256-cafef00d"
PATTERN_C_ETAG = "pattern-c-sha256-feedface"

COMMIT = "abc123456789def"


def build_mock_hf_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # --- Pattern A: 307 → relative /api/resolve-cache ---
    @app.api_route(
        "/{owner}/{name}/resolve/{rev}/pattern_a.txt",
        methods=["HEAD", "GET"],
    )
    async def pattern_a(owner: str, name: str, rev: str, request: Request):
        # HEAD: HF-style 307 with a relative Location + misleading
        # Content-Length (redirect body, not file size).
        if request.method == "HEAD":
            return Response(
                status_code=307,
                headers={
                    "location": f"/api/resolve-cache/models/{owner}/{name}/{COMMIT}/pattern_a.txt",
                    "content-length": "278",
                    "x-repo-commit": COMMIT,
                    "x-linked-etag": f'"{PATTERN_A_ETAG}"',
                },
            )
        # GET: 307 to /api/resolve-cache so the follow chain lands on the
        # body. khub's FallbackClient.get uses follow_redirects=True, so
        # httpx walks this internally.
        return RedirectResponse(
            url=f"/api/resolve-cache/models/{owner}/{name}/{COMMIT}/pattern_a.txt",
            status_code=307,
        )

    @app.api_route(
        "/api/resolve-cache/models/{owner}/{name}/{sha}/pattern_a.txt",
        methods=["HEAD", "GET"],
    )
    async def pattern_a_final(
        owner: str, name: str, sha: str, request: Request
    ):
        headers = {
            "content-length": str(len(PATTERN_A_BYTES)),
            "etag": f'"{PATTERN_A_ETAG}"',
            "x-repo-commit": COMMIT,
            "content-type": "text/plain; charset=utf-8",
        }
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)
        return Response(
            content=PATTERN_A_BYTES, status_code=200, headers=headers,
        )

    # --- Pattern B: 302 → absolute "CDN" URL with X-Linked-Size ---
    @app.api_route(
        "/{owner}/{name}/resolve/{rev}/pattern_b.bin",
        methods=["HEAD", "GET"],
    )
    async def pattern_b(owner: str, name: str, rev: str, request: Request):
        # Use the same host as an absolute URL — simulates HF's absolute
        # 302 into cas-bridge.xethub.hf.co. khub should pass this Location
        # through untouched (urljoin on an absolute URL is a no-op).
        cdn_url = str(request.base_url) + "cas/pattern_b.bin"
        if request.method == "HEAD":
            return Response(
                status_code=302,
                headers={
                    "location": cdn_url,
                    "content-length": "1369",
                    "x-linked-size": str(len(PATTERN_B_BYTES)),
                    "x-linked-etag": f'"{PATTERN_B_ETAG}"',
                    "x-repo-commit": COMMIT,
                },
            )
        return RedirectResponse(url=cdn_url, status_code=302)

    @app.api_route("/cas/pattern_b.bin", methods=["HEAD", "GET"])
    async def cas_pattern_b(request: Request):
        headers = {
            "content-length": str(len(PATTERN_B_BYTES)),
            "etag": f'"{PATTERN_B_ETAG}"',
            "content-type": "application/octet-stream",
        }
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)
        return Response(
            content=PATTERN_B_BYTES, status_code=200, headers=headers,
        )

    # --- Pattern C: direct 200 ---
    @app.api_route(
        "/{owner}/{name}/resolve/{rev}/pattern_c.md",
        methods=["HEAD", "GET"],
    )
    async def pattern_c(owner: str, name: str, rev: str, request: Request):
        headers = {
            "content-length": str(len(PATTERN_C_BYTES)),
            "etag": f'"{PATTERN_C_ETAG}"',
            "x-repo-commit": COMMIT,
            "content-type": "text/markdown; charset=utf-8",
        }
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)
        return Response(
            content=PATTERN_C_BYTES, status_code=200, headers=headers,
        )

    return app
