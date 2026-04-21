"""Tests for XET token refresh routes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import kohakuhub.api.xet.routers.xet as xet_router


@pytest.mark.asyncio
async def test_get_xet_read_token_returns_not_found_for_missing_repo(monkeypatch):
    monkeypatch.setattr(xet_router, "get_repository", lambda *_args: None)

    response = await xet_router.get_xet_read_token(
        "model",
        "owner",
        "repo",
        "main",
        "README.md",
        request=SimpleNamespace(),
    )

    assert response.status_code == 404
    assert response.body == b'{"error":"Repository not found"}'


@pytest.mark.asyncio
async def test_get_xet_read_token_returns_headers_and_extracts_bearer_token(monkeypatch):
    repo = SimpleNamespace(full_id="owner/repo")
    seen = {}

    monkeypatch.setattr(xet_router, "get_repository", lambda *_args: repo)
    monkeypatch.setattr(xet_router, "check_repo_read_permission", lambda repo_arg, user: seen.setdefault("permission", (repo_arg, user)))
    monkeypatch.setattr(xet_router.cfg.app, "base_url", "https://hub.example.com")

    response = await xet_router.get_xet_read_token(
        "dataset",
        "owner",
        "repo",
        "main",
        "README.md",
        request=SimpleNamespace(),
        user=SimpleNamespace(username="owner"),
        authorization="Bearer hf_token|external=1",
    )

    assert response.status_code == 200
    assert response.headers["X-Xet-Cas-Url"] == "https://hub.example.com"
    assert response.headers["X-Xet-Access-Token"] == "hf_token"
    assert int(response.headers["X-Xet-Token-Expiration"]) > 0
    assert seen["permission"] == (repo, SimpleNamespace(username="owner"))
