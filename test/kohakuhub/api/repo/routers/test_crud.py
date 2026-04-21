"""API tests for repository CRUD routes."""


async def test_create_repository_and_reject_normalized_duplicate(owner_client):
    create_response = await owner_client.post(
        "/api/repos/create",
        json={"type": "model", "name": "sandbox-repo", "private": False},
    )
    assert create_response.status_code == 200
    assert create_response.json()["repo_id"] == "owner/sandbox-repo"

    duplicate_response = await owner_client.post(
        "/api/repos/create",
        json={"type": "model", "name": "sandbox_repo", "private": False},
    )
    assert duplicate_response.status_code == 400
    assert duplicate_response.headers["x-error-code"] == "RepoExists"
    assert "conflicts" in duplicate_response.headers["x-error-message"]


async def test_admin_can_delete_empty_org_repository(admin_client, owner_client):
    create_response = await owner_client.post(
        "/api/repos/create",
        json={
            "type": "dataset",
            "name": "temp-delete",
            "private": False,
            "organization": "acme-labs",
        },
    )
    assert create_response.status_code == 200

    delete_response = await admin_client.request(
        "DELETE",
        "/api/repos/delete",
        json={"type": "dataset", "name": "temp-delete", "organization": "acme-labs"},
    )
    assert delete_response.status_code == 200
    assert "deleted" in delete_response.json()["message"].lower()
