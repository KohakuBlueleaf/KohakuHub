"""Pytest fixtures for KohakuHub backend tests."""

from __future__ import annotations

import os

import httpx
import pytest
import pytest_asyncio


TEST_PROFILE = os.environ.get("KOHAKUHUB_TEST_PROFILE", "fast")


if TEST_PROFILE == "fast":
    from test.kohakuhub.support.bootstrap import (
        ADMIN_TOKEN,
        DEFAULT_PASSWORD,
        apply_fast_test_env,
    )
    from test.kohakuhub.support.state import create_fast_test_state

    apply_fast_test_env()

    @pytest.fixture(scope="session")
    def fast_test_state():
        state = create_fast_test_state()
        return state


    @pytest.fixture(scope="session")
    def app(fast_test_state):
        return fast_test_state.modules.app


    @pytest.fixture(scope="session", autouse=True)
    async def _prepare_fast_state(fast_test_state):
        await fast_test_state.prepare()


    @pytest.fixture(autouse=True)
    def _restore_fast_state(fast_test_state):
        fast_test_state.restore_active_state()


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
