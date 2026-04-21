"""API tests for admin quota routes."""


async def test_admin_quota_overview_and_namespace_management(admin_client):
    overview_response = await admin_client.get("/admin/api/quota/overview")
    assert overview_response.status_code == 200
    overview = overview_response.json()
    assert "top_consumers" in overview
    assert "system_storage" in overview

    get_response = await admin_client.get("/admin/api/quota/owner")
    assert get_response.status_code == 200
    assert get_response.json()["namespace"] == "owner"

    update_response = await admin_client.put(
        "/admin/api/quota/owner",
        json={"private_quota_bytes": 4096, "public_quota_bytes": 8192},
    )
    assert update_response.status_code == 200
    assert update_response.json()["private_quota_bytes"] == 4096
    assert update_response.json()["public_quota_bytes"] == 8192

    recalculate_response = await admin_client.post("/admin/api/quota/owner/recalculate")
    assert recalculate_response.status_code == 200
