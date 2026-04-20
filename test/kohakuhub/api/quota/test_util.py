"""Unit tests for quota utilities."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import kohakuhub.api.quota.util as quota_util


class _Expr:
    def __and__(self, other):
        return self


class _Field:
    def __eq__(self, other):
        return _Expr()


class _HistoryQuery:
    def __init__(self, items):
        self.items = list(items)

    def where(self, *args, **kwargs):
        return self

    def count(self):
        return len(self.items)

    def distinct(self):
        unique = {}
        for item in self.items:
            unique.setdefault(item.sha256, item)
        return _HistoryQuery(unique.values())

    def __iter__(self):
        return iter(self.items)


class _FakeFileModel:
    repository = _Field()
    path_in_repo = _Field()
    is_deleted = _Field()
    results = []

    @classmethod
    def get_or_none(cls, expr):
        if cls.results:
            return cls.results.pop(0)
        return None


class _FakeLFSObjectHistoryModel:
    repository = _Field()
    sha256 = _Field()
    size = _Field()
    items = []

    @classmethod
    def select(cls, *args):
        return _HistoryQuery(cls.items)


class _FakeUserModel:
    username = _Field()
    get_result = None
    get_or_none_result = None

    @classmethod
    def get(cls, expr):
        return cls.get_result

    @classmethod
    def get_or_none(cls, expr):
        return cls.get_or_none_result


class _FakeLakeFSClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def list_objects(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _MutableEntity(SimpleNamespace):
    def save(self):
        self.saved = True


class _MutableRepo(SimpleNamespace):
    def save(self):
        self.saved = True


@pytest.fixture(autouse=True)
def _patch_models(monkeypatch):
    _FakeFileModel.results = []
    _FakeLFSObjectHistoryModel.items = []
    _FakeUserModel.get_result = None
    _FakeUserModel.get_or_none_result = None
    monkeypatch.setattr(quota_util, "File", _FakeFileModel)
    monkeypatch.setattr(quota_util, "LFSObjectHistory", _FakeLFSObjectHistoryModel)
    monkeypatch.setattr(quota_util, "User", _FakeUserModel)
    monkeypatch.setattr(quota_util, "lakefs_repo_name", lambda repo_type, full_id: f"{repo_type}-{full_id}")


@pytest.mark.asyncio
async def test_calculate_repository_storage_covers_pagination_and_failures(monkeypatch):
    repo = _MutableRepo(repo_type="model", full_id="owner/demo")
    client = _FakeLakeFSClient(
        [
            {
                "results": [
                    {"path_type": "object", "path": "weights.bin", "size_bytes": 10},
                ],
                "pagination": {"has_more": True, "next_offset": "page-2"},
            },
            {
                "results": [
                    {"path_type": "object", "path": "notes.txt", "size_bytes": 5},
                ],
                "pagination": {"has_more": False},
            },
        ]
    )
    _FakeFileModel.results = [SimpleNamespace(lfs=True), None]
    _FakeLFSObjectHistoryModel.items = [
        SimpleNamespace(sha256="sha-a", size=10),
        SimpleNamespace(sha256="sha-b", size=12),
    ]
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: client)

    storage = await quota_util.calculate_repository_storage(repo)

    assert storage == {
        "total_bytes": 27,
        "current_branch_bytes": 15,
        "current_branch_non_lfs_bytes": 5,
        "lfs_total_bytes": 22,
        "lfs_unique_bytes": 22,
    }
    assert client.calls[1]["after"] == "page-2"

    failing_client = _FakeLakeFSClient([RuntimeError("list failed")])
    _FakeLFSObjectHistoryModel.items = []
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: failing_client)

    failed_storage = await quota_util.calculate_repository_storage(repo)
    assert failed_storage["current_branch_bytes"] == 0
    assert failed_storage["total_bytes"] == 0


def test_quota_helpers_cover_org_overages_missing_entities_and_ownerless_repositories(monkeypatch):
    monkeypatch.setattr(quota_util, "get_organization", lambda namespace: None)
    assert quota_util.check_quota("acme", 1, is_private=True, is_org=True) == (
        False,
        "Organization not found: acme",
    )

    org = _MutableEntity(
        private_quota_bytes=100,
        public_quota_bytes=200,
        private_used_bytes=95,
        public_used_bytes=20,
    )
    monkeypatch.setattr(quota_util, "get_organization", lambda namespace: org)
    allowed, message = quota_util.check_quota("acme", 10, is_private=True, is_org=True)
    assert allowed is False
    assert "Private storage quota exceeded" in message

    private_used, public_used = quota_util.increment_storage(
        "acme",
        15,
        is_private=False,
        is_org=True,
    )
    assert (private_used, public_used) == (95, 35)

    _FakeUserModel.get_or_none_result = None
    storage_info = quota_util.get_storage_info("ghost")
    assert storage_info["private_quota_bytes"] is None
    assert storage_info["total_used_bytes"] == 0

    orphan_repo = _MutableRepo(
        owner=None,
        private=False,
        quota_bytes=None,
        used_bytes=3,
        full_id="owner/orphan",
    )
    repo_info = quota_util.get_repo_storage_info(orphan_repo)
    assert repo_info["namespace_quota_bytes"] is None
    assert repo_info["namespace_used_bytes"] == 0

    updated_info = quota_util.set_repo_quota(orphan_repo, 10)
    assert orphan_repo.quota_bytes == 10
    assert updated_info["effective_quota_bytes"] == 10
