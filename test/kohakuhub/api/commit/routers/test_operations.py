"""API tests for commit operations."""

import hashlib

from test.kohakuhub.api.helpers import encode_ndjson, file_op


async def test_commit_regular_file_and_show_up_in_tree(owner_client):
    payload = encode_ndjson(
        [
            {
                "key": "header",
                "value": {"summary": "Add notes", "description": "regular file commit"},
            },
            file_op("notes.txt", b"hello from commit"),
        ]
    )
    commit_response = await owner_client.post(
        "/api/models/owner/demo-model/commit/main",
        content=payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    assert commit_response.status_code == 200

    tree_response = await owner_client.get("/api/models/owner/demo-model/tree/main")
    assert tree_response.status_code == 200
    assert any(item["path"] == "notes.txt" for item in tree_response.json())


async def test_commit_lfs_file_from_service_storage(owner_client, backend_test_state):
    content = b"second lfs payload"
    oid = hashlib.sha256(content).hexdigest()
    key = f"lfs/{oid[:2]}/{oid[2:4]}/{oid}"
    backend_test_state.s3_client.put_object(
        Bucket=backend_test_state.modules.config_module.cfg.s3.bucket,
        Key=key,
        Body=content,
        ContentType="application/octet-stream",
    )

    payload = encode_ndjson(
        [
            {
                "key": "header",
                "value": {"summary": "Add extra weights", "description": "lfs file commit"},
            },
            {
                "key": "lfsFile",
                "value": {
                    "path": "weights/extra-model.safetensors",
                    "oid": oid,
                    "size": len(content),
                    "algo": "sha256",
                },
            },
        ]
    )

    response = await owner_client.post(
        "/api/models/owner/demo-model/commit/main",
        content=payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    assert response.status_code == 200

    tree_response = await owner_client.get(
        "/api/models/owner/demo-model/tree/main",
        params={"recursive": "true"},
    )
    assert tree_response.status_code == 200
    assert any(
        item["path"] == "weights/extra-model.safetensors"
        for item in tree_response.json()
    )
