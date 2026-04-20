"""Tests for async utility wrappers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import kohakuhub.async_utils as async_utils


class DummyLoop:
    """Loop stub capturing executor invocations."""

    def __init__(self):
        self.calls: list[tuple[object, object, tuple[object, ...]]] = []

    async def run_in_executor(self, executor, func, *args):
        self.calls.append((executor, func, args))
        return func(*args)


def _named_callable(name: str):
    def _call(**_kwargs):
        return None

    _call.__name__ = name
    return _call


@pytest.mark.asyncio
async def test_executor_helpers_forward_args_and_kwargs(monkeypatch):
    loop = DummyLoop()
    monkeypatch.setattr(async_utils.asyncio, "get_event_loop", lambda: loop)

    def combine(value: int, *, increment: int = 0) -> int:
        return value + increment

    assert await async_utils.run_in_s3_executor(combine, 2, increment=3) == 5
    assert await async_utils.run_in_lakefs_executor(combine, 3, increment=4) == 7
    assert await async_utils.run_in_db_executor(combine, 4, increment=5) == 9

    assert loop.calls[0][0] is async_utils._s3_executor
    assert loop.calls[1][0] is async_utils._lakefs_executor
    assert loop.calls[2][0] is async_utils._db_executor


@pytest.mark.asyncio
async def test_legacy_executor_alias_uses_lakefs_executor(monkeypatch):
    captured: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def fake_run_in_lakefs_executor(func, *args, **kwargs):
        captured.append((args, kwargs))
        return "done"

    monkeypatch.setattr(async_utils, "run_in_lakefs_executor", fake_run_in_lakefs_executor)

    result = await async_utils.run_in_executor(lambda value: value, 1, flag=True)

    assert result == "done"
    assert captured == [((1,), {"flag": True})]


@pytest.mark.asyncio
async def test_make_async_wrappers_delegate_to_expected_executor(monkeypatch):
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def fake_s3(func, *args, **kwargs):
        calls.append(("s3", args, kwargs))
        return func(*args, **kwargs)

    async def fake_lakefs(func, *args, **kwargs):
        calls.append(("lakefs", args, kwargs))
        return func(*args, **kwargs)

    async def fake_db(func, *args, **kwargs):
        calls.append(("db", args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(async_utils, "run_in_s3_executor", fake_s3)
    monkeypatch.setattr(async_utils, "run_in_lakefs_executor", fake_lakefs)
    monkeypatch.setattr(async_utils, "run_in_db_executor", fake_db)

    def multiply(value: int, *, factor: int = 1) -> int:
        return value * factor

    assert await async_utils.make_async_s3(multiply)(3, factor=2) == 6
    assert await async_utils.make_async_lakefs(multiply)(4, factor=3) == 12
    assert await async_utils.make_async_db(multiply)(5, factor=4) == 20
    assert await async_utils.make_async(multiply)(6, factor=5) == 30

    assert calls == [
        ("s3", (3,), {"factor": 2}),
        ("lakefs", (4,), {"factor": 3}),
        ("db", (5,), {"factor": 4}),
        ("lakefs", (6,), {"factor": 5}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "client_attr", "expected_name", "kwargs"),
    [
        ("link_physical_address", "staging", "link_physical_address", {"path": "weights.bin"}),
        ("upload_object", "objects", "upload_object", {"path": "weights.bin"}),
        ("commit", "commits", "commit", {"message": "save"}),
        ("delete_object", "objects", "delete_object", {"path": "weights.bin"}),
        ("list_objects", "objects", "list_objects", {"prefix": "folder/"}),
        ("stat_object", "objects", "stat_object", {"path": "weights.bin"}),
        ("get_object", "objects", "get_object", {"path": "weights.bin"}),
        ("get_commit", "commits", "get_commit", {"commit_id": "abc"}),
        ("create_repository", "repositories", "create_repository", {"name": "demo"}),
        ("delete_repository", "repositories", "delete_repository", {"repository": "demo"}),
        ("create_branch", "branches", "create_branch", {"name": "feature", "source": "main"}),
        ("delete_branch", "branches", "delete_branch", {"branch": "feature"}),
        ("create_tag", "tags", "create_tag", {"tag": "v1", "ref": "main"}),
        ("delete_tag", "tags", "delete_tag", {"tag": "v1"}),
        ("log_commits", "commits", "log_commits", {"amount": 5}),
    ],
)
async def test_async_lakefs_client_wraps_common_blocking_methods(
    monkeypatch, method_name, client_attr, expected_name, kwargs
):
    captured: list[tuple[object, dict[str, object]]] = []

    async def fake_run(func, **run_kwargs):
        captured.append((func, run_kwargs))
        return {"method": func.__name__, "kwargs": run_kwargs}

    monkeypatch.setattr(async_utils, "run_in_lakefs_executor", fake_run)

    client = SimpleNamespace(
        staging=SimpleNamespace(link_physical_address=_named_callable("link_physical_address")),
        objects=SimpleNamespace(
            upload_object=_named_callable("upload_object"),
            delete_object=_named_callable("delete_object"),
            list_objects=_named_callable("list_objects"),
            stat_object=_named_callable("stat_object"),
            get_object=_named_callable("get_object"),
        ),
        commits=SimpleNamespace(
            commit=_named_callable("commit"),
            get_commit=_named_callable("get_commit"),
            log_commits=_named_callable("log_commits"),
        ),
        repositories=SimpleNamespace(
            create_repository=_named_callable("create_repository"),
            delete_repository=_named_callable("delete_repository"),
        ),
        branches=SimpleNamespace(
            create_branch=_named_callable("create_branch"),
            delete_branch=_named_callable("delete_branch"),
        ),
        tags=SimpleNamespace(
            create_tag=_named_callable("create_tag"),
            delete_tag=_named_callable("delete_tag"),
        ),
    )
    wrapped = async_utils.AsyncLakeFSClient(client)

    result = await getattr(wrapped, method_name)(**kwargs)

    func, passed_kwargs = captured[0]
    assert func is getattr(getattr(client, client_attr), expected_name)
    assert passed_kwargs == kwargs
    assert result == {"method": expected_name, "kwargs": kwargs}


def test_async_lakefs_client_properties_expose_sync_namespaces():
    client = SimpleNamespace(
        repositories=object(),
        branches=object(),
        commits=object(),
        staging=object(),
        objects=object(),
    )
    wrapped = async_utils.AsyncLakeFSClient(client)

    assert wrapped.repositories is client.repositories
    assert wrapped.branches is client.branches
    assert wrapped.commits is client.commits
    assert wrapped.staging is client.staging
    assert wrapped.objects is client.objects


def test_get_async_lakefs_client_uses_rest_client_factory(monkeypatch):
    expected = object()
    monkeypatch.setattr(
        "kohakuhub.lakefs_rest_client.get_lakefs_rest_client",
        lambda: expected,
    )

    assert async_utils.get_async_lakefs_client() is expected
