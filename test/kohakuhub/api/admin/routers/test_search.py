"""API tests for admin search routes."""

from test.kohakuhub.api.helpers import encode_ndjson, file_op


async def test_admin_search_finds_users_repositories_and_commits(
    admin_client, owner_client
):
    commit_payload = encode_ndjson(
        [
            {"key": "header", "value": {"summary": "searchable admin commit"}},
            file_op("searchable.txt", b"search data"),
        ]
    )
    commit_response = await owner_client.post(
        "/api/models/owner/demo-model/commit/main",
        content=commit_payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    assert commit_response.status_code == 200

    search_response = await admin_client.get(
        "/admin/api/search",
        params=[
            ("q", "owner"),
            ("types", "users"),
            ("types", "repositories"),
        ],
    )
    assert search_response.status_code == 200
    payload = search_response.json()["results"]
    assert any(user["username"] == "owner" for user in payload["users"])
    assert any(repo["full_id"] == "owner/demo-model" for repo in payload["repositories"])

    commit_search_response = await admin_client.get(
        "/admin/api/search",
        params=[("q", "searchable admin commit"), ("types", "commits")],
    )
    assert commit_search_response.status_code == 200
    assert any(
        commit["message"] == "searchable admin commit"
        for commit in commit_search_response.json()["results"]["commits"]
    )
