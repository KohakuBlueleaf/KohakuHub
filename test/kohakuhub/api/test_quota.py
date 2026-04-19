"""API tests for quota routes."""

import httpx


async def test_public_namespace_and_repository_quota_views(app, owner_client):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as anonymous_client:
        anonymous_response = await anonymous_client.get("/api/quota/owner/public")
        assert anonymous_response.status_code == 200
        assert anonymous_response.json()["can_see_private"] is False

    owner_public_response = await owner_client.get("/api/quota/owner/public")
    assert owner_public_response.status_code == 200
    assert owner_public_response.json()["can_see_private"] is True

    quota_response = await owner_client.get("/api/quota/owner")
    assert quota_response.status_code == 200
    assert quota_response.json()["namespace"] == "owner"

    repo_quota_response = await owner_client.get(
        "/api/quota/repo/model/owner/demo-model"
    )
    assert repo_quota_response.status_code == 200
    assert repo_quota_response.json()["repo_id"] == "owner/demo-model"

    update_repo_response = await owner_client.put(
        "/api/quota/repo/model/owner/demo-model",
        json={"quota_bytes": 8192},
    )
    assert update_repo_response.status_code == 200
    assert update_repo_response.json()["quota_bytes"] == 8192
    assert update_repo_response.json()["is_inheriting"] is False

    recalculate_repo_response = await owner_client.post(
        "/api/quota/repo/model/owner/demo-model/recalculate"
    )
    assert recalculate_repo_response.status_code == 200

    repos_response = await owner_client.get("/api/quota/owner/repos")
    assert repos_response.status_code == 200
    assert any(
        repo["repo_id"] == "owner/demo-model"
        for repo in repos_response.json()["repositories"]
    )


async def test_org_quota_updates_require_admin_membership(
    owner_client, visitor_client
):
    forbidden_response = await visitor_client.put(
        "/api/quota/acme-labs",
        json={"quota_bytes": 4096},
    )
    assert forbidden_response.status_code == 403

    update_response = await owner_client.put(
        "/api/quota/acme-labs",
        json={"quota_bytes": 4096},
    )
    assert update_response.status_code == 200
    assert update_response.json()["namespace"] == "acme-labs"

    recalculate_response = await owner_client.post("/api/quota/acme-labs/recalculate")
    assert recalculate_response.status_code == 200
