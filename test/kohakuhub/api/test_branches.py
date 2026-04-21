"""API tests for branch and tag routes."""

from test.kohakuhub.api.helpers import encode_ndjson, file_op


async def test_create_branch_commit_and_delete(owner_client):
    create_response = await owner_client.post(
        "/api/models/owner/demo-model/branch",
        json={"branch": "feature"},
    )
    assert create_response.status_code == 200

    commit_payload = encode_ndjson(
        [
            {"key": "header", "value": {"summary": "Branch change"}},
            file_op("branch-only.txt", b"branch specific content"),
        ]
    )
    commit_response = await owner_client.post(
        "/api/models/owner/demo-model/commit/feature",
        content=commit_payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    assert commit_response.status_code == 200

    feature_tree = await owner_client.get(
        "/api/models/owner/demo-model/tree/feature",
        params={"recursive": "true"},
    )
    assert feature_tree.status_code == 200
    assert any(item["path"] == "branch-only.txt" for item in feature_tree.json())

    main_tree = await owner_client.get(
        "/api/models/owner/demo-model/tree/main",
        params={"recursive": "true"},
    )
    assert main_tree.status_code == 200
    assert all(item["path"] != "branch-only.txt" for item in main_tree.json())

    delete_response = await owner_client.delete(
        "/api/models/owner/demo-model/branch/feature"
    )
    assert delete_response.status_code == 200


async def test_tag_lifecycle_and_main_branch_protection(owner_client):
    create_response = await owner_client.post(
        "/api/models/owner/demo-model/tag",
        json={"tag": "release-1"},
    )
    assert create_response.status_code == 200

    tree_response = await owner_client.get("/api/models/owner/demo-model/tree/release-1")
    assert tree_response.status_code == 200
    assert any(item["path"] == "README.md" for item in tree_response.json())

    main_delete_response = await owner_client.delete(
        "/api/models/owner/demo-model/branch/main"
    )
    assert main_delete_response.status_code == 400
    assert "Cannot delete main branch" in main_delete_response.headers["x-error-message"]

    delete_response = await owner_client.delete(
        "/api/models/owner/demo-model/tag/release-1"
    )
    assert delete_response.status_code == 200


async def test_non_writer_cannot_create_branch(visitor_client):
    response = await visitor_client.post(
        "/api/models/owner/demo-model/branch",
        json={"branch": "forbidden"},
    )

    assert response.status_code == 403
