"""Unit tests for configuration loading."""

from __future__ import annotations

import io
from contextlib import contextmanager

import pytest

import kohakuhub.config as hub_config


@contextmanager
def _open_bytes(_path, _mode):
    yield io.BytesIO(b"")


def test_validate_production_safety_and_parser_helpers():
    config = hub_config.Config(
        s3=hub_config.S3Config(),
        lakefs=hub_config.LakeFSConfig(),
        app=hub_config.AppConfig(lfs_keep_versions=1, lfs_threshold_bytes=512),
    )
    warnings = config.validate_production_safety()
    assert any("S3 access_key" in warning for warning in warnings)
    assert any("LakeFS secret_key" in warning for warning in warnings)
    assert any("Session secret" in warning for warning in warnings)
    assert any("Admin secret token" in warning for warning in warnings)
    assert any("keep_versions=1" in warning for warning in warnings)
    assert any("512 bytes" in warning for warning in warnings)

    assert hub_config.update_recursive({"app": {"base_url": "a"}}, {"app": {"api_base": "/api"}}) == {
        "app": {"base_url": "a", "api_base": "/api"}
    }
    assert hub_config._parse_quota(None) is None
    assert hub_config._parse_quota("") is None
    assert hub_config._parse_quota("None") is None
    assert hub_config._parse_quota("unlimited") is None
    assert hub_config._parse_quota("123") == 123

    assert hub_config._parse_fallback_sources(None) == []
    assert hub_config._parse_fallback_sources("") == []
    assert hub_config._parse_fallback_sources('{"bad": true}') == []
    assert hub_config._parse_fallback_sources("not-json") == []
    assert hub_config._parse_fallback_sources('[{"url": "https://hf.co"}]') == [
        {"url": "https://hf.co"}
    ]


