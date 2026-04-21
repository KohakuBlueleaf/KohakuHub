"""API tests for admin user routes."""


async def test_admin_can_list_get_and_create_users(admin_client):
    list_response = await admin_client.get("/admin/api/users")
    assert list_response.status_code == 200
    assert any(user["username"] == "owner" for user in list_response.json()["users"])

    detail_response = await admin_client.get("/admin/api/users/owner")
    assert detail_response.status_code == 200
    assert detail_response.json()["username"] == "owner"

    create_response = await admin_client.post(
        "/admin/api/users",
        json={
            "username": "managed-user",
            "email": "managed@example.com",
            "password": "KohakuTest123!",
            "email_verified": True,
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["username"] == "managed-user"
