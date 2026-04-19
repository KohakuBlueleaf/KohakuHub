"""API tests for file endpoints."""

import asyncio


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


async def test_get_revision_and_resolve_file_routes(client):
    revision_response = await client.get("/api/models/owner/demo-model/revision/main")
    assert revision_response.status_code == 200
    assert revision_response.json()["id"] == "owner/demo-model"

    head_response = await client.head("/api/models/owner/demo-model/resolve/main/README.md")
    assert head_response.status_code == 200
    assert head_response.headers["X-Linked-Size"] == str(len(b"# Demo Model\n\nseed data\n"))

    get_response = await client.get("/api/models/owner/demo-model/resolve/main/README.md")
    assert get_response.status_code == 302
    assert get_response.headers["location"].startswith("https://fake-s3.local/")

    await asyncio.sleep(0)
