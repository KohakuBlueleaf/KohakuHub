"""API tests for settings routes."""


async def test_update_user_and_org_settings(owner_client, member_client):
    user_response = await owner_client.put(
        "/api/users/owner/settings",
        json={"full_name": "Owner Updated", "bio": "Updated bio"},
    )
    assert user_response.status_code == 200

    profile_response = await owner_client.get("/api/users/owner/profile")
    assert profile_response.status_code == 200
    assert profile_response.json()["full_name"] == "Owner Updated"

    org_response = await member_client.put(
        "/api/organizations/acme-labs/settings",
        json={"bio": "Updated organization bio"},
    )
    assert org_response.status_code == 200

    org_profile = await owner_client.get("/api/organizations/acme-labs/profile")
    assert org_profile.status_code == 200
    assert org_profile.json()["bio"] == "Updated organization bio"


async def test_update_repo_lfs_settings_and_read_effective_values(owner_client):
    update_response = await owner_client.put(
        "/api/models/owner/demo-model/settings",
        json={
            "lfs_threshold_bytes": 2000000,
            "lfs_keep_versions": 7,
            "lfs_suffix_rules": [".gguf"],
        },
    )
    assert update_response.status_code == 200

    read_response = await owner_client.get("/api/models/owner/demo-model/settings/lfs")
    assert read_response.status_code == 200
    payload = read_response.json()
    assert payload["lfs_threshold_bytes"] == 2000000
    assert payload["lfs_keep_versions"] == 7
    assert ".gguf" in payload["lfs_suffix_rules_effective"]


async def test_namespace_type_social_links_and_repo_setting_validation(owner_client):
    update_response = await owner_client.put(
        "/api/users/owner/settings",
        json={
            "website": "https://owner.example",
            "social_media": {"github": "owner-dev"},
        },
    )
    assert update_response.status_code == 200

    type_response = await owner_client.get("/api/users/owner/type")
    assert type_response.status_code == 200
    assert type_response.json()["type"] == "user"

    profile_response = await owner_client.get("/api/users/owner/profile")
    assert profile_response.status_code == 200
    assert profile_response.json()["website"] == "https://owner.example"
    assert profile_response.json()["social_media"]["github"] == "owner-dev"

    invalid_response = await owner_client.put(
        "/api/models/owner/demo-model/settings",
        json={"lfs_threshold_bytes": 999999},
    )
    assert invalid_response.status_code == 400