def test_load_config_merges_file_and_environment(monkeypatch):
    hub_config.load_config.cache_clear()

    file_config = {
        "s3": {"endpoint": "http://file-s3", "bucket": "file-bucket"},
        "lakefs": {"endpoint": "http://file-lakefs"},
        "app": {"base_url": "http://file-app", "lfs_threshold_bytes": 2048},
    }

    monkeypatch.setattr(hub_config.os.path, "exists", lambda path: True)
    monkeypatch.setattr("builtins.open", _open_bytes)
    monkeypatch.setattr(hub_config.tomllib, "load", lambda fh: file_config)
    monkeypatch.setenv("HUB_CONFIG", "/tmp/from-env.toml")
    monkeypatch.setenv("KOHAKU_HUB_S3_PUBLIC_ENDPOINT", "http://env-s3-public")
    monkeypatch.setenv("KOHAKU_HUB_S3_ENDPOINT", "http://env-s3")
    monkeypatch.setenv("KOHAKU_HUB_S3_ACCESS_KEY", "env-ak")
    monkeypatch.setenv("KOHAKU_HUB_S3_SECRET_KEY", "env-sk")
    monkeypatch.setenv("KOHAKU_HUB_S3_BUCKET", "env-bucket")
    monkeypatch.setenv("KOHAKU_HUB_S3_REGION", "auto")
    monkeypatch.setenv("KOHAKU_HUB_S3_SIGNATURE_VERSION", "s3v4")
    monkeypatch.setenv("KOHAKU_HUB_LAKEFS_ENDPOINT", "http://env-lakefs")
    monkeypatch.setenv("KOHAKU_HUB_LAKEFS_ACCESS_KEY", "lakefs-ak")
    monkeypatch.setenv("KOHAKU_HUB_LAKEFS_SECRET_KEY", "lakefs-sk")
    monkeypatch.setenv("KOHAKU_HUB_LAKEFS_REPO_NAMESPACE", "kh")
    monkeypatch.setenv("KOHAKU_HUB_SMTP_ENABLED", "true")
    monkeypatch.setenv("KOHAKU_HUB_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("KOHAKU_HUB_SMTP_PORT", "2525")
    monkeypatch.setenv("KOHAKU_HUB_SMTP_USERNAME", "mailer")
    monkeypatch.setenv("KOHAKU_HUB_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("KOHAKU_HUB_SMTP_FROM", "noreply@example.com")
    monkeypatch.setenv("KOHAKU_HUB_SMTP_TLS", "false")
    monkeypatch.setenv("KOHAKU_HUB_REQUIRE_EMAIL_VERIFICATION", "true")
    monkeypatch.setenv("KOHAKU_HUB_INVITATION_ONLY", "true")
    monkeypatch.setenv("KOHAKU_HUB_SESSION_SECRET", "session-secret")
    monkeypatch.setenv("KOHAKU_HUB_SESSION_EXPIRE_HOURS", "12")
    monkeypatch.setenv("KOHAKU_HUB_TOKEN_EXPIRE_DAYS", "30")
    monkeypatch.setenv("KOHAKU_HUB_ADMIN_ENABLED", "false")
    monkeypatch.setenv("KOHAKU_HUB_ADMIN_SECRET_TOKEN", "admin-secret")
    monkeypatch.setenv("KOHAKU_HUB_DEFAULT_USER_PRIVATE_QUOTA_BYTES", "100")
    monkeypatch.setenv("KOHAKU_HUB_DEFAULT_USER_PUBLIC_QUOTA_BYTES", "unlimited")
    monkeypatch.setenv("KOHAKU_HUB_DEFAULT_ORG_PRIVATE_QUOTA_BYTES", "200")
    monkeypatch.setenv("KOHAKU_HUB_DEFAULT_ORG_PUBLIC_QUOTA_BYTES", "none")
    monkeypatch.setenv("KOHAKU_HUB_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("KOHAKU_HUB_FALLBACK_CACHE_TTL", "10")
    monkeypatch.setenv("KOHAKU_HUB_FALLBACK_TIMEOUT", "20")
    monkeypatch.setenv("KOHAKU_HUB_FALLBACK_MAX_CONCURRENT", "30")
    monkeypatch.setenv("KOHAKU_HUB_FALLBACK_REQUIRE_AUTH", "true")
    monkeypatch.setenv(
        "KOHAKU_HUB_FALLBACK_SOURCES",
        '[{"url": "https://hf.co", "priority": 1}]',
    )
    monkeypatch.setenv("KOHAKU_HUB_BASE_URL", "http://env-app")
    monkeypatch.setenv("KOHAKU_HUB_INTERNAL_BASE_URL", "http://internal-app")
    monkeypatch.setenv("KOHAKU_HUB_API_BASE", "/api/v2")
    monkeypatch.setenv("KOHAKU_HUB_DISABLE_DATASET_VIEWER", "true")
    monkeypatch.setenv("KOHAKU_HUB_DB_BACKEND", "postgres")
    monkeypatch.setenv("KOHAKU_HUB_DATABASE_URL", "postgres://db")
    monkeypatch.setenv("KOHAKU_HUB_DATABASE_KEY", "key")
    monkeypatch.setenv("KOHAKU_HUB_LFS_THRESHOLD_BYTES", "4096")
    monkeypatch.setenv("KOHAKU_HUB_LFS_MULTIPART_THRESHOLD_BYTES", "8192")
    monkeypatch.setenv("KOHAKU_HUB_LFS_MULTIPART_CHUNK_SIZE_BYTES", "1024")
    monkeypatch.setenv("KOHAKU_HUB_LFS_KEEP_VERSIONS", "6")
    monkeypatch.setenv("KOHAKU_HUB_LFS_AUTO_GC", "true")
    monkeypatch.setenv("KOHAKU_HUB_SITE_NAME", "Env Hub")
    monkeypatch.setenv("KOHAKU_HUB_DEBUG_LOG_PAYLOADS", "true")
    monkeypatch.setenv("KOHAKU_HUB_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("KOHAKU_HUB_LOG_FORMAT", "terminal")
    monkeypatch.setenv("KOHAKU_HUB_LOG_DIR", "/tmp/logs")

    cfg = hub_config.load_config()
    assert cfg.s3.public_endpoint == "http://env-s3-public"
    assert cfg.s3.endpoint == "http://env-s3"
    assert cfg.s3.access_key == "env-ak"
    assert cfg.s3.secret_key == "env-sk"
    assert cfg.s3.bucket == "env-bucket"
    assert cfg.s3.region == "auto"
    assert cfg.s3.signature_version == "s3v4"
    assert cfg.lakefs.endpoint == "http://env-lakefs"
    assert cfg.lakefs.repo_namespace == "kh"
    assert cfg.smtp.enabled is True
    assert cfg.smtp.host == "smtp.example.com"
    assert cfg.smtp.port == 2525
    assert cfg.smtp.use_tls is False
    assert cfg.auth.require_email_verification is True
    assert cfg.auth.invitation_only is True
    assert cfg.auth.session_secret == "session-secret"
    assert cfg.admin.enabled is False
    assert cfg.admin.secret_token == "admin-secret"
    assert cfg.quota.default_user_private_quota_bytes == 100
    assert cfg.quota.default_user_public_quota_bytes is None
    assert cfg.quota.default_org_private_quota_bytes == 200
    assert cfg.quota.default_org_public_quota_bytes is None
    assert cfg.fallback.enabled is False
    assert cfg.fallback.cache_ttl_seconds == 10
    assert cfg.fallback.timeout_seconds == 20
    assert cfg.fallback.max_concurrent_requests == 30
    assert cfg.fallback.require_auth is True
    assert cfg.fallback.sources == [{"url": "https://hf.co", "priority": 1}]
    assert cfg.app.base_url == "http://env-app"
    assert cfg.app.internal_base_url == "http://internal-app"
    assert cfg.app.api_base == "/api/v2"
    assert cfg.app.disable_dataset_viewer is True
    assert cfg.app.db_backend == "postgres"
    assert cfg.app.database_url == "postgres://db"
    assert cfg.app.database_key == "key"
    assert cfg.app.lfs_threshold_bytes == 4096
    assert cfg.app.lfs_multipart_threshold_bytes == 8192
    assert cfg.app.lfs_multipart_chunk_size_bytes == 1024
    assert cfg.app.lfs_keep_versions == 6
    assert cfg.app.lfs_auto_gc is True
    assert cfg.app.site_name == "Env Hub"
    assert cfg.app.debug_log_payloads is True
    assert cfg.app.log_level == "DEBUG"
    assert cfg.app.log_format == "terminal"
    assert cfg.app.log_dir == "/tmp/logs"

    hub_config.load_config.cache_clear()


def test_load_config_uses_defaults_when_file_is_missing(monkeypatch):
    hub_config.load_config.cache_clear()
    monkeypatch.setattr(hub_config.os.path, "exists", lambda path: False)
    monkeypatch.delenv("HUB_CONFIG", raising=False)
    for key in list(hub_config.os.environ):
        if key.startswith("KOHAKU_HUB_"):
            monkeypatch.delenv(key, raising=False)

    cfg = hub_config.load_config(path="/tmp/missing.toml")
    assert cfg.s3.endpoint == "http://localhost:9000"
    assert cfg.lakefs.endpoint == "http://localhost:8000"
    assert cfg.app.base_url == "http://localhost:48888"
    hub_config.load_config.cache_clear()
