"""API tests for SSH key routes."""

TEST_PUBLIC_KEY = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIETd15NJPPGOG7SIPyY4AkAlUJQnjhI/8x2UMhww8PHs "
    "test@example"
)


async def test_ssh_key_crud_duplicate_and_ownership(owner_client, outsider_client):
    list_response = await owner_client.get("/api/user/keys")
    assert list_response.status_code == 200
    initial_keys = list_response.json()
    initial_count = len(initial_keys)
    # The fixture under test must not collide with anything the seed planted.
    assert all(
        not k["title"].startswith("Workstation Test") for k in initial_keys
    )

    create_response = await owner_client.post(
        "/api/user/keys",
        json={"title": "Workstation Test", "key": TEST_PUBLIC_KEY},
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    key_id = payload["id"]
    assert payload["key_type"] == "ssh-ed25519"
    assert payload["fingerprint"].startswith("SHA256:")

    get_response = await owner_client.get(f"/api/user/keys/{key_id}")
    assert get_response.status_code == 200
    assert get_response.json()["title"] == "Workstation Test"

    duplicate_response = await owner_client.post(
        "/api/user/keys",
        json={"title": "Duplicate", "key": TEST_PUBLIC_KEY},
    )
    assert duplicate_response.status_code == 409

    forbidden_response = await outsider_client.delete(f"/api/user/keys/{key_id}")
    assert forbidden_response.status_code == 403

    delete_response = await owner_client.delete(f"/api/user/keys/{key_id}")
    assert delete_response.status_code == 200

    final_response = await owner_client.get("/api/user/keys")
    assert final_response.status_code == 200
    final_keys = final_response.json()
    assert len(final_keys) == initial_count
    assert all(k["id"] != key_id for k in final_keys)
