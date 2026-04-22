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
    # `huggingface_hub.HfApi.create_repo(..., exist_ok=True)` only accepts the
    # 409 status code as "repo already exists"; see hf_api.py:4501. The body is
    # JSON so the client can still build a RepoUrl from the response.
    assert duplicate_response.status_code == 409
    assert duplicate_response.headers["x-error-code"] == "RepoExists"
    assert "conflicts" in duplicate_response.headers["x-error-message"]
    body = duplicate_response.json()
    assert body["url"].endswith("/models/owner/sandbox-repo")
    assert body["repo_id"] == "owner/sandbox-repo"


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
