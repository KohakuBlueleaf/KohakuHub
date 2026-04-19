"""API tests for organization routes."""


async def test_get_organization_and_members(owner_client):
    org_response = await owner_client.get("/org/acme-labs")
    assert org_response.status_code == 200
    assert org_response.json()["name"] == "acme-labs"

    members_response = await owner_client.get("/org/acme-labs/members")
    assert members_response.status_code == 200
    members = {member["user"]: member["role"] for member in members_response.json()["members"]}
    assert members["owner"] == "super-admin"
    assert members["member"] == "admin"


async def test_admin_can_update_and_remove_member(owner_client):
    update_response = await owner_client.put(
        "/org/acme-labs/members/visitor",
        json={"role": "member"},
    )
    assert update_response.status_code == 200

    list_response = await owner_client.get("/org/acme-labs/members")
    members = {member["user"]: member["role"] for member in list_response.json()["members"]}
    assert members["visitor"] == "member"

    remove_response = await owner_client.delete("/org/acme-labs/members/visitor")
    assert remove_response.status_code == 200

    list_response = await owner_client.get("/org/acme-labs/members")
    usernames = {member["user"] for member in list_response.json()["members"]}
    assert "visitor" not in usernames


async def test_non_admin_cannot_add_org_members(visitor_client):
    response = await visitor_client.post(
        "/org/acme-labs/members",
        json={"username": "outsider", "role": "member"},
    )

    assert response.status_code == 403


async def test_create_organization_and_list_user_memberships(owner_client):
    create_response = await owner_client.post(
        "/org/create",
        json={"name": "studio-team", "description": "A new test organization"},
    )
    assert create_response.status_code == 200
    assert create_response.json()["name"] == "studio-team"

    memberships_response = await owner_client.get("/org/users/owner/orgs")
    assert memberships_response.status_code == 200
    names = {org["name"] for org in memberships_response.json()["organizations"]}
    assert "studio-team" in names
