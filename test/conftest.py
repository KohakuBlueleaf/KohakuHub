"""Pytest fixtures for KohakuHub backend tests."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from test.kohakuhub.support.bootstrap import ADMIN_TOKEN, DEFAULT_PASSWORD
from test.kohakuhub.support.service_bootstrap import apply_service_test_env
from test.kohakuhub.support.service_state import create_service_test_state

apply_service_test_env()


@pytest.fixture(scope="session")
def backend_test_state(pytestconfig):
    terminal_reporter = pytestconfig.pluginmanager.get_plugin("terminalreporter")

    def report_progress(message: str) -> None:
        if terminal_reporter is not None:
            terminal_reporter.write_line(f"[backend-test] {message}")

    return create_service_test_state(progress_callback=report_progress)


@pytest.fixture(scope="session")
def app(backend_test_state):
    return backend_test_state.modules.app


@pytest.fixture(scope="session", autouse=True)
async def _prepare_backend_state(backend_test_state):
    await backend_test_state.prepare()


@pytest.fixture(autouse=True)
def _restore_backend_state(backend_test_state):
    backend_test_state.restore_active_state()


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
