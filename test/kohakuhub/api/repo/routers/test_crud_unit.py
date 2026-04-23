"""Unit tests for repository CRUD routes and helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.repo.routers.crud as repo_crud


class _Expr:
    def __init__(self, value):
        self.value = value

    def __and__(self, other):
        return _Expr(("and", self.value, getattr(other, "value", other)))


class _Field:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return _Expr((self.name, "==", other))

    def __hash__(self):
        return hash(self.name)


class _Query:
    def __init__(self, items=None, execute_result=1):
        self.items = list(items or [])
        self.execute_result = execute_result
        self.where_calls = []

    def where(self, *args):
        self.where_calls.append(args)
        return self

    def execute(self):
        return self.execute_result

    def __iter__(self):
        return iter(self.items)


class _AtomicContext:
    def __init__(self, seen: dict):
        self.seen = seen

    def __enter__(self):
        self.seen["entered"] = self.seen.get("entered", 0) + 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.seen["exited"] = self.seen.get("exited", 0) + 1
        return False


class _FakeRepositoryModel:
    repo_type = _Field("repo_type")
    namespace = _Field("namespace")
    name = _Field("name")
    id = _Field("id")

    select_query = _Query()
    update_query = _Query()
    get_or_create_calls = []

    @classmethod
    def reset(cls):
        cls.select_query = _Query()
        cls.update_query = _Query()
        cls.get_or_create_calls = []

    @classmethod
    def select(cls):
        return cls.select_query

    @classmethod
    def update(cls, **kwargs):
        cls.update_kwargs = kwargs
        return cls.update_query

    @classmethod
    def get_or_create(cls, **kwargs):
        cls.get_or_create_calls.append(kwargs)
        return SimpleNamespace(full_id=kwargs["full_id"]), True


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.raise_on = {}
        self.list_payloads = []
        self.repository_exists_values = []

    def _maybe_raise(self, name):
        error = self.raise_on.get(name)
        if error:
            raise error

    async def create_repository(self, **kwargs):
        self.calls.append(("create_repository", kwargs))
        self._maybe_raise("create_repository")
        return {"ok": True}

    async def delete_repository(self, **kwargs):
        self.calls.append(("delete_repository", kwargs))
        self._maybe_raise("delete_repository")
        return {"ok": True}

    async def list_objects(self, **kwargs):
        self.calls.append(("list_objects", kwargs))
        self._maybe_raise("list_objects")
        return self.list_payloads.pop(0)

    async def link_physical_address(self, **kwargs):
        self.calls.append(("link_physical_address", kwargs))
        self._maybe_raise("link_physical_address")
        return {"ok": True}

    async def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        self._maybe_raise("get_object")
        return b"content"

    async def upload_object(self, **kwargs):
        self.calls.append(("upload_object", kwargs))
        self._maybe_raise("upload_object")
        return {"ok": True}

    async def commit(self, **kwargs):
        self.calls.append(("commit", kwargs))
        self._maybe_raise("commit")
        return {"ok": True}

    async def repository_exists(self, repo_name):
        self.calls.append(("repository_exists", repo_name))
        if self.repository_exists_values:
            return self.repository_exists_values.pop(0)
        return False


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner()


@pytest.fixture(autouse=True)
def _reset_repo_model():
    _FakeRepositoryModel.reset()


@pytest.mark.asyncio
async def test_create_repo_covers_conflicts_lakefs_failure_and_success(monkeypatch):
    user = SimpleNamespace(username="owner")
    client = _FakeClient()
    monkeypatch.setattr(repo_crud, "Repository", _FakeRepositoryModel)
    monkeypatch.setattr(repo_crud, "check_namespace_permission", lambda namespace, user, is_admin=False: None)
    monkeypatch.setattr(repo_crud, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(repo_crud, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(repo_crud.cfg.s3, "bucket", "hub-storage")
    monkeypatch.setattr(repo_crud.cfg.app, "base_url", "https://hub.example.com")
    monkeypatch.setattr(repo_crud, "get_repository", lambda *_args: None)
    monkeypatch.setattr(
        repo_crud,
        "normalize_name",
        lambda name: name.lower().replace("-", "").replace("_", ""),
    )

    _FakeRepositoryModel.select_query = _Query(items=[SimpleNamespace(name="demo-model")])
    conflict = await repo_crud.create_repo(
        repo_crud.CreateRepoPayload(type="model", name="Demo_Model"), user=user
    )
    # huggingface_hub's create_repo(exist_ok=True) only shortcuts on 409, so the
    # conflict response now uses 409 Conflict with a JSON body carrying `url`.
    assert conflict.status_code == 409
    assert conflict.headers.get("x-error-code") == repo_crud.HFErrorCode.REPO_EXISTS
    assert json.loads(bytes(conflict.body)).get("url")

    monkeypatch.setattr(repo_crud, "get_repository", lambda *_args: SimpleNamespace())
    exact_conflict = await repo_crud.create_repo(
        repo_crud.CreateRepoPayload(type="model", name="demo-model"), user=user
    )
    assert exact_conflict.status_code == 409
    assert json.loads(bytes(exact_conflict.body)).get("url")

    monkeypatch.setattr(repo_crud, "get_repository", lambda *_args: None)
    _FakeRepositoryModel.select_query = _Query(items=[])
    client.raise_on["create_repository"] = RuntimeError("lakefs failed")
    lakefs_error = await repo_crud.create_repo(
        repo_crud.CreateRepoPayload(type="model", name="demo-model"), user=user
    )
    assert lakefs_error.status_code == 500

    client.raise_on.pop("create_repository", None)
    success = await repo_crud.create_repo(
        repo_crud.CreateRepoPayload(type="model", name="demo-model"), user=user
    )
    assert success["repo_id"] == "owner/demo-model"
    assert _FakeRepositoryModel.get_or_create_calls


@pytest.mark.asyncio
async def test_delete_repo_covers_admin_validation_not_found_and_failures(monkeypatch):
    repo_row = SimpleNamespace(delete_instance=lambda: None)
    client = _FakeClient()
    atomic_state = {}

    monkeypatch.setattr(repo_crud, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(repo_crud, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(repo_crud, "check_repo_delete_permission", lambda repo, user, is_admin=False: None)
    monkeypatch.setattr(repo_crud, "cleanup_repository_storage", lambda **kwargs: _async_return({"repo_objects_deleted": 1, "lfs_objects_deleted": 0, "lfs_history_deleted": 0}))
    monkeypatch.setattr(repo_crud, "is_lakefs_not_found_error", lambda error: "404" in str(error))
    monkeypatch.setattr(repo_crud, "db", SimpleNamespace(atomic=lambda: _AtomicContext(atomic_state)))

    with pytest.raises(HTTPException) as admin_missing_org:
        await repo_crud.delete_repo(
            repo_crud.DeleteRepoPayload(type="model", name="demo-model"),
            auth=(None, True),
        )
    assert admin_missing_org.value.status_code == 400

    monkeypatch.setattr(repo_crud, "get_repository", lambda *_args: None)
    not_found = await repo_crud.delete_repo(
        repo_crud.DeleteRepoPayload(type="model", name="demo-model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert not_found.status_code == 404

    monkeypatch.setattr(repo_crud, "get_repository", lambda *_args: repo_row)
    success = await repo_crud.delete_repo(
        repo_crud.DeleteRepoPayload(type="model", name="demo-model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert "deleted" in success["message"].lower()
    assert atomic_state == {"entered": 1, "exited": 1}

    client.raise_on["delete_repository"] = RuntimeError("boom")
    failure = await repo_crud.delete_repo(
        repo_crud.DeleteRepoPayload(type="model", name="demo-model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert failure.status_code == 500

    client.raise_on["delete_repository"] = RuntimeError("404 missing")
    db_failure_repo = SimpleNamespace(delete_instance=lambda: (_ for _ in ()).throw(RuntimeError("db broke")))
    monkeypatch.setattr(repo_crud, "get_repository", lambda *_args: db_failure_repo)
    db_failure = await repo_crud.delete_repo(
        repo_crud.DeleteRepoPayload(type="model", name="demo-model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert db_failure.status_code == 500


@pytest.mark.asyncio
async def test_migrate_lakefs_repository_covers_noop_missing_source_success_and_cleanup(monkeypatch):
    client = _FakeClient()
    from_repo = SimpleNamespace(full_id="owner/from")
    deleted_prefixes = []
    monkeypatch.setattr(repo_crud, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(repo_crud, "lakefs_repo_name", lambda repo_type, repo_id: "same-repo" if repo_id == "owner/same" else repo_id.replace("/", "-"))
    monkeypatch.setattr(repo_crud, "get_repository", lambda repo_type, namespace, name: from_repo if name in {"from", "same"} else None)
    monkeypatch.setattr(repo_crud, "get_file", lambda repo, path: SimpleNamespace(lfs=path.endswith(".bin")) if path != "missing.txt" else None)
    monkeypatch.setattr(repo_crud, "should_use_lfs", lambda repo, path, size: path.endswith(".bin"))
    monkeypatch.setattr(repo_crud, "delete_objects_with_prefix", lambda bucket, prefix: deleted_prefixes.append((bucket, prefix)) or _async_return(2))
    monkeypatch.setattr(repo_crud.cfg.s3, "bucket", "hub-storage")

    await repo_crud._migrate_lakefs_repository("model", "owner/same", "owner/same")

    with pytest.raises(HTTPException) as missing_source:
        await repo_crud._migrate_lakefs_repository("model", "owner/missing", "owner/to")
    assert missing_source.value.status_code == 404

    client.list_payloads = [
        {
            "results": [
                {"path_type": "object", "path": "README.md", "size_bytes": 3, "checksum": "sha256:readme", "physical_address": "s3://bucket/repo/README.md"},
                {"path_type": "object", "path": "weights.bin", "size_bytes": 12, "checksum": "sha256:weights", "physical_address": "s3://bucket/lfs/weights"},
            ],
            "pagination": {"has_more": False},
        }
    ]
    await repo_crud._migrate_lakefs_repository("model", "owner/from", "owner/to")
    method_names = [name for name, _kwargs in client.calls]
    assert "create_repository" in method_names
    assert "link_physical_address" in method_names
    assert "upload_object" in method_names
    assert deleted_prefixes[-1] == ("hub-storage", "owner-from/")

    client.raise_on["create_repository"] = RuntimeError("migration broke")
    with pytest.raises(HTTPException) as migration_error:
        await repo_crud._migrate_lakefs_repository("model", "owner/from", "owner/to")
    assert migration_error.value.status_code == 500


def test_update_repository_database_records_covers_same_and_cross_namespace_moves(monkeypatch):
    increments = []
    repo_row = SimpleNamespace(id=1, quota_bytes=100, used_bytes=50, private=False)
    monkeypatch.setattr(repo_crud, "Repository", _FakeRepositoryModel)
    monkeypatch.setattr(repo_crud, "get_organization", lambda namespace: SimpleNamespace() if namespace.startswith("org") else None)
    monkeypatch.setattr(repo_crud, "increment_storage", lambda **kwargs: increments.append(kwargs))

    repo_crud._update_repository_database_records(
        repo_row=repo_row,
        from_id="owner/from",
        to_id="owner/to",
        from_namespace="owner",
        to_namespace="owner",
        to_name="to",
        moving_namespace=False,
        repo_size=0,
    )
    assert _FakeRepositoryModel.update_kwargs["quota_bytes"] == 100

    repo_crud._update_repository_database_records(
        repo_row=repo_row,
        from_id="owner/from",
        to_id="org-team/to",
        from_namespace="owner",
        to_namespace="org-team",
        to_name="to",
        moving_namespace=True,
        repo_size=25,
    )
    assert _FakeRepositoryModel.update_kwargs["quota_bytes"] is None
    assert increments[0]["bytes_delta"] == -25
    assert increments[1]["bytes_delta"] == 25


@pytest.mark.asyncio
async def test_move_repo_covers_validation_quota_success_and_nonfatal_cleanup(monkeypatch):
    repo_row = SimpleNamespace(private=False)
    atomic_state = {}
    monkeypatch.setattr(repo_crud, "check_repo_delete_permission", lambda repo, user, is_admin=False: None)
    monkeypatch.setattr(repo_crud, "check_namespace_permission", lambda namespace, user, is_admin=False: None)
    monkeypatch.setattr(repo_crud, "get_repository", lambda repo_type, namespace, name: repo_row if (namespace, name) == ("owner", "from") else None)
    monkeypatch.setattr(repo_crud, "calculate_repository_storage", lambda repo: _async_return({"total_bytes": 12}))
    monkeypatch.setattr(repo_crud, "get_organization", lambda namespace: None)
    monkeypatch.setattr(repo_crud, "check_quota", lambda **kwargs: (True, None))
    monkeypatch.setattr(repo_crud, "_migrate_lakefs_repository", lambda **kwargs: _async_return(None))
    monkeypatch.setattr(repo_crud, "_update_repository_database_records", lambda **kwargs: atomic_state.setdefault("updated", []).append(kwargs))
    monkeypatch.setattr(repo_crud, "db", SimpleNamespace(atomic=lambda: _AtomicContext(atomic_state)))
    monkeypatch.setattr(repo_crud, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(repo_crud, "cleanup_repository_storage", lambda **kwargs: _async_return({"repo_objects_deleted": 1, "lfs_objects_deleted": 0, "lfs_history_deleted": 0}))
    monkeypatch.setattr(repo_crud.cfg.app, "base_url", "https://hub.example.com")

    bad_source = await repo_crud.move_repo(
        repo_crud.MoveRepoPayload(fromRepo="bad", toRepo="owner/to", type="model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert bad_source.status_code == 400

    bad_target = await repo_crud.move_repo(
        repo_crud.MoveRepoPayload(fromRepo="owner/from", toRepo="bad", type="model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert bad_target.status_code == 400

    monkeypatch.setattr(repo_crud, "get_repository", lambda repo_type, namespace, name: None)
    not_found = await repo_crud.move_repo(
        repo_crud.MoveRepoPayload(fromRepo="owner/from", toRepo="owner/to", type="model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert not_found.status_code == 404

    monkeypatch.setattr(repo_crud, "get_repository", lambda repo_type, namespace, name: repo_row if (namespace, name) == ("owner", "from") else SimpleNamespace())
    exists = await repo_crud.move_repo(
        repo_crud.MoveRepoPayload(fromRepo="owner/from", toRepo="owner/to", type="model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert exists.status_code == 409
    assert exists.headers.get("x-error-code") == repo_crud.HFErrorCode.REPO_EXISTS

    monkeypatch.setattr(repo_crud, "get_repository", lambda repo_type, namespace, name: repo_row if (namespace, name) == ("owner", "from") else None)
    monkeypatch.setattr(repo_crud, "check_quota", lambda **kwargs: (False, "quota exceeded"))
    with pytest.raises(HTTPException) as quota_error:
        await repo_crud.move_repo(
            repo_crud.MoveRepoPayload(fromRepo="owner/from", toRepo="other/to", type="model"),
            auth=(SimpleNamespace(username="owner"), False),
        )
    assert quota_error.value.status_code == 400

    monkeypatch.setattr(repo_crud, "check_quota", lambda **kwargs: (True, None))
    success = await repo_crud.move_repo(
        repo_crud.MoveRepoPayload(fromRepo="owner/from", toRepo="other/to", type="model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert success["success"] is True
    assert atomic_state["updated"]

    monkeypatch.setattr(repo_crud, "cleanup_repository_storage", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("cleanup failed")))
    success = await repo_crud.move_repo(
        repo_crud.MoveRepoPayload(fromRepo="owner/from", toRepo="other/to", type="model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert success["success"] is True


@pytest.mark.asyncio
async def test_squash_repo_covers_validation_success_and_recovery(monkeypatch):
    repo_row = SimpleNamespace(private=False)
    temp_repo = SimpleNamespace(private=False)
    atomic_state = {}
    client = _FakeClient()
    client.repository_exists_values = [True, False, True, False]
    repo_lookup = {"initial": repo_row, "temp": temp_repo, "final": repo_row}

    def fake_get_repository(repo_type, namespace, name):
        if name == "demo":
            return repo_lookup["initial"]
        if name.startswith("demo-squash-"):
            return repo_lookup["temp"]
        return repo_lookup["final"]

    monkeypatch.setattr(repo_crud, "get_repository", fake_get_repository)
    monkeypatch.setattr(repo_crud, "check_repo_delete_permission", lambda repo, user, is_admin=False: None)
    monkeypatch.setattr(repo_crud, "_migrate_lakefs_repository", lambda **kwargs: _async_return(None))
    monkeypatch.setattr(repo_crud, "_update_repository_database_records", lambda **kwargs: atomic_state.setdefault("updated", []).append(kwargs))
    monkeypatch.setattr(repo_crud, "cleanup_repository_storage", lambda **kwargs: _async_return({"repo_objects_deleted": 1, "lfs_objects_deleted": 0, "lfs_history_deleted": 0}))
    monkeypatch.setattr(repo_crud, "get_lakefs_client", lambda: client)
    monkeypatch.setattr(repo_crud, "lakefs_repo_name", lambda repo_type, repo_id: f"{repo_type}:{repo_id}")
    monkeypatch.setattr(repo_crud, "update_repository_storage", lambda repo: _async_return(None))
    monkeypatch.setattr(repo_crud, "db", SimpleNamespace(atomic=lambda: _AtomicContext(atomic_state)))
    monkeypatch.setattr(repo_crud.uuid, "uuid4", lambda: SimpleNamespace(hex="abc12345deadbeef"))

    bad_id = await repo_crud.squash_repo(
        repo_crud.SquashRepoPayload(repo="bad", type="model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert bad_id.status_code == 400

    monkeypatch.setattr(repo_crud, "get_repository", lambda *_args: None)
    not_found = await repo_crud.squash_repo(
        repo_crud.SquashRepoPayload(repo="owner/demo", type="model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert not_found.status_code == 404

    monkeypatch.setattr(repo_crud, "get_repository", fake_get_repository)
    success = await repo_crud.squash_repo(
        repo_crud.SquashRepoPayload(repo="owner/demo", type="model"),
        auth=(SimpleNamespace(username="owner"), False),
    )
    assert success["success"] is True
    assert atomic_state["updated"]

    monkeypatch.setattr(repo_crud, "_migrate_lakefs_repository", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("squash failed")))
    with pytest.raises(HTTPException) as squash_error:
        await repo_crud.squash_repo(
            repo_crud.SquashRepoPayload(repo="owner/demo", type="model"),
            auth=(SimpleNamespace(username="owner"), False),
        )
    assert squash_error.value.status_code == 500
