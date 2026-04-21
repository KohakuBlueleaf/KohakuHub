"""Tests for fallback source configuration loading."""

from __future__ import annotations

from dataclasses import dataclass

import kohakuhub.api.fallback.config as fallback_config_module


class FakeField:
    """Field stub supporting Peewee-like comparisons."""

    def __eq__(self, other):  # noqa: D105
        return ("eq", other)


@dataclass
class FakeSource:
    """Fallback source row used by query stubs."""

    namespace: str
    enabled: bool
    priority: int
    url: str
    token: str | None
    name: str
    source_type: str


class FakeQuery:
    """Very small subset of the Peewee query API."""

    def __init__(self, rows: list[FakeSource]):
        self.rows = rows
        self.filters: list[tuple[str, object]] = []

    def where(self, *conditions):
        for condition in conditions:
            if isinstance(condition, tuple) and condition[0] == "eq":
                self.filters.append(condition)
        return self

    def order_by(self, *args):
        filtered_rows = self.rows
        for _operator, expected in self.filters:
            if isinstance(expected, str):
                filtered_rows = [row for row in filtered_rows if row.namespace == expected]
            elif isinstance(expected, bool):
                filtered_rows = [row for row in filtered_rows if row.enabled is expected]
        return iter(sorted(filtered_rows, key=lambda item: item.priority))


def _install_fake_model(monkeypatch, rows: list[FakeSource]) -> None:
    class FakeFallbackSource:
        namespace = FakeField()
        enabled = FakeField()
        priority = FakeField()

        @staticmethod
        def select():
            return FakeQuery(rows)

    monkeypatch.setattr(fallback_config_module, "FallbackSource", FakeFallbackSource)


def test_get_enabled_sources_returns_empty_when_fallback_disabled(monkeypatch):
    monkeypatch.setattr(fallback_config_module.cfg.fallback, "enabled", False)

    assert fallback_config_module.get_enabled_sources("owner") == []


def test_get_enabled_sources_merges_config_db_namespace_and_user_tokens(monkeypatch):
    monkeypatch.setattr(fallback_config_module.cfg.fallback, "enabled", True)
    monkeypatch.setattr(
        fallback_config_module.cfg.fallback,
        "sources",
        [
            {
                "url": "https://config-primary.local",
                "token": "admin-config",
                "priority": 40,
                "name": "Config Primary",
                "source_type": "huggingface",
            },
            {
                "url": "https://duplicate.local",
                "token": "config-duplicate",
                "priority": 90,
                "name": "Duplicate Config",
                "source_type": "kohakuhub",
            },
        ],
    )
    _install_fake_model(
        monkeypatch,
        [
            FakeSource("", True, 20, "https://global-db.local", "db-global", "DB Global", "huggingface"),
            FakeSource("", True, 80, "https://duplicate.local", "db-duplicate", "DB Duplicate", "huggingface"),
            FakeSource("owner", True, 10, "https://owner-db.local", "db-owner", "Owner DB", "kohakuhub"),
        ],
    )

    sources = fallback_config_module.get_enabled_sources(
        "owner",
        user_tokens={
            "https://config-primary.local": "user-token",
            "https://owner-db.local": "namespace-user-token",
        },
    )

    assert [source["url"] for source in sources] == [
        "https://owner-db.local",
        "https://global-db.local",
        "https://config-primary.local",
        "https://duplicate.local",
    ]
    assert sources[0]["token"] == "namespace-user-token"
    assert sources[0]["token_source"] == "user"
    assert sources[1]["token"] == "db-global"
    assert sources[2]["token"] == "user-token"
    assert sources[2]["token_source"] == "user"
    assert sources[3]["token"] == "config-duplicate"


def test_get_enabled_sources_tolerates_database_errors(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(fallback_config_module.cfg.fallback, "enabled", True)
    monkeypatch.setattr(fallback_config_module.cfg.fallback, "sources", [])
    monkeypatch.setattr(fallback_config_module.logger, "warning", warnings.append)

    class BrokenFallbackSource:
        namespace = FakeField()
        enabled = FakeField()
        priority = FakeField()

        @staticmethod
        def select():
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(fallback_config_module, "FallbackSource", BrokenFallbackSource)

    assert fallback_config_module.get_enabled_sources("owner") == []
    assert warnings == [
        "Failed to load global sources from database: db unavailable",
        "Failed to load namespace sources from database for owner: db unavailable",
    ]


def test_get_source_by_url_returns_matching_enabled_source(monkeypatch):
    monkeypatch.setattr(
        fallback_config_module,
        "get_enabled_sources",
        lambda namespace: [
            {"url": "https://one.local", "priority": 20},
            {"url": "https://two.local", "priority": 10},
        ],
    )

    assert fallback_config_module.get_source_by_url("https://two.local", "owner") == {
        "url": "https://two.local",
        "priority": 10,
    }
    assert fallback_config_module.get_source_by_url("https://missing.local", "owner") is None
