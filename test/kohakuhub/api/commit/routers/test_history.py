"""API tests for commit history routes."""

from test.kohakuhub.api.helpers import encode_ndjson, file_op


async def test_list_commit_history_and_diff(owner_client):
    payload = encode_ndjson(
        [
            {
                "key": "header",
                "value": {"summary": "Add changelog", "description": "history test"},
            },
            file_op("changelog.md", b"# Changelog\n\n- added from history test\n"),
        ]
    )
    commit_response = await owner_client.post(
        "/api/models/owner/demo-model/commit/main",
        content=payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    assert commit_response.status_code == 200
    commit_id = commit_response.json()["commitOid"]

    list_response = await owner_client.get("/api/models/owner/demo-model/commits/main")
    assert list_response.status_code == 200
    commits = list_response.json()["commits"]
    assert any(commit["id"] == commit_id for commit in commits)

    detail_response = await owner_client.get(
        f"/api/models/owner/demo-model/commit/{commit_id}"
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["author"] == "owner"
    assert detail["message"] == "Add changelog"

    diff_response = await owner_client.get(
        f"/api/models/owner/demo-model/commit/{commit_id}/diff"
    )
    assert diff_response.status_code == 200
    diff_payload = diff_response.json()
    files = {item["path"]: item for item in diff_payload["files"]}
    assert files["changelog.md"]["type"] == "added"
    assert "+++ b/changelog.md" in files["changelog.md"]["diff"]
