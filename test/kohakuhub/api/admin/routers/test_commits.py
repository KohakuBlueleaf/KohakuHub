"""API tests for admin commit routes."""

from test.kohakuhub.api.helpers import encode_ndjson, file_op


async def test_admin_can_filter_commit_history(admin_client, owner_client):
    commit_payload = encode_ndjson(
        [
            {"key": "header", "value": {"summary": "admin commit listing"}},
            file_op("admin-commit.txt", b"admin commit body"),
        ]
    )
    commit_response = await owner_client.post(
        "/api/models/owner/demo-model/commit/main",
        content=commit_payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    assert commit_response.status_code == 200

    repo_response = await admin_client.get(
        "/admin/api/commits",
        params={"repo_full_id": "owner/demo-model"},
    )
    assert repo_response.status_code == 200
    assert any(
        commit["message"] == "admin commit listing"
        for commit in repo_response.json()["commits"]
    )

    user_response = await admin_client.get(
        "/admin/api/commits",
        params={"username": "owner"},
    )
    assert user_response.status_code == 200
    assert all(commit["username"] == "owner" for commit in user_response.json()["commits"])
