"""API and helper tests for avatar routes."""

from __future__ import annotations

import io

from PIL import Image
import pytest

from kohakuhub.api.avatar import process_avatar_image

pytestmark = pytest.mark.backend_per_test


def _image_bytes(mode: str, size: tuple[int, int], fmt: str = "PNG") -> bytes:
    color = (255, 0, 0, 128) if "A" in mode else (255, 0, 0)
    image = Image.new(mode, size, color)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def test_process_avatar_image_resizes_crops_and_converts_to_jpeg():
    processed = process_avatar_image(_image_bytes("RGBA", (3000, 2000)))
    image = Image.open(io.BytesIO(processed))

    assert image.format == "JPEG"
    assert image.size == (1024, 1024)


def test_process_avatar_image_rejects_invalid_images():
    with pytest.raises(Exception):
        process_avatar_image(b"not-an-image")


@pytest.mark.asyncio
async def test_user_avatar_upload_get_and_delete_flow(owner_client):
    avatar_bytes = _image_bytes("RGB", (1200, 800))

    upload_response = await owner_client.post(
        "/api/users/owner/avatar",
        files={"file": ("avatar.png", avatar_bytes, "image/png")},
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["success"] is True

    get_response = await owner_client.get("/api/users/owner/avatar")
    assert get_response.status_code == 200
    assert get_response.headers["content-type"] == "image/jpeg"
    assert get_response.headers["Cache-Control"] == "public, max-age=86400"

    delete_response = await owner_client.delete("/api/users/owner/avatar")
    assert delete_response.status_code == 200
    assert delete_response.json()["success"] is True

    missing_response = await owner_client.get("/api/users/owner/avatar")
    assert missing_response.status_code == 404


@pytest.mark.asyncio
async def test_user_avatar_rejects_invalid_type_and_wrong_user(member_client, owner_client):
    invalid_type_response = await owner_client.post(
        "/api/users/owner/avatar",
        files={"file": ("avatar.txt", b"hello", "text/plain")},
    )
    assert invalid_type_response.status_code == 400
    assert "Invalid image type" in invalid_type_response.json()["detail"]

    unauthorized_response = await member_client.post(
        "/api/users/owner/avatar",
        files={"file": ("avatar.png", _image_bytes("RGB", (300, 300)), "image/png")},
    )
    assert unauthorized_response.status_code == 403


@pytest.mark.asyncio
async def test_org_avatar_upload_get_and_delete_flow(owner_client):
    avatar_bytes = _image_bytes("RGB", (1600, 900))

    upload_response = await owner_client.post(
        "/api/organizations/acme-labs/avatar",
        files={"file": ("org-avatar.png", avatar_bytes, "image/png")},
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["success"] is True

    get_response = await owner_client.get("/api/organizations/acme-labs/avatar")
    assert get_response.status_code == 200
    assert get_response.headers["content-type"] == "image/jpeg"
    assert get_response.headers["Cache-Control"] == "public, max-age=86400"

    delete_response = await owner_client.delete("/api/organizations/acme-labs/avatar")
    assert delete_response.status_code == 200
    assert delete_response.json()["success"] is True


@pytest.mark.asyncio
async def test_org_avatar_requires_admin_role(visitor_client):
    response = await visitor_client.post(
        "/api/organizations/acme-labs/avatar",
        files={"file": ("org-avatar.png", _image_bytes("RGB", (300, 300)), "image/png")},
    )
    assert response.status_code == 403
