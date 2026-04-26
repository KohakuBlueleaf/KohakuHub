"""Unit tests for admin fallback routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.admin.routers.fallback as admin_fallback


class _Expr:
    def __and__(self, other):
        return self


class _Field:
    def __eq__(self, other):
        return _Expr()


class _FakeFallbackSource:
    DoesNotExist = type("DoesNotExist", (Exception,), {})
    priority = _Field()
    namespace = _Field()
    enabled = _Field()
    create_impl = None
    select_impl = None
    get_by_id_impl = None

    @classmethod
    def create(cls, **kwargs):
        return cls.create_impl(**kwargs)

    @classmethod
    def select(cls):
        return cls.select_impl()

    @classmethod
    def get_by_id(cls, source_id):
        return cls.get_by_id_impl(source_id)


class _FakeQuery:
    def order_by(self, *args, **kwargs):
        return self

    def where(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter([])


@pytest.fixture(autouse=True)
def _patch_source_model(monkeypatch):
    _FakeFallbackSource.create_impl = None
    _FakeFallbackSource.select_impl = None
    _FakeFallbackSource.get_by_id_impl = None
    monkeypatch.setattr(admin_fallback, "FallbackSource", _FakeFallbackSource)


@pytest.mark.asyncio
async def test_create_list_and_get_fallback_routes_cover_generic_failures():
    _FakeFallbackSource.create_impl = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("create failed"))

    with pytest.raises(HTTPException) as create_exc:
        await admin_fallback.create_fallback_source(
            admin_fallback.FallbackSourceCreate(
                namespace="owner",
                url="https://mirror.local",
                name="Mirror",
                source_type="huggingface",
            )
        )
    assert create_exc.value.status_code == 500

    _FakeFallbackSource.select_impl = lambda: (_ for _ in ()).throw(RuntimeError("list failed"))
    with pytest.raises(HTTPException) as list_exc:
        await admin_fallback.list_fallback_sources()
    assert list_exc.value.status_code == 500

    _FakeFallbackSource.get_by_id_impl = lambda source_id: (_ for _ in ()).throw(RuntimeError("get failed"))
    with pytest.raises(HTTPException) as get_exc:
        await admin_fallback.get_fallback_source(1)
    assert get_exc.value.status_code == 500


@pytest.mark.asyncio
async def test_update_delete_and_cache_routes_cover_error_paths(monkeypatch):
    source = SimpleNamespace(
        id=1,
        namespace="owner",
        url="https://mirror.local",
        token=None,
        priority=10,
        name="Mirror",
        source_type="huggingface",
        enabled=True,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
        save=lambda: None,
        delete_instance=lambda: None,
    )
    _FakeFallbackSource.get_by_id_impl = lambda source_id: source

    with pytest.raises(HTTPException) as invalid_update_exc:
        await admin_fallback.update_fallback_source(
            1,
            admin_fallback.FallbackSourceUpdate(token="new-token", source_type="invalid"),
        )
    assert invalid_update_exc.value.status_code == 400
    assert source.token == "new-token"

    broken_source = SimpleNamespace(**source.__dict__)
    broken_source.save = lambda: (_ for _ in ()).throw(RuntimeError("save failed"))
    _FakeFallbackSource.get_by_id_impl = lambda source_id: broken_source
    monkeypatch.setattr(
        admin_fallback,
        "get_cache",
        lambda: SimpleNamespace(clear=lambda: None),
    )
    with pytest.raises(HTTPException) as update_exc:
        await admin_fallback.update_fallback_source(
            1,
            admin_fallback.FallbackSourceUpdate(name="Broken"),
        )
    assert update_exc.value.status_code == 500

    delete_broken_source = SimpleNamespace(**source.__dict__)
    delete_broken_source.delete_instance = lambda: (_ for _ in ()).throw(RuntimeError("delete failed"))
    _FakeFallbackSource.get_by_id_impl = lambda source_id: delete_broken_source
    with pytest.raises(HTTPException) as delete_exc:
        await admin_fallback.delete_fallback_source(1)
    assert delete_exc.value.status_code == 500

    monkeypatch.setattr(
        admin_fallback,
        "get_cache",
        lambda: (_ for _ in ()).throw(RuntimeError("cache unavailable")),
    )
    with pytest.raises(HTTPException) as stats_exc:
        await admin_fallback.get_cache_stats()
    assert stats_exc.value.status_code == 500

    with pytest.raises(HTTPException) as clear_exc:
        await admin_fallback.clear_cache()
    assert clear_exc.value.status_code == 500
