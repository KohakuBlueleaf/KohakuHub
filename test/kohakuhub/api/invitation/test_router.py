"""API tests for invitation routes."""


async def test_org_invitation_flow(owner_client, outsider_client):
    create_response = await owner_client.post(
        "/api/invitations/org/acme-labs/create",
        json={"role": "member"},
    )
    assert create_response.status_code == 200
    token = create_response.json()["token"]

    detail_response = await owner_client.get(f"/api/invitations/{token}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["org_name"] == "acme-labs"
    assert detail["role"] == "member"
    assert detail["is_available"] is True

    list_response = await owner_client.get("/api/invitations/org/acme-labs/list")
    assert list_response.status_code == 200
    assert any(item["token"] == token for item in list_response.json()["invitations"])

    accept_response = await outsider_client.post(f"/api/invitations/{token}/accept")
    assert accept_response.status_code == 200
    assert accept_response.json()["org_name"] == "acme-labs"

    members_response = await owner_client.get("/org/acme-labs/members")
    assert members_response.status_code == 200
    roles = {member["user"]: member["role"] for member in members_response.json()["members"]}
    assert roles["outsider"] == "member"


async def test_org_invitation_delete_and_permission_checks(
    owner_client, visitor_client
):
    forbidden_response = await visitor_client.post(
        "/api/invitations/org/acme-labs/create",
        json={"role": "member"},
    )
    assert forbidden_response.status_code == 403

    create_response = await owner_client.post(
        "/api/invitations/org/acme-labs/create",
        json={"role": "visitor"},
    )
    assert create_response.status_code == 200
    token = create_response.json()["token"]

    delete_response = await owner_client.delete(f"/api/invitations/{token}")
    assert delete_response.status_code == 200

    detail_response = await owner_client.get(f"/api/invitations/{token}")
    assert detail_response.status_code == 404
