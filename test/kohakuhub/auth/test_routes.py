"""API tests for auth routes."""

from kohakuhub.db import EmailVerification, Token
from kohakuhub.db_operations import get_user_by_username


async def test_login_me_and_logout_flow(client):
    login_response = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "KohakuTest123!"},
    )
    assert login_response.status_code == 200
    assert "session_id" in client.cookies

    me_response = await client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "owner"

    logout_response = await client.post("/api/auth/logout")
    assert logout_response.status_code == 200


async def test_register_rejects_duplicate_username(client):
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "owner",
            "email": "duplicate@example.com",
            "password": "KohakuTest123!",
        },
    )

    assert response.status_code == 400
    assert "Username already exists" in str(response.json())


async def test_create_list_and_revoke_token(owner_client):
    create_response = await owner_client.post(
        "/api/auth/tokens/create",
        json={"name": "automation"},
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    token_id = payload["token_id"]
    token_value = payload["token"]

    list_response = await owner_client.get("/api/auth/tokens")
    assert list_response.status_code == 200
    assert any(token["id"] == token_id for token in list_response.json()["tokens"])

    bearer_client = owner_client
    bearer_client.cookies.clear()
    bearer_client.headers["Authorization"] = f"Bearer {token_value}"
    me_response = await bearer_client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "owner"

    revoke_response = await owner_client.delete(f"/api/auth/tokens/{token_id}")
    assert revoke_response.status_code == 200
    assert Token.get_or_none(Token.id == token_id) is None


async def test_login_blocks_unverified_user_when_email_verification_required(
    client, backend_test_state, monkeypatch
):
    owner = get_user_by_username("owner")
    owner.email_verified = False
    owner.save()
    monkeypatch.setattr(
        backend_test_state.modules.config_module.cfg.auth,
        "require_email_verification",
        True,
    )

    response = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "KohakuTest123!"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Please verify your email first"


async def test_register_with_email_verification_and_verify_flow(
    client, backend_test_state, monkeypatch
):
    monkeypatch.setattr(
        backend_test_state.modules.config_module.cfg.auth,
        "require_email_verification",
        True,
    )
    monkeypatch.setattr(
        backend_test_state.modules.auth_routes_module,
        "send_verification_email",
        lambda email, username, token: True,
    )

    register_response = await client.post(
        "/api/auth/register",
        json={
            "username": "fresh-user",
            "email": "fresh@example.com",
            "password": "KohakuTest123!",
        },
    )

    assert register_response.status_code == 200
    assert register_response.json()["email_verified"] is False

    user = get_user_by_username("fresh-user")
    verification = EmailVerification.get(EmailVerification.user == user)
    verify_response = await client.get(
        "/api/auth/verify-email",
        params={"token": verification.token},
    )

    assert verify_response.status_code == 302
    assert verify_response.headers["location"] == "/fresh-user"
    assert "session_id=" in verify_response.headers["set-cookie"]

    user = get_user_by_username("fresh-user")
    assert user.email_verified is True


async def test_register_requires_invitation_when_invitation_only_enabled(
    client, backend_test_state, monkeypatch
):
    monkeypatch.setattr(
        backend_test_state.modules.config_module.cfg.auth,
        "invitation_only",
        True,
    )

    response = await client.post(
        "/api/auth/register",
        json={
            "username": "invite-needed",
            "email": "invite@example.com",
            "password": "KohakuTest123!",
        },
    )

    assert response.status_code == 403
