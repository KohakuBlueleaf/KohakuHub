"""API tests for external token management routes."""

from urllib.parse import quote


async def test_available_sources_and_external_token_crud(owner_client):
    available_response = await owner_client.get("/api/fallback-sources/available")
    assert available_response.status_code == 200
    assert isinstance(available_response.json(), list)

    add_response = await owner_client.post(
        "/api/users/owner/external-tokens",
        json={"url": "https://hf.example", "token": "hf_abcdef"},
    )
    assert add_response.status_code == 200

    list_response = await owner_client.get("/api/users/owner/external-tokens")
    assert list_response.status_code == 200
    tokens = {item["url"]: item for item in list_response.json()}
    assert tokens["https://hf.example"]["token_preview"] == "hf_a***"

    bulk_response = await owner_client.put(
        "/api/users/owner/external-tokens/bulk",
        json={
            "tokens": [
                {"url": "https://hf.example", "token": "hf_updated"},
                {"url": "https://mirror.example", "token": "mi_abcdef"},
            ]
        },
    )
    assert bulk_response.status_code == 200

    list_response = await owner_client.get("/api/users/owner/external-tokens")
    tokens = {item["url"]: item for item in list_response.json()}
    assert set(tokens) == {"https://hf.example", "https://mirror.example"}
    assert tokens["https://hf.example"]["token_preview"] == "hf_u***"

    delete_response = await owner_client.delete(
        "/api/users/owner/external-tokens/"
        + quote("https://mirror.example", safe="")
    )
    assert delete_response.status_code == 200

    final_response = await owner_client.get("/api/users/owner/external-tokens")
    assert [item["url"] for item in final_response.json()] == ["https://hf.example"]


async def test_external_tokens_reject_cross_user_access(outsider_client):
    response = await outsider_client.post(
        "/api/users/owner/external-tokens",
        json={"url": "https://hf.example", "token": "hf_denied"},
    )

    assert response.status_code == 403
