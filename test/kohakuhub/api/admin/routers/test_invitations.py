"""API tests for admin invitation routes."""


async def test_admin_can_create_list_and_delete_registration_invitations(admin_client):
    create_response = await admin_client.post(
        "/admin/api/invitations/register",
        json={"role": "member", "max_usage": 2, "expires_days": 3},
    )
    assert create_response.status_code == 200
    token = create_response.json()["token"]
    assert create_response.json()["action"] == "register_account"

    list_response = await admin_client.get(
        "/admin/api/invitations",
        params={"action": "register_account"},
    )
    assert list_response.status_code == 200
    invitations = list_response.json()["invitations"]
    assert any(invitation["token"] == token for invitation in invitations)

    delete_response = await admin_client.delete(f"/admin/api/invitations/{token}")
    assert delete_response.status_code == 200
