"""API tests for admin fallback management routes."""


async def test_admin_can_create_list_get_update_and_delete_fallback_sources(
    admin_client, backend_test_state
):
    create_response = await admin_client.post(
        "/admin/api/fallback-sources",
        json={
            "namespace": "owner",
            "url": "https://mirror.local/",
            "token": "secret-token",
            "priority": 20,
            "name": "Mirror",
            "source_type": "huggingface",
            "enabled": True,
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["namespace"] == "owner"
    assert created["url"] == "https://mirror.local"
    assert created["source_type"] == "huggingface"

    list_response = await admin_client.get("/admin/api/fallback-sources")
    assert list_response.status_code == 200
    sources = list_response.json()
    assert any(source["id"] == created["id"] for source in sources)

    filtered_response = await admin_client.get(
        "/admin/api/fallback-sources",
        params={"namespace": "owner", "enabled": "true"},
    )
    assert filtered_response.status_code == 200
    assert [source["id"] for source in filtered_response.json()] == [created["id"]]

    get_response = await admin_client.get(f"/admin/api/fallback-sources/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Mirror"

    cache = backend_test_state.modules.fallback_cache_module.get_cache()
    cache.set("model", "owner", "demo", "https://mirror.local", "Mirror", "huggingface", exists=True)

    update_response = await admin_client.put(
        f"/admin/api/fallback-sources/{created['id']}",
        json={
            "url": "https://fallback.local/",
            "name": "Fallback Mirror",
            "priority": 5,
            "source_type": "kohakuhub",
            "enabled": False,
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["url"] == "https://fallback.local"
    assert updated["name"] == "Fallback Mirror"
    assert updated["priority"] == 5
    assert updated["source_type"] == "kohakuhub"
    assert updated["enabled"] is False
    assert cache.stats()["size"] == 0

    delete_response = await admin_client.delete(
        f"/admin/api/fallback-sources/{created['id']}"
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["success"] is True


async def test_admin_fallback_routes_validate_errors_and_expose_cache_controls(
    admin_client, backend_test_state
):
    invalid_create = await admin_client.post(
        "/admin/api/fallback-sources",
        json={
            "namespace": "",
            "url": "https://invalid.local",
            "name": "Invalid",
            "source_type": "unknown",
            "enabled": True,
        },
    )
    assert invalid_create.status_code == 400
    assert "Invalid source_type" in invalid_create.json()["detail"]["error"]

    missing_get = await admin_client.get("/admin/api/fallback-sources/999999")
    assert missing_get.status_code == 404

    missing_update = await admin_client.put(
        "/admin/api/fallback-sources/999999",
        json={"name": "still-missing"},
    )
    assert missing_update.status_code == 404

    missing_delete = await admin_client.delete("/admin/api/fallback-sources/999999")
    assert missing_delete.status_code == 404

    cache = backend_test_state.modules.fallback_cache_module.get_cache()
    cache.set("model", "owner", "demo", "https://cache.local", "Cache", "huggingface", exists=True)

    stats_response = await admin_client.get("/admin/api/fallback-sources/cache/stats")
    assert stats_response.status_code == 200
    stats = stats_response.json()
    assert stats["size"] >= 1
    assert "usage_percent" in stats

    clear_response = await admin_client.delete("/admin/api/fallback-sources/cache/clear")
    assert clear_response.status_code == 200
    assert clear_response.json()["success"] is True
    assert clear_response.json()["old_size"] >= 1
    assert cache.stats()["size"] == 0
