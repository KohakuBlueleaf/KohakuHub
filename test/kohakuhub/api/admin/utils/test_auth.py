"""Unit tests for admin auth helpers."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import kohakuhub.api.admin.utils.auth as admin_auth


@pytest.mark.asyncio
async def test_verify_admin_token_rejects_disabled_missing_and_invalid_tokens(monkeypatch):
    monkeypatch.setattr(admin_auth.cfg.admin, "enabled", False)
    with pytest.raises(HTTPException) as disabled_exc:
        await admin_auth.verify_admin_token("secret")
    assert disabled_exc.value.status_code == 503

    monkeypatch.setattr(admin_auth.cfg.admin, "enabled", True)
    monkeypatch.setattr(admin_auth.cfg.admin, "secret_token", "expected")

    with pytest.raises(HTTPException) as missing_exc:
        await admin_auth.verify_admin_token(None)
    assert missing_exc.value.status_code == 401

    with pytest.raises(HTTPException) as invalid_exc:
        await admin_auth.verify_admin_token("wrong")
    assert invalid_exc.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_admin_token_accepts_matching_secret(monkeypatch):
    monkeypatch.setattr(admin_auth.cfg.admin, "enabled", True)
    monkeypatch.setattr(admin_auth.cfg.admin, "secret_token", "expected")

    assert await admin_auth.verify_admin_token("expected") is True
