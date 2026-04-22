"""API tests for commit operations."""

import base64
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


async def test_frontend_upload_files_ndjson_pipeline(owner_client):
    """Replay the exact NDJSON-commit pipeline ``repoAPI.uploadFiles``
    (``src/kohaku-hub-ui/src/utils/api.js``) builds by hand:
    (1) compute SHA256 client-side, (2) POST ``/preupload`` with
    ``{files:[{path,size,sha256}]}``, (3) classify files per
    ``uploadMode`` / ``shouldIgnore`` in the response, (4) POST
    ``/commit/{branch}`` with ``Content-Type: application/x-ndjson``
    containing inline base64 for regular files.

    This pins the entire UI upload contract end-to-end — any server-side
    change that drops ``uploadMode`` / ``shouldIgnore`` from the preupload
    response or rejects the NDJSON commit shape will fail here."""
    create_response = await owner_client.post(
        "/api/repos/create",
        json={"type": "model", "name": "ui-upload-pipeline", "private": False},
    )
    create_response.raise_for_status()

    small_body = b"tiny file\n"
    files_meta = [
        {
            "path": "small.txt",
            "size": len(small_body),
            "sha256": hashlib.sha256(small_body).hexdigest(),
        }
    ]

    preupload_response = await owner_client.post(
        "/api/models/owner/ui-upload-pipeline/preupload/main",
        json={"files": files_meta},
    )
    preupload_response.raise_for_status()
    preupload = preupload_response.json()
    assert "files" in preupload
    entries = preupload["files"]
    assert len(entries) == 1
    entry = entries[0]
    assert "uploadMode" in entry
    assert entry["uploadMode"] in {"regular", "lfs"}
    assert "shouldIgnore" in entry

    commit_payload = encode_ndjson(
        [
            {"key": "header", "value": {"summary": "ui pipeline seed"}},
            {
                "key": "file",
                "value": {
                    "path": "small.txt",
                    "content": base64.b64encode(small_body).decode("ascii"),
                    "encoding": "base64",
                },
            },
        ]
    )
    commit_response = await owner_client.post(
        "/api/models/owner/ui-upload-pipeline/commit/main",
        content=commit_payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    commit_response.raise_for_status()
    assert commit_response.json().get("commitOid")

    # The second preupload must still return a valid classification so the
    # UI can render progress for every file. Strict SHA256-based dedup is
    # only possible for LFS files (regular files store a git-blob-SHA1
    # server-side), so we assert the response shape rather than a
    # particular ``shouldIgnore`` value.
    second_preupload = await owner_client.post(
        "/api/models/owner/ui-upload-pipeline/preupload/main",
        json={"files": files_meta},
    )
    second_preupload.raise_for_status()
    second_entry = second_preupload.json()["files"][0]
    assert second_entry["path"] == "small.txt"
    assert second_entry["uploadMode"] in {"regular", "lfs"}
    assert isinstance(second_entry["shouldIgnore"], bool)
