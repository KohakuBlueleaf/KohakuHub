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


class _FileQuery:
    """Mock peewee SelectQuery for the File table.

    Supports the chain ``File.select(...).where(...).tuples().iterator()`` used
    by ``calculate_repository_storage`` to bulk-load (path_in_repo, lfs) pairs.
    """

    def __init__(self, rows):
        self.rows = list(rows)

    def where(self, *args, **kwargs):
        return self

    def tuples(self):
        return self

    def iterator(self):
        return iter(self.rows)

    def __iter__(self):
        return iter(self.rows)


class _DistinctQuery:
    """Mock subquery used by ``lfs_unique_bytes`` aggregation.

    The production code wraps this in an outer SELECT with SUM(u.size); the
    fake implementation collapses both layers and just returns the unique-sum
    when ``.scalar()`` is called via the alias path.
    """

    def __init__(self, items):
        self.items = list(items)

    def where(self, *args, **kwargs):
        return self

    def distinct(self):
        return self

    @property
    def c(self):
        # ``sub.c.size`` is referenced when building the outer SELECT; the
        # actual identity does not matter, only that attribute access works.
        return SimpleNamespace(size=object())

    def alias(self, _name):
        return self


class _AggregateQuery:
    """Captures the aggregation form (total vs unique) and resolves to the sum.

    The production code calls either ``select(SUM(size)).where(...).scalar()``
    (total) or ``select(SUM(sub.c.size)).from_(sub).scalar()`` (unique).
    """

    def __init__(self, items, mode):
        self.items = items
        self.mode = mode  # "total" or "unique"
        self._subquery = None

    def where(self, *args, **kwargs):
        return self

    def from_(self, sub):
        self._subquery = sub
        # Switch into unique mode using the subquery's items.
        return _AggregateQuery(sub.items, "unique")

    def scalar(self):
        if self.mode == "total":
            return sum(item.size for item in self.items)
        # unique
        seen = {}
        for item in self.items:
            seen.setdefault(item.sha256, item.size)
        return sum(seen.values())


class _FakeFileModel:
    repository = _Field()
    path_in_repo = _Field()
    lfs = _Field()
    is_deleted = _Field()

    # Map of path_in_repo -> lfs flag (only active rows). Rows missing here
    # are treated as not-in-DB (matching the previous "get_or_none → None"
    # behaviour, which left current_branch_lfs_bytes unchanged).
    rows: dict[str, bool] = {}

    @classmethod
    def select(cls, *_fields):
        return _FileQuery((path, lfs) for path, lfs in cls.rows.items())


class _FakeLFSObjectHistoryModel:
    repository = _Field()
    sha256 = _Field()
    size = _Field()
    items = []

    @classmethod
    def select(cls, *args):
        # The production code uses two distinct call sites:
        #   1) select(SUM(size)).where(...).scalar()           → total
        #   2) select(sha256, size).where(...).distinct().alias(..)  → subquery
        # We disambiguate by inspecting how many positional args were passed:
        # the SUM aggregator passes exactly one (the function expression), the
        # distinct subquery passes two (sha256, size).
        if len(args) <= 1:
            return _AggregateQuery(cls.items, "total")
        return _DistinctQuery(cls.items)


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
    _FakeFileModel.rows = {}
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
    # Two paths in main: weights.bin (LFS) and notes.txt (not in DB → non-LFS).
    _FakeFileModel.rows = {"weights.bin": True}
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


def _single_page(objects):
    """Build a one-page LakeFS list_objects response."""
    return {
        "results": [
            {"path_type": "object", "path": p, "size_bytes": s}
            for p, s in objects
        ],
        "pagination": {"has_more": False},
    }


@pytest.mark.asyncio
async def test_calculate_repository_storage_empty_repo(monkeypatch):
    """Empty repo (no LakeFS objects, no File rows, no LFS history) → all zero."""
    repo = _MutableRepo(repo_type="model", full_id="owner/empty")
    client = _FakeLakeFSClient([{"results": [], "pagination": {"has_more": False}}])
    _FakeFileModel.rows = {}
    _FakeLFSObjectHistoryModel.items = []
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: client)

    storage = await quota_util.calculate_repository_storage(repo)

    assert storage == {
        "total_bytes": 0,
        "current_branch_bytes": 0,
        "current_branch_non_lfs_bytes": 0,
        "lfs_total_bytes": 0,
        "lfs_unique_bytes": 0,
    }


