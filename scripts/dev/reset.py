#!/usr/bin/env python3
"""Pure local helper for clearing KohakuHub dev state."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(1, str(SRC_DIR))

from kohakuhub.api.fallback.cache import get_cache
from kohakuhub.config import cfg
from kohakuhub.db import FallbackSource, Invitation, Repository, User, db, init_db
from kohakuhub.logger import get_logger
from kohakuhub.utils.lakefs import get_lakefs_client
from kohakuhub.utils.s3 import delete_objects_with_prefix, init_storage

logger = get_logger("LOCAL_DEV_RESET")

DEMO_SEED_MANIFEST_PATH = ROOT_DIR / "hub-meta" / "dev" / "demo-seed-manifest.json"


def _collect_reset_counts() -> dict[str, int]:
    return {
        "users_cleared": User.select().where(User.is_org == False).count(),
        "organizations_cleared": User.select().where(User.is_org == True).count(),
        "repositories_cleared": Repository.select().count(),
        "invitations_cleared": Invitation.select().count(),
        "fallback_sources_cleared": FallbackSource.select().count(),
    }


async def _list_all_lakefs_repositories() -> list[dict[str, Any]]:
    lakefs_client = get_lakefs_client()
    repositories: list[dict[str, Any]] = []
    after: str | None = None

    while True:
        payload = await lakefs_client.list_repositories(amount=1000, after=after)

        if isinstance(payload, list):
            repositories.extend(payload)
            return repositories

        repositories.extend(payload.get("results", []))
        pagination = payload.get("pagination", {})
        if not pagination.get("has_more"):
            return repositories

        after = pagination.get("next_offset")
        if not after:
            return repositories


async def _clear_lakefs_repositories() -> int:
    lakefs_client = get_lakefs_client()
    deleted = 0

    for repository in await _list_all_lakefs_repositories():
        repo_id = repository.get("id")
        if not repo_id:
            continue

        await lakefs_client.delete_repository(repository=repo_id, force=True)
        deadline = asyncio.get_running_loop().time() + 20.0
        while asyncio.get_running_loop().time() < deadline:
            if not await lakefs_client.repository_exists(repo_id):
                deleted += 1
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError(f"Timed out deleting LakeFS repository: {repo_id}")

    return deleted


def _reset_database_state() -> None:
    if cfg.app.db_backend == "postgres":
        if not db.is_closed():
            db.close()
        db.connect(reuse_if_open=True)
        connection = db.connection()
        previous_autocommit = connection.autocommit
        connection.autocommit = True
        try:
            db.execute_sql("DROP SCHEMA IF EXISTS public CASCADE")
            db.execute_sql("CREATE SCHEMA public")
            db.execute_sql("GRANT ALL ON SCHEMA public TO CURRENT_USER")
            db.execute_sql("GRANT ALL ON SCHEMA public TO public")
        finally:
            connection.autocommit = previous_autocommit
            db.close()
    elif cfg.app.db_backend == "sqlite":
        if not db.is_closed():
            db.close()
        db.connect(reuse_if_open=True)
        for table_name in (
            "confirmationtoken",
            "fallbacksource",
            "dailyrepostats",
            "downloadsession",
            "repositorylike",
            "invitation",
            "sshkey",
            "lfsobjecthistory",
            "commit",
            "userorganization",
            "stagingupload",
            "file",
            "repository",
            "userexternaltoken",
            "token",
            "session",
            "emailverification",
            "user",
        ):
            db.execute_sql(f'DROP TABLE IF EXISTS "{table_name}"')
        db.close()
    else:
        raise RuntimeError(f"Unsupported database backend: {cfg.app.db_backend}")

    init_db()


async def reset_local_data() -> dict[str, Any]:
    """Clear local-development state without deleting Docker bind-mount paths."""
    summary = _collect_reset_counts()
    warnings: list[str] = []

    init_storage()
    summary["lakefs_repositories_deleted"] = await _clear_lakefs_repositories()
    summary["s3_objects_deleted"] = await delete_objects_with_prefix(cfg.s3.bucket, "")
    _reset_database_state()
    init_storage()

    try:
        get_cache().clear()
        summary["fallback_cache_cleared"] = True
    except Exception as exc:
        logger.warning(f"Failed to clear fallback cache after local reset: {exc}")
        warnings.append(f"Fallback cache clear failed: {exc}")
        summary["fallback_cache_cleared"] = False

    if DEMO_SEED_MANIFEST_PATH.exists():
        try:
            DEMO_SEED_MANIFEST_PATH.unlink()
            summary["manifest_removed"] = True
        except Exception as exc:
            logger.warning(f"Failed to remove demo seed manifest: {exc}")
            warnings.append(f"Demo seed manifest removal failed: {exc}")
            summary["manifest_removed"] = False
    else:
        summary["manifest_removed"] = False

    summary["database_reinitialized"] = True

    logger.warning(
        "Completed local development reset: "
        f"{summary['repositories_cleared']} repo(s), "
        f"{summary['users_cleared']} user(s), "
        f"{summary['organizations_cleared']} org(s), "
        f"{summary['lakefs_repositories_deleted']} LakeFS repo(s), "
        f"{summary['s3_objects_deleted']} S3 object(s)"
    )

    return {
        "success": True,
        "summary": summary,
        "warnings": warnings,
    }
