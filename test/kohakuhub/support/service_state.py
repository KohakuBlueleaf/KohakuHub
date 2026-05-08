"""Service-backed state manager for backend tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import psycopg2

from scripts.dev.init_lakefs import initialize_lakefs
from test.kohakuhub.support.bootstrap import load_backend_modules
from test.kohakuhub.support.seed import build_baseline
from test.kohakuhub.support.service_bootstrap import (
    DEV_LAKEFS_CREDENTIALS_FILE,
    apply_service_test_env,
    get_service_test_config,
)


def _read_credentials(path: Path) -> tuple[str, str] | None:
    if not path.exists():
        return None

    access_key = ""
    secret_key = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "KOHAKU_HUB_LAKEFS_ACCESS_KEY":
            access_key = value
        elif key == "KOHAKU_HUB_LAKEFS_SECRET_KEY":
            secret_key = value

    if access_key and secret_key:
        return access_key, secret_key
    return None


def _lakefs_credentials_valid(endpoint: str, credentials_file: Path) -> bool:
    credentials = _read_credentials(credentials_file)
    if credentials is None:
        return False

    access_key, secret_key = credentials
    try:
        response = httpx.get(
            f"{endpoint.rstrip('/')}/api/v1/repositories",
            auth=(access_key, secret_key),
            timeout=5.0,
        )
    except Exception:
        return False

    return response.status_code == 200


def _postgres_admin_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    db_name = parsed.path.lstrip("/")
    admin_db = "postgres" if db_name != "postgres" else db_name
    return parsed._replace(path=f"/{admin_db}").geturl()


def _wait_for_postgres(database_url: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    admin_url = _postgres_admin_url(database_url)
    while time.time() < deadline:
        try:
            conn = psycopg2.connect(admin_url)
            conn.close()
            return
        except Exception:
            time.sleep(1)

    parsed = urlparse(database_url)
    raise TimeoutError(
        f"Timed out waiting for PostgreSQL at {parsed.hostname}:{parsed.port or 5432}"
    )


def _ensure_database_exists(database_url: str) -> None:
    parsed = urlparse(database_url)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise ValueError(f"Database URL is missing a database name: {database_url}")

    conn = psycopg2.connect(_postgres_admin_url(database_url))
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cursor.fetchone() is None:
                cursor.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        conn.close()


def _wait_for_http(url: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for {url}")


def _ensure_services_ready(
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    def report(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    cfg = get_service_test_config()
    report("waiting for PostgreSQL")
    _wait_for_postgres(cfg.database_url)
    report("ensuring the test database exists")
    _ensure_database_exists(cfg.database_url)
    report("waiting for MinIO")
    _wait_for_http(f"{cfg.s3_endpoint.rstrip('/')}/minio/health/live")
    report("checking LakeFS credentials")

    if (
        not _lakefs_credentials_valid(cfg.lakefs_endpoint, cfg.lakefs_credentials_file)
        and _lakefs_credentials_valid(cfg.lakefs_endpoint, DEV_LAKEFS_CREDENTIALS_FILE)
    ):
        cfg.lakefs_credentials_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.lakefs_credentials_file.write_text(
            DEV_LAKEFS_CREDENTIALS_FILE.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    if not _lakefs_credentials_valid(cfg.lakefs_endpoint, cfg.lakefs_credentials_file):
        report("bootstrapping LakeFS test credentials")
        cfg.lakefs_credentials_file.unlink(missing_ok=True)
        result = initialize_lakefs(
            endpoint=cfg.lakefs_endpoint,
            credentials_file=cfg.lakefs_credentials_file,
            admin_user="admin",
            timeout_seconds=60,
        )
        if result != 0:
            raise RuntimeError("Failed to initialize LakeFS test credentials")

    credentials = _read_credentials(cfg.lakefs_credentials_file)
    if credentials is not None:
        os_environ = {
            "KOHAKU_HUB_LAKEFS_ACCESS_KEY": credentials[0],
            "KOHAKU_HUB_LAKEFS_SECRET_KEY": credentials[1],
        }
        for key, value in os_environ.items():
            if not value:
                continue
            os.environ[key] = value


@dataclass(slots=True)
class ServiceTestState:
    """Runtime state used by the service-backed backend test suite."""

    modules: object
    s3_client: object
    lakefs_client: object
    progress_callback: Callable[[str], None] | None = None

    def _report(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    def _close_db(self) -> None:
        db = self.modules.db_module.db
        if not db.is_closed():
            db.close()

    def _reset_database(self) -> None:
        self._close_db()
        conn = psycopg2.connect(self.modules.config_module.cfg.app.database_url)
        conn.autocommit = True
        try:
            with conn.cursor() as cursor:
                cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
                cursor.execute("CREATE SCHEMA public")
                cursor.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")
                cursor.execute("GRANT ALL ON SCHEMA public TO public")
        finally:
            conn.close()

    def _clear_bucket(self) -> None:
        self.modules.s3_module.init_storage()
        bucket = self.modules.config_module.cfg.s3.bucket
        paginator = self.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                self.s3_client.delete_objects(Bucket=bucket, Delete={"Objects": objects})

    async def _list_lakefs_repositories(self) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        after: str | None = None
        lakefs_client = self.lakefs_client

        while True:
            params: dict[str, Any] = {}
            if after:
                params["after"] = after

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{lakefs_client.base_url}/repositories",
                    params=params,
                    auth=lakefs_client.auth,
                    timeout=None,
                )
                lakefs_client._check_response(response)
                payload = response.json()

            if isinstance(payload, list):
                repos.extend(payload)
                return repos

            repos.extend(payload.get("results", []))
            pagination = payload.get("pagination", {})
            if not pagination.get("has_more"):
                return repos

            after = pagination.get("next_offset")
            if not after:
                return repos

    async def _clear_lakefs(self) -> None:
        # Use a per-call ``httpx.AsyncClient`` here — pointedly NOT the pooled
        # singleton inside ``LakeFSRestClient``. ``_restore_active_state`` is
        # driven from ``asyncio.run(...)`` (fresh short-lived loop per call);
        # any pooled client would carry connections from a previous loop and
        # raise "is bound to a different event loop". A non-pooled client
        # opens a fresh connection per request, which is fine here because
        # the cleanup runs at most a handful of repos per iteration.
        lakefs_client = self.lakefs_client
        for repository in await self._list_lakefs_repositories():
            repo_id = repository.get("id")
            if not repo_id:
                continue
            async with httpx.AsyncClient() as raw:
                delete_response = await raw.delete(
                    f"{lakefs_client.base_url}/repositories/{repo_id}",
                    params={"force": "true"},
                    auth=lakefs_client.auth,
                    timeout=None,
                )
            if delete_response.status_code not in (200, 204, 404):
                lakefs_client._check_response(delete_response)

            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                async with httpx.AsyncClient() as raw:
                    exists_response = await raw.get(
                        f"{lakefs_client.base_url}/repositories/{repo_id}",
                        auth=lakefs_client.auth,
                        timeout=None,
                    )
                if exists_response.status_code == 404:
                    break
                await asyncio.sleep(0.25)
            else:
                raise TimeoutError(f"Timed out deleting LakeFS repository: {repo_id}")

    async def _restore_active_state(self, *, emit_progress: bool) -> None:
        report = self._report if emit_progress else (lambda _message: None)

        # The FastAPI handlers' shared ``LakeFSRestClient`` singleton holds
        # a pooled ``httpx.AsyncClient`` bound to whichever event loop it
        # was first used in. ``restore_active_state`` runs from
        # ``asyncio.run(...)`` (a fresh, short-lived loop) and the
        # baseline-seed phase below drives traffic through the FastAPI app
        # which would re-use that pool. Clearing the singleton forces the
        # next handler to lazily rebuild a pool bound to the current loop.
        # (The dedicated ``self.lakefs_client`` is *not* used through the
        # pool by this state plumbing — see ``_clear_lakefs`` for the raw
        # per-call ``httpx.AsyncClient`` form.)
        self.modules.lakefs_rest_client_module._singleton_client = None

        report("clearing LakeFS repositories")
        await self._clear_lakefs()
        report("clearing the object storage bucket")
        self._clear_bucket()
        report("resetting the PostgreSQL schema")
        self._reset_database()
        report("rebuilding the database schema")
        self.modules.db_module.init_db()
        report("initializing the storage bucket")
        self.modules.s3_module.init_storage()
        transport = httpx.ASGITransport(app=self.modules.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            report("seeding the backend baseline")
            await build_baseline(
                client,
                self.s3_client,
                self.modules.config_module.cfg,
            )
        self.modules.fallback_cache_module.get_cache().clear()
        report("baseline restore completed")

    async def prepare(self) -> None:
        """Prepare the backend test baseline in the real service stack."""
        await self._restore_active_state(emit_progress=True)

    def restore_active_state(self) -> None:
        """Restore the backend test baseline in the real service stack."""
        asyncio.run(self._restore_active_state(emit_progress=False))


def create_service_test_state(
    progress_callback: Callable[[str], None] | None = None,
) -> ServiceTestState:
    """Create the service-backed state manager used by the backend suite."""
    apply_service_test_env()
    _ensure_services_ready(progress_callback=progress_callback)
    modules = load_backend_modules(force_reload=True, apply_env=apply_service_test_env)
    s3_client = modules.s3_module.get_s3_client()
    # Construct a *dedicated* LakeFSRestClient for the test-state plumbing
    # rather than reusing the module-level singleton from
    # ``get_lakefs_rest_client()``. Both the FastAPI handlers and the
    # test-state plumbing would otherwise share the same pooled
    # ``httpx.AsyncClient``; because ``restore_active_state`` runs its work
    # under ``asyncio.run(...)`` (which creates and closes a fresh event
    # loop per call), and pytest-asyncio in turn gives each test a separate
    # loop, the shared pooled client would end up bound to a closed loop
    # and the next handler call would raise ``Event loop is closed``.
    # Keeping the test plumbing on its own client isolates that lifecycle.
    lakefs_cfg = modules.config_module.cfg.lakefs
    lakefs_client = modules.lakefs_rest_client_module.LakeFSRestClient(
        endpoint=lakefs_cfg.endpoint,
        access_key=lakefs_cfg.access_key,
        secret_key=lakefs_cfg.secret_key,
    )
    return ServiceTestState(
        modules=modules,
        s3_client=s3_client,
        lakefs_client=lakefs_client,
        progress_callback=progress_callback,
    )
