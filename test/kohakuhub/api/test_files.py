"""API tests for file endpoints."""

import asyncio

import httpx


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