@pytest.mark.asyncio
async def test_calculate_repository_storage_only_non_lfs(monkeypatch):
    """Pure non-LFS repo: every LakeFS object lacks a `lfs=True` row."""
    repo = _MutableRepo(repo_type="dataset", full_id="owner/notes")
    client = _FakeLakeFSClient(
        [_single_page([("a.txt", 100), ("b.txt", 250), ("c.md", 50)])]
    )
    # All present in File table but lfs=False
    _FakeFileModel.rows = {"a.txt": False, "b.txt": False, "c.md": False}
    _FakeLFSObjectHistoryModel.items = []
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: client)

    storage = await quota_util.calculate_repository_storage(repo)

    assert storage["current_branch_bytes"] == 400
    assert storage["current_branch_non_lfs_bytes"] == 400
    assert storage["lfs_total_bytes"] == 0
    assert storage["lfs_unique_bytes"] == 0
    assert storage["total_bytes"] == 400  # non-LFS in main + lfs_unique


@pytest.mark.asyncio
async def test_calculate_repository_storage_lfs_history_dedups_same_sha(monkeypatch):
    """Multiple history rows with the same sha256 must be counted ONCE in unique."""
    repo = _MutableRepo(repo_type="model", full_id="owner/dedup")
    # No live LakeFS objects (focus on the LFS history maths).
    client = _FakeLakeFSClient([{"results": [], "pagination": {"has_more": False}}])
    _FakeFileModel.rows = {}
    # 3 commits of the same sha-a (same content), 1 of sha-b → unique = a + b
    _FakeLFSObjectHistoryModel.items = [
        SimpleNamespace(sha256="sha-a", size=1000),
        SimpleNamespace(sha256="sha-a", size=1000),
        SimpleNamespace(sha256="sha-a", size=1000),
        SimpleNamespace(sha256="sha-b", size=400),
    ]
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: client)

    storage = await quota_util.calculate_repository_storage(repo)

    # lfs_total counts every row (3*1000 + 400)
    assert storage["lfs_total_bytes"] == 3400
    # lfs_unique counts each (sha256, size) pair once → 1000 + 400
    assert storage["lfs_unique_bytes"] == 1400
    # total = non-LFS in main (0) + unique LFS
    assert storage["total_bytes"] == 1400


@pytest.mark.asyncio
async def test_calculate_repository_storage_soft_deleted_file_treated_as_non_lfs(
    monkeypatch,
):
    """Soft-deleted File row must NOT cause its size to count as LFS bytes.

    Regression guard: the bulk-fetch dict is built with `is_deleted=False`,
    so a path that is soft-deleted in DB but still listed by LakeFS (race
    window) should be classified as non-LFS, matching the old `get_or_none`
    behaviour with `is_deleted == False`.
    """
    repo = _MutableRepo(repo_type="model", full_id="owner/zombie")
    client = _FakeLakeFSClient([_single_page([("ghost.bin", 1234)])])
    # File table has NO row for "ghost.bin" because soft-delete excludes it
    # from `(is_deleted=False)` filter; the dict mirrors that.
    _FakeFileModel.rows = {}
    _FakeLFSObjectHistoryModel.items = []
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: client)

    storage = await quota_util.calculate_repository_storage(repo)

    # Counted under main but not under LFS
    assert storage["current_branch_bytes"] == 1234
    assert storage["current_branch_non_lfs_bytes"] == 1234
    assert storage["lfs_total_bytes"] == 0
    assert storage["lfs_unique_bytes"] == 0
    assert storage["total_bytes"] == 1234


@pytest.mark.asyncio
async def test_calculate_repository_storage_lakefs_path_without_file_row(monkeypatch):
    """LakeFS object with no matching File row → counted as non-LFS.

    This is the canonical "old `get_or_none` returns None" case that drove
    the original semantics; the new bulk-dict path must reproduce it.
    """
    repo = _MutableRepo(repo_type="model", full_id="owner/orphan-file")
    client = _FakeLakeFSClient([_single_page([("README.md", 80), ("logo.png", 5000)])])
    # README.md has a row, logo.png does not
    _FakeFileModel.rows = {"README.md": False}
    _FakeLFSObjectHistoryModel.items = []
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: client)

    storage = await quota_util.calculate_repository_storage(repo)

    # Both treated as non-LFS — logo.png because dict.get returns False default
    assert storage["current_branch_bytes"] == 5080
    assert storage["current_branch_non_lfs_bytes"] == 5080
    assert storage["total_bytes"] == 5080


@pytest.mark.asyncio
async def test_calculate_repository_storage_file_row_without_lakefs_object(
    monkeypatch,
):
    """File row exists but the path is NOT listed by LakeFS → ignored.

    The loop only iterates over LakeFS results, so File rows for paths that
    aren't in the current branch contribute 0 to the running totals. The
    bulk-load step still loads the row, but it stays unused.
    """
    repo = _MutableRepo(repo_type="model", full_id="owner/stale-row")
    client = _FakeLakeFSClient([_single_page([("present.txt", 30)])])
    _FakeFileModel.rows = {"present.txt": False, "stale_lfs.bin": True}
    _FakeLFSObjectHistoryModel.items = []
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: client)

    storage = await quota_util.calculate_repository_storage(repo)

    # Only the path actually in LakeFS contributes
    assert storage["current_branch_bytes"] == 30
    assert storage["current_branch_non_lfs_bytes"] == 30
    # stale_lfs.bin not in LakeFS list → no LFS bytes from main; LFS history is empty
    assert storage["lfs_unique_bytes"] == 0
    assert storage["total_bytes"] == 30


