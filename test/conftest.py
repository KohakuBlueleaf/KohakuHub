"""Pytest fixtures for KohakuHub backend tests."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from test.kohakuhub.support.bootstrap import ADMIN_TOKEN, DEFAULT_PASSWORD
from test.kohakuhub.support.live_server import start_live_server, stop_live_server
from test.kohakuhub.support.service_bootstrap import apply_service_test_env
from test.kohakuhub.support.service_state import create_service_test_state

apply_service_test_env()

_INITIAL_BASELINE_READY = False
_LAST_BACKEND_MODULE = None
_BACKEND_FIXTURE_NAMES = {
    "prepared_backend_test_state",
    "backend_test_state",
    "app",
    "client",
    "owner_client",
    "member_client",
    "visitor_client",
    "outsider_client",
    "admin_client",
    "live_server_url",
    "hf_api_token",
}


@pytest.fixture(scope="session")
def backend_test_state(pytestconfig):
    terminal_reporter = pytestconfig.pluginmanager.get_plugin("terminalreporter")

    def report_progress(message: str) -> None:
        if terminal_reporter is not None:
            terminal_reporter.write_line(f"[backend-test] {message}")

    return create_service_test_state(progress_callback=report_progress)


@pytest.fixture(scope="session")
async def prepared_backend_test_state(backend_test_state):
    global _INITIAL_BASELINE_READY
    await backend_test_state.prepare()
    _INITIAL_BASELINE_READY = True
    return backend_test_state


@pytest.fixture(scope="session")
def app(prepared_backend_test_state):
    return prepared_backend_test_state.modules.app


@pytest.fixture(autouse=True)
def _restore_backend_state_per_test(request):
    global _INITIAL_BASELINE_READY, _LAST_BACKEND_MODULE

    needs_backend = bool(_BACKEND_FIXTURE_NAMES.intersection(request.fixturenames))
    if not needs_backend:
        return

    backend_test_state = request.getfixturevalue("backend_test_state")
    request.getfixturevalue("prepared_backend_test_state")
    current_module = request.node.module.__name__

    # Drop the LakeFSRestClient singleton's pooled httpx client between
    # tests. ``httpx.AsyncClient`` binds its connection pool to the event
    # loop it was constructed in. Two loops are in play:
    #
    #   * ``restore_active_state()`` uses ``asyncio.run(...)`` internally —
    #     a short-lived loop for the clear+seed phase. FastAPI handlers
    #     driven during the seed lazily build the pool *inside* that loop,
    #     then the loop closes.
    #   * pytest-asyncio gives each test its own fresh loop.
    #
    # We null the singleton both *before* the restore (so the seed phase
    # doesn't inherit a pool tied to the previous test's closed loop) and
    # again *after* (so the test body doesn't inherit the seed phase's
    # pool tied to its now-closed asyncio.run loop). Without the second
    # reset, handlers raise ``Event loop is closed`` on the first LakeFS
    # call.
    from kohakuhub import lakefs_rest_client as _lakefs_rest
    _lakefs_rest._singleton_client = None

    if request.node.get_closest_marker("backend_per_test") is not None:
        backend_test_state.restore_active_state()
        _lakefs_rest._singleton_client = None
        _LAST_BACKEND_MODULE = current_module
        return

    if _LAST_BACKEND_MODULE != current_module:
        if _INITIAL_BASELINE_READY:
            _INITIAL_BASELINE_READY = False
        else:
            backend_test_state.restore_active_state()
            _lakefs_rest._singleton_client = None
        _LAST_BACKEND_MODULE = current_module


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as async_client:
        yield async_client


@pytest_asyncio.fixture
async def owner_client(client):
    response = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": DEFAULT_PASSWORD},
    )
    response.raise_for_status()
    return client


@pytest_asyncio.fixture
async def member_client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as async_client:
        response = await async_client.post(
            "/api/auth/login",
            json={"username": "member", "password": DEFAULT_PASSWORD},
        )
        response.raise_for_status()
        yield async_client


@pytest_asyncio.fixture
async def visitor_client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as async_client:
        response = await async_client.post(
            "/api/auth/login",
            json={"username": "visitor", "password": DEFAULT_PASSWORD},
        )
        response.raise_for_status()
        yield async_client


@pytest_asyncio.fixture
async def outsider_client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as async_client:
        response = await async_client.post(
            "/api/auth/login",
            json={"username": "outsider", "password": DEFAULT_PASSWORD},
        )
        response.raise_for_status()
        yield async_client


@pytest_asyncio.fixture
async def admin_client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
        headers={"X-Admin-Token": ADMIN_TOKEN},
    ) as async_client:
        yield async_client


@pytest.fixture
def live_server_url(app, backend_test_state):
    cfg = backend_test_state.modules.config_module.cfg
    previous_base_url = cfg.app.base_url
    previous_internal_base_url = cfg.app.internal_base_url
    handle = start_live_server(app)
    cfg.app.base_url = handle.base_url
    cfg.app.internal_base_url = handle.base_url
    try:
        yield handle.base_url
    finally:
        cfg.app.base_url = previous_base_url
        cfg.app.internal_base_url = previous_internal_base_url
        stop_live_server(handle)


@pytest_asyncio.fixture
async def hf_api_token(owner_client):
    response = await owner_client.post(
        "/api/auth/tokens/create",
        json={"name": "hf-api-compat"},
    )
    response.raise_for_status()
    return response.json()["token"]
