"""Unit tests for external token routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.auth.external_tokens as external_tokens_api


class _Expr:
    def __and__(self, other):
        return self


class _Field:
    def __eq__(self, other):
        return _Expr()

    def asc(self):
        return self


class _Query:
    def __init__(self, items):
        self.items = list(items)

    def where(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self.items)


class _FakeFallbackSource:
    namespace = _Field()
    enabled = _Field()
    priority = _Field()
    select_items = []
    fail = False

    @classmethod
    def select(cls):
        if cls.fail:
            raise RuntimeError("db down")
        return _Query(cls.select_items)


@pytest.fixture(autouse=True)
def _patch_fallback_source(monkeypatch):
    _FakeFallbackSource.select_items = []
    _FakeFallbackSource.fail = False
    monkeypatch.setattr(external_tokens_api, "FallbackSource", _FakeFallbackSource)


@pytest.mark.asyncio
async def test_get_available_fallback_sources_deduplicates_and_handles_database_errors(monkeypatch):
    monkeypatch.setattr(
        external_tokens_api.cfg.fallback,
        "sources",
        [
            {"url": "https://hf.local", "priority": 20},
            {
                "url": "https://mirror.local",
                "name": "Mirror",
                "source_type": "kohakuhub",
                "priority": 5,
            },
        ],
    )
    _FakeFallbackSource.select_items = [
        SimpleNamespace(
            url="https://hf.local",
            name="Duplicate HF",
            source_type="huggingface",
            priority=1,
        ),
        SimpleNamespace(
            url="https://db.local",
            name="Database",
            source_type="huggingface",
            priority=10,
        ),
    ]

    sources = await external_tokens_api.get_available_fallback_sources()

    assert sources == [
        {
            "url": "https://mirror.local",
            "name": "Mirror",
            "source_type": "kohakuhub",
            "priority": 5,
        },
        {
            "url": "https://db.local",
            "name": "Database",
            "source_type": "huggingface",
            "priority": 10,
        },
        {
            "url": "https://hf.local",
            "name": "Unknown",
            "source_type": "huggingface",
            "priority": 20,
        },
    ]

    _FakeFallbackSource.fail = True
    sources_without_db = await external_tokens_api.get_available_fallback_sources()
    assert [source["url"] for source in sources_without_db] == [
        "https://mirror.local",
        "https://hf.local",
    ]


@pytest.mark.asyncio
async def test_external_token_routes_cover_authorization_and_validation_errors(monkeypatch):
    user = SimpleNamespace(username="owner")
    other_user = SimpleNamespace(username="other")
    now = datetime(2026, 4, 20, tzinfo=timezone.utc)

    monkeypatch.setattr(
        external_tokens_api,
        "get_user_external_tokens",
        lambda user: [
            {
                "url": "https://hf.local",
                "token": "hf_secret",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    with pytest.raises(HTTPException) as list_exc:
        await external_tokens_api.list_external_tokens("owner", user=other_user)
    assert list_exc.value.status_code == 403

    tokens = await external_tokens_api.list_external_tokens("owner", user=user)
    assert tokens[0].token_preview == "hf_s***"

    with pytest.raises(HTTPException) as add_exc:
        await external_tokens_api.add_external_token(
            "owner",
            external_tokens_api.ExternalTokenRequest(url="ftp://invalid", token="secret"),
            user=user,
        )
    assert add_exc.value.status_code == 400

    with pytest.raises(HTTPException) as delete_auth_exc:
        await external_tokens_api.delete_external_token("owner", "https://hf.local", user=other_user)
    assert delete_auth_exc.value.status_code == 403

    monkeypatch.setattr(external_tokens_api, "delete_user_external_token", lambda user, url: 0)
    with pytest.raises(HTTPException) as delete_missing_exc:
        await external_tokens_api.delete_external_token("owner", "https://hf.local", user=user)
    assert delete_missing_exc.value.status_code == 404


@pytest.mark.asyncio
async def test_bulk_update_external_tokens_covers_deletions_authorization_and_invalid_urls(
    monkeypatch,
):
    user = SimpleNamespace(username="owner")
    other_user = SimpleNamespace(username="other")
    deleted_urls = []
    saved_tokens = []

    monkeypatch.setattr(
        external_tokens_api,
        "get_user_external_tokens",
        lambda user: [
            {"url": "https://old.local"},
            {"url": "https://keep.local"},
        ],
    )
    monkeypatch.setattr(
        external_tokens_api,
        "delete_user_external_token",
        lambda user, url: deleted_urls.append(url) or 1,
    )
    monkeypatch.setattr(
        external_tokens_api,
        "set_user_external_token",
        lambda user, url, token: saved_tokens.append((url, token)),
    )

    with pytest.raises(HTTPException) as auth_exc:
        await external_tokens_api.bulk_update_external_tokens(
            "owner",
            external_tokens_api.BulkExternalTokensRequest(tokens=[]),
            user=other_user,
        )
    assert auth_exc.value.status_code == 403

    with pytest.raises(HTTPException) as invalid_exc:
        await external_tokens_api.bulk_update_external_tokens(
            "owner",
            external_tokens_api.BulkExternalTokensRequest(
                tokens=[
                    external_tokens_api.ExternalTokenRequest(url="https://keep.local", token="keep"),
                    external_tokens_api.ExternalTokenRequest(url="bad-url", token="bad"),
                ]
            ),
            user=user,
        )
    assert invalid_exc.value.status_code == 400
    assert deleted_urls == ["https://old.local"]
    assert saved_tokens == [("https://keep.local", "keep")]