@pytest.mark.asyncio
async def test_calculate_repository_storage_mixed_lfs_and_non_lfs_with_history(
    monkeypatch,
):
    """End-to-end mix that exercises every code path in one shot."""
    repo = _MutableRepo(repo_type="dataset", full_id="owner/mixed")
    client = _FakeLakeFSClient(
        [
            _single_page(
                [
                    ("model.safetensors", 1_000_000),  # LFS, in history
                    ("config.json", 500),  # non-LFS
                    ("tokenizer.json", 8_000),  # non-LFS
                    ("orphan.txt", 12),  # not in File DB → non-LFS
                ]
            )
        ]
    )
    _FakeFileModel.rows = {
        "model.safetensors": True,
        "config.json": False,
        "tokenizer.json": False,
    }
    # Two historical versions of model.safetensors (sha-old, sha-new), plus a
    # since-deleted LFS (sha-old-deleted) that's still in history.
    _FakeLFSObjectHistoryModel.items = [
        SimpleNamespace(sha256="sha-new", size=1_000_000),
        SimpleNamespace(sha256="sha-old", size=900_000),
        SimpleNamespace(sha256="sha-old-deleted", size=200_000),
    ]
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: client)

    storage = await quota_util.calculate_repository_storage(repo)

    assert storage["current_branch_bytes"] == 1_000_000 + 500 + 8_000 + 12
    assert storage["current_branch_non_lfs_bytes"] == 500 + 8_000 + 12
    assert storage["lfs_total_bytes"] == 1_000_000 + 900_000 + 200_000
    assert storage["lfs_unique_bytes"] == 1_000_000 + 900_000 + 200_000
    assert (
        storage["total_bytes"]
        == storage["current_branch_non_lfs_bytes"] + storage["lfs_unique_bytes"]
    )


@pytest.mark.asyncio
async def test_calculate_repository_storage_pagination_continues_correctly(monkeypatch):
    """Verify the second page request carries the previous `next_offset`.

    The bulk-fetch was added before the loop; the loop itself must still
    correctly thread pagination state across pages.
    """
    repo = _MutableRepo(repo_type="model", full_id="owner/paged")
    client = _FakeLakeFSClient(
        [
            {
                "results": [
                    {"path_type": "object", "path": "p1", "size_bytes": 1},
                    {"path_type": "object", "path": "p2", "size_bytes": 2},
                ],
                "pagination": {"has_more": True, "next_offset": "cursor-A"},
            },
            {
                "results": [
                    {"path_type": "object", "path": "p3", "size_bytes": 3},
                ],
                "pagination": {"has_more": True, "next_offset": "cursor-B"},
            },
            {
                "results": [
                    {"path_type": "object", "path": "p4", "size_bytes": 4},
                ],
                "pagination": {"has_more": False},
            },
        ]
    )
    _FakeFileModel.rows = {"p1": False, "p2": False, "p3": False, "p4": False}
    _FakeLFSObjectHistoryModel.items = []
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: client)

    storage = await quota_util.calculate_repository_storage(repo)

    assert storage["current_branch_bytes"] == 10
    # Pagination cursor was forwarded correctly
    assert client.calls[0].get("after", "") == ""
    assert client.calls[1]["after"] == "cursor-A"
    assert client.calls[2]["after"] == "cursor-B"


@pytest.mark.asyncio
async def test_calculate_repository_storage_skips_non_object_path_types(monkeypatch):
    """LakeFS may yield `path_type='common_prefix'` rows; they must be skipped."""
    repo = _MutableRepo(repo_type="model", full_id="owner/prefixes")
    # Mix object + common_prefix entries on the same page
    client = _FakeLakeFSClient(
        [
            {
                "results": [
                    {"path_type": "common_prefix", "path": "subdir/", "size_bytes": 0},
                    {"path_type": "object", "path": "file.txt", "size_bytes": 42},
                ],
                "pagination": {"has_more": False},
            }
        ]
    )
    _FakeFileModel.rows = {"file.txt": False}
    _FakeLFSObjectHistoryModel.items = []
    monkeypatch.setattr(quota_util, "get_lakefs_client", lambda: client)

    storage = await quota_util.calculate_repository_storage(repo)

    # common_prefix row contributed nothing
    assert storage["current_branch_bytes"] == 42


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
