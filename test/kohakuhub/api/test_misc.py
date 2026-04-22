"""API tests for utility routes."""

import httpx


async def test_version_site_config_and_yaml_validation(client):
    version_response = await client.get("/api/version")
    assert version_response.status_code == 200
    assert version_response.json()["api"] == "kohakuhub"

    site_config_response = await client.get("/api/site-config")
    assert site_config_response.status_code == 200
    assert "site_name" in site_config_response.json()

    valid_yaml_response = await client.post(
        "/api/validate-yaml",
        json={"content": "model:\\n  name: demo\\n"},
    )
    assert valid_yaml_response.status_code == 200
    assert valid_yaml_response.json()["valid"] is True

    invalid_yaml_response = await client.post(
        "/api/validate-yaml",
        json={"content": "model: [broken"},
    )
    assert invalid_yaml_response.status_code == 200
    assert invalid_yaml_response.json()["valid"] is False


async def test_whoami_v2_requires_auth_and_returns_orgs(app, owner_client):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as anonymous_client:
        anonymous_response = await anonymous_client.get("/api/whoami-v2")
        assert anonymous_response.status_code == 401

    authenticated_response = await owner_client.get("/api/whoami-v2")
    assert authenticated_response.status_code == 200
    payload = authenticated_response.json()
    assert payload["name"] == "owner"
    assert any(org["name"] == "acme-labs" for org in payload["orgs"])


async def test_whoami_v2_bearer_and_cookie_agree(app, owner_client, hf_api_token):
    """A bearer-token caller and a cookie-session caller for the same user
    must receive identical ``/api/whoami-v2`` payloads (at minimum:
    ``name`` and the set of ``orgs``). Gradio deploys rely on the bearer
    path; the web UI relies on the cookie path — both must stay in sync."""
    cookie_response = await owner_client.get("/api/whoami-v2")
    cookie_response.raise_for_status()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as bearer_client:
        bearer_response = await bearer_client.get(
            "/api/whoami-v2",
            headers={"Authorization": f"Bearer {hf_api_token}"},
        )
    bearer_response.raise_for_status()

    cookie_payload = cookie_response.json()
    bearer_payload = bearer_response.json()
    assert cookie_payload["name"] == bearer_payload["name"] == "owner"
    assert (
        {org["name"] for org in cookie_payload["orgs"]}
        == {org["name"] for org in bearer_payload["orgs"]}
    )
