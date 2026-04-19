"""Bootstrap helpers for backend tests."""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / "src"
TEST_ROOT = ROOT_DIR / "hub-meta" / "test" / "backend"
STATE_DIR = TEST_ROOT / "state"
ACTIVE_DB_PATH = STATE_DIR / "active.sqlite3"
BASELINE_DB_PATH = STATE_DIR / "baseline.sqlite3"
SNAPSHOT_PATH = STATE_DIR / "baseline-services.json"
STATE_VERSION = "backend-test-state-v1"
DEFAULT_PASSWORD = "KohakuTest123!"
ADMIN_TOKEN = "test-admin-token"

_ENV_APPLIED = False


def ensure_paths() -> None:
    """Create local directories used by backend tests."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def apply_fast_test_env() -> None:
    """Apply deterministic environment overrides for the fast backend suite."""
    global _ENV_APPLIED
    if _ENV_APPLIED:
        return

    ensure_paths()

    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

    env = {
        "KOHAKU_HUB_BASE_URL": "http://testserver",
        "KOHAKU_HUB_INTERNAL_BASE_URL": "http://testserver",
        "KOHAKU_HUB_API_BASE": "/api",
        "KOHAKU_HUB_DB_BACKEND": "sqlite",
        "KOHAKU_HUB_DATABASE_URL": f"sqlite:///{ACTIVE_DB_PATH}",
        "KOHAKU_HUB_DATABASE_KEY": "test-database-key",
        "KOHAKU_HUB_S3_ENDPOINT": "http://fake-s3.local",
        "KOHAKU_HUB_S3_PUBLIC_ENDPOINT": "http://fake-s3.local",
        "KOHAKU_HUB_S3_ACCESS_KEY": "test-access-key",
        "KOHAKU_HUB_S3_SECRET_KEY": "test-secret-key",
        "KOHAKU_HUB_S3_BUCKET": "test-bucket",
        "KOHAKU_HUB_S3_REGION": "us-east-1",
        "KOHAKU_HUB_LAKEFS_ENDPOINT": "http://fake-lakefs.local",
        "KOHAKU_HUB_LAKEFS_ACCESS_KEY": "test-access-key",
        "KOHAKU_HUB_LAKEFS_SECRET_KEY": "test-secret-key",
        "KOHAKU_HUB_LAKEFS_REPO_NAMESPACE": "hf",
        "KOHAKU_HUB_SESSION_SECRET": "test-session-secret",
        "KOHAKU_HUB_SESSION_EXPIRE_HOURS": "168",
        "KOHAKU_HUB_TOKEN_EXPIRE_DAYS": "365",
        "KOHAKU_HUB_REQUIRE_EMAIL_VERIFICATION": "false",
        "KOHAKU_HUB_INVITATION_ONLY": "false",
        "KOHAKU_HUB_ADMIN_ENABLED": "true",
        "KOHAKU_HUB_ADMIN_SECRET_TOKEN": ADMIN_TOKEN,
        "KOHAKU_HUB_FALLBACK_ENABLED": "false",
        "KOHAKU_HUB_DISABLE_DATASET_VIEWER": "true",
        "KOHAKU_HUB_LOG_LEVEL": "ERROR",
        "KOHAKU_HUB_LOG_FORMAT": "terminal",
        "KOHAKU_HUB_LFS_THRESHOLD_BYTES": "1024",
        "KOHAKU_HUB_LFS_MULTIPART_THRESHOLD_BYTES": "1000000",
        "KOHAKU_HUB_LFS_MULTIPART_CHUNK_SIZE_BYTES": "500000",
        "KOHAKU_HUB_LFS_KEEP_VERSIONS": "5",
        "KOHAKU_HUB_LFS_AUTO_GC": "true",
    }
    os.environ.update(env)
    _ENV_APPLIED = True


def clear_backend_modules() -> None:
    """Remove imported backend modules so the test env can be reapplied."""
    to_delete = [name for name in sys.modules if name.startswith("kohakuhub")]
    for name in to_delete:
        del sys.modules[name]


@dataclass(slots=True)
class BackendModules:
    """Container with imported backend modules used by test support."""

    app: object
    db_module: object
    main_module: object
    config_module: object
    files_module: object
    commit_module: object
    auth_routes_module: object
    quota_module: object
    lakefs_module: object
    s3_module: object
    repo_crud_module: object
    repo_info_module: object
    repo_tree_module: object
    likes_module: object
    org_module: object
    settings_module: object
    validation_module: object
    auth_dependencies_module: object
    auth_permissions_module: object
    fallback_cache_module: object
    history_module: object
    branches_module: object
    misc_module: object
    external_tokens_module: object
    admin_users_module: object
    admin_repositories_module: object
    lakefs_rest_client_module: object
    repo_gc_module: object
    git_lfs_module: object


def load_backend_modules(*, force_reload: bool = False) -> BackendModules:
    """Import backend modules after the fast test environment is configured."""
    apply_fast_test_env()

    if force_reload:
        clear_backend_modules()

    main_module = importlib.import_module("kohakuhub.main")
    db_module = importlib.import_module("kohakuhub.db")
    config_module = importlib.import_module("kohakuhub.config")
    files_module = importlib.import_module("kohakuhub.api.files")
    commit_module = importlib.import_module("kohakuhub.api.commit.routers.operations")
    auth_routes_module = importlib.import_module("kohakuhub.auth.routes")
    quota_module = importlib.import_module("kohakuhub.api.quota.util")
    lakefs_module = importlib.import_module("kohakuhub.utils.lakefs")
    s3_module = importlib.import_module("kohakuhub.utils.s3")
    repo_crud_module = importlib.import_module("kohakuhub.api.repo.routers.crud")
    repo_info_module = importlib.import_module("kohakuhub.api.repo.routers.info")
    repo_tree_module = importlib.import_module("kohakuhub.api.repo.routers.tree")
    likes_module = importlib.import_module("kohakuhub.api.likes")
    org_module = importlib.import_module("kohakuhub.api.org.router")
    settings_module = importlib.import_module("kohakuhub.api.settings")
    validation_module = importlib.import_module("kohakuhub.api.validation")
    auth_dependencies_module = importlib.import_module("kohakuhub.auth.dependencies")
    auth_permissions_module = importlib.import_module("kohakuhub.auth.permissions")
    fallback_cache_module = importlib.import_module("kohakuhub.api.fallback.cache")
    history_module = importlib.import_module("kohakuhub.api.commit.routers.history")
    branches_module = importlib.import_module("kohakuhub.api.branches")
    misc_module = importlib.import_module("kohakuhub.api.misc")
    external_tokens_module = importlib.import_module(
        "kohakuhub.api.auth.external_tokens"
    )
    admin_users_module = importlib.import_module("kohakuhub.api.admin.routers.users")
    admin_repositories_module = importlib.import_module(
        "kohakuhub.api.admin.routers.repositories"
    )
    lakefs_rest_client_module = importlib.import_module("kohakuhub.lakefs_rest_client")
    repo_gc_module = importlib.import_module("kohakuhub.api.repo.utils.gc")
    git_lfs_module = importlib.import_module("kohakuhub.api.git.routers.lfs")

    return BackendModules(
        app=main_module.app,
        db_module=db_module,
        main_module=main_module,
        config_module=config_module,
        files_module=files_module,
        commit_module=commit_module,
        auth_routes_module=auth_routes_module,
        quota_module=quota_module,
        lakefs_module=lakefs_module,
        s3_module=s3_module,
        repo_crud_module=repo_crud_module,
        repo_info_module=repo_info_module,
        repo_tree_module=repo_tree_module,
        likes_module=likes_module,
        org_module=org_module,
        settings_module=settings_module,
        validation_module=validation_module,
        auth_dependencies_module=auth_dependencies_module,
        auth_permissions_module=auth_permissions_module,
        fallback_cache_module=fallback_cache_module,
        history_module=history_module,
        branches_module=branches_module,
        misc_module=misc_module,
        external_tokens_module=external_tokens_module,
        admin_users_module=admin_users_module,
        admin_repositories_module=admin_repositories_module,
        lakefs_rest_client_module=lakefs_rest_client_module,
        repo_gc_module=repo_gc_module,
        git_lfs_module=git_lfs_module,
    )
