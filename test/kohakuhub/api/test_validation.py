"""API tests for validation endpoints."""


async def test_check_name_rejects_reserved_namespace_name(client):
    response = await client.post("/api/validate/check-name", json={"name": "admin"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert "reserved" in payload["message"]


async def test_check_name_rejects_normalized_repository_conflict(client):
    response = await client.post(
        "/api/validate/check-name",
        json={
            "name": "demo_model",
            "namespace": "owner",
            "type": "model",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["conflict_with"] == "owner/demo-model"


async def test_check_name_accepts_available_repository_name(client):
    response = await client.post(
        "/api/validate/check-name",
        json={
            "name": "brand-new-repo",
            "namespace": "owner",
            "type": "model",
        },
    )

    assert response.status_code == 200
    assert response.json()["available"] is True
