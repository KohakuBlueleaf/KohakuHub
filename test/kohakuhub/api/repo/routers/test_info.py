"""API tests for repository info routes."""

import httpx


async def test_get_repo_info_returns_siblings_and_lfs_metadata(client):
    response = await client.get("/api/models/owner/demo-model")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "owner/demo-model"
    sibling_names = {item["rfilename"] for item in payload["siblings"]}
    assert {"README.md", "config.json", "weights/model.safetensors"} <= sibling_names

    lfs_sibling = next(
        sibling for sibling in payload["siblings"] if sibling["rfilename"] == "weights/model.safetensors"
    )
    assert lfs_sibling["lfs"]["size"] > 0


async def test_list_repositories_and_user_repo_views_respect_visibility(
    app, client, owner_client
):
    model_list_response = await client.get("/api/models", params={"author": "owner"})
    assert model_list_response.status_code == 200
    assert any(repo["id"] == "owner/demo-model" for repo in model_list_response.json())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as anonymous_client:
        anonymous_user_repos = await anonymous_client.get("/api/users/acme-labs/repos")
        assert anonymous_user_repos.status_code == 200
        assert anonymous_user_repos.json()["datasets"] == []

    owner_user_repos = await owner_client.get("/api/users/acme-labs/repos")
    assert owner_user_repos.status_code == 200
    assert any(
        repo["id"] == "acme-labs/private-dataset"
        for repo in owner_user_repos.json()["datasets"]
    )
