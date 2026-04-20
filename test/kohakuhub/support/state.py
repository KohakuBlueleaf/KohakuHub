"""Persistent state manager for the fast backend test suite."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from test.kohakuhub.support.bootstrap import (
    ACTIVE_DB_PATH,
    BASELINE_DB_PATH,
    SNAPSHOT_PATH,
    STATE_VERSION,
    apply_fast_test_env,
    ensure_paths,
    load_backend_modules,
)
from test.kohakuhub.support.fakes import FakeLakeFSClient, FakeS3Service
from test.kohakuhub.support.seed import build_baseline


@dataclass(slots=True)
class FastTestState:
    """Runtime state used by the fast backend test suite."""

    modules: object
    fake_s3: FakeS3Service
    fake_lakefs: FakeLakeFSClient

    def install_fakes(self) -> None:
        """Patch imported backend modules to use fake storage services."""
        cfg = self.modules.config_module.cfg
        self.fake_s3.init_storage(cfg.s3.bucket)

        async def fake_generate_download_presigned_url(
            bucket: str,
            key: str,
            expires_in: int = 3600,
            filename: str | None = None,
        ) -> str:
            return f"https://fake-s3.local/{bucket}/{key}?download=1"

        async def fake_generate_upload_presigned_url(
            bucket: str,
            key: str,
            expires_in: int = 3600,
            content_type: str | None = None,
            checksum_sha256: str | None = None,
        ) -> dict:
            return {
                "url": f"https://fake-s3.local/{bucket}/{key}?upload=1",
                "headers": {},
                "expires_at": "2099-01-01T00:00:00.000000Z",
            }

        async def fake_generate_multipart_upload_urls(
            bucket: str,
            key: str,
            part_count: int,
            expires_in: int = 3600,
            content_type: str | None = None,
        ) -> dict:
            return {
                "upload_id": "fake-upload-id",
                "expires_at": "2099-01-01T00:00:00.000000Z",
                "part_urls": [
                    {
                        "part_number": index + 1,
                        "url": f"https://fake-s3.local/{bucket}/{key}?part={index + 1}",
                    }
                    for index in range(part_count)
                ],
            }

        async def fake_complete_multipart_upload(
            bucket: str,
            key: str,
            upload_id: str,
            parts: list[dict],
        ) -> dict:
            return {"bucket": bucket, "key": key, "upload_id": upload_id, "parts": parts}

        async def fake_abort_multipart_upload(bucket: str, key: str, upload_id: str) -> None:
            return None

        async def fake_object_exists(bucket: str, key: str) -> bool:
            return self.fake_s3.object_exists(bucket, key)

        async def fake_get_object_metadata(bucket: str, key: str) -> dict:
            return self.fake_s3.get_object_metadata(bucket, key)

        async def fake_delete_objects_with_prefix(bucket: str, prefix: str) -> int:
            return self.fake_s3.delete_prefix(bucket, prefix)

        async def fake_copy_s3_folder(
            bucket: str,
            from_prefix: str,
            to_prefix: str,
            exclude_prefix: str | None = None,
        ) -> int:
            return self.fake_s3.copy_prefix(bucket, from_prefix, to_prefix, exclude_prefix)

        def fake_init_storage() -> None:
            self.fake_s3.init_storage(cfg.s3.bucket)

        def fake_get_s3_client():
            return self.fake_s3.get_client()

        def fake_get_lakefs_client():
            return self.fake_lakefs

        def patch_attr(module, name, value) -> None:
            if module is not None:
                setattr(module, name, value)

        patch_attr(self.modules.main_module, "init_storage", fake_init_storage)
        patch_attr(self.modules.s3_module, "init_storage", fake_init_storage)
        patch_attr(self.modules.s3_module, "get_s3_client", fake_get_s3_client)
        patch_attr(
            self.modules.s3_module,
            "generate_download_presigned_url",
            fake_generate_download_presigned_url,
        )
        patch_attr(
            self.modules.s3_module,
            "generate_upload_presigned_url",
            fake_generate_upload_presigned_url,
        )
        patch_attr(
            self.modules.s3_module,
            "generate_multipart_upload_urls",
            fake_generate_multipart_upload_urls,
        )
        patch_attr(
            self.modules.s3_module,
            "complete_multipart_upload",
            fake_complete_multipart_upload,
        )
        patch_attr(
            self.modules.s3_module,
            "abort_multipart_upload",
            fake_abort_multipart_upload,
        )
        patch_attr(self.modules.s3_module, "object_exists", fake_object_exists)
        patch_attr(self.modules.s3_module, "get_object_metadata", fake_get_object_metadata)
        patch_attr(
            self.modules.s3_module,
            "delete_objects_with_prefix",
            fake_delete_objects_with_prefix,
        )
        patch_attr(self.modules.s3_module, "copy_s3_folder", fake_copy_s3_folder)

        for module in (
            self.modules.files_module,
            self.modules.repo_crud_module,
            self.modules.commit_module,
            self.modules.repo_info_module,
            self.modules.repo_tree_module,
            self.modules.quota_module,
            self.modules.history_module,
            self.modules.branches_module,
            self.modules.repo_gc_module,
            self.modules.git_lfs_module,
            self.modules.admin_repositories_module,
        ):
            patch_attr(module, "get_lakefs_client", fake_get_lakefs_client)

        patch_attr(
            self.modules.history_module,
            "get_lakefs_rest_client",
            fake_get_lakefs_client,
        )
        patch_attr(self.modules.lakefs_module, "get_lakefs_client", fake_get_lakefs_client)
        patch_attr(
            self.modules.lakefs_rest_client_module,
            "get_lakefs_rest_client",
            fake_get_lakefs_client,
        )

        patch_attr(
            self.modules.files_module,
            "generate_download_presigned_url",
            fake_generate_download_presigned_url,
        )
        patch_attr(self.modules.commit_module, "object_exists", fake_object_exists)
        patch_attr(
            self.modules.commit_module,
            "get_object_metadata",
            fake_get_object_metadata,
        )
        patch_attr(
            self.modules.repo_crud_module,
            "delete_objects_with_prefix",
            fake_delete_objects_with_prefix,
        )
        patch_attr(self.modules.repo_crud_module, "copy_s3_folder", fake_copy_s3_folder)
        patch_attr(self.modules.repo_gc_module, "get_s3_client", fake_get_s3_client)
        patch_attr(
            self.modules.repo_gc_module,
            "delete_objects_with_prefix",
            fake_delete_objects_with_prefix,
        )
        patch_attr(self.modules.repo_gc_module, "object_exists", fake_object_exists)
        patch_attr(
            self.modules.git_lfs_module,
            "generate_upload_presigned_url",
            fake_generate_upload_presigned_url,
        )
        patch_attr(
            self.modules.git_lfs_module,
            "generate_download_presigned_url",
            fake_generate_download_presigned_url,
        )
        patch_attr(
            self.modules.git_lfs_module,
            "generate_multipart_upload_urls",
            fake_generate_multipart_upload_urls,
        )
        patch_attr(
            self.modules.git_lfs_module,
            "complete_multipart_upload",
            fake_complete_multipart_upload,
        )
        patch_attr(
            self.modules.git_lfs_module,
            "abort_multipart_upload",
            fake_abort_multipart_upload,
        )
        patch_attr(
            self.modules.git_lfs_module,
            "get_object_metadata",
            fake_get_object_metadata,
        )
        patch_attr(self.modules.git_lfs_module, "object_exists", fake_object_exists)

        self.modules.fallback_cache_module.get_cache().clear()

    def _close_db(self) -> None:
        db = self.modules.db_module.db
        if not db.is_closed():
            db.close()

    def _unlink_with_retry(self, path: Path) -> None:
        """Remove state files after closing SQLite handles, with a short Windows-safe retry."""
        for attempt in range(5):
            try:
                path.unlink(missing_ok=True)
                return
            except PermissionError:
                self._close_db()
                if attempt == 4:
                    raise
                time.sleep(0.1)

    def restore_active_state(self) -> None:
        """Restore DB and fake services from the prepared baseline."""
        self._close_db()
        if not BASELINE_DB_PATH.exists():
            raise FileNotFoundError("Baseline DB is missing. Run prepare first.")
        shutil.copyfile(BASELINE_DB_PATH, ACTIVE_DB_PATH)
        snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        self.fake_s3.load_snapshot(snapshot["s3"])
        self.fake_lakefs.load_snapshot(snapshot["lakefs"])
        self.modules.fallback_cache_module.get_cache().clear()

    async def prepare(self) -> None:
        """Build the baseline state if missing, then restore it for use."""
        ensure_paths()
        needs_prepare = not BASELINE_DB_PATH.exists() or not SNAPSHOT_PATH.exists()
        if not needs_prepare:
            snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
            needs_prepare = snapshot.get("version") != STATE_VERSION

        if needs_prepare:
            self._close_db()
            self._unlink_with_retry(ACTIVE_DB_PATH)
            self._unlink_with_retry(BASELINE_DB_PATH)
            SNAPSHOT_PATH.unlink(missing_ok=True)
            self.modules.db_module.init_db()
            transport = httpx.ASGITransport(app=self.modules.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
            ) as client:
                await build_baseline(client, self.fake_s3, self.modules.config_module.cfg)

            self._close_db()
            shutil.copyfile(ACTIVE_DB_PATH, BASELINE_DB_PATH)
            snapshot = {
                "version": STATE_VERSION,
                "s3": self.fake_s3.export_snapshot(),
                "lakefs": self.fake_lakefs.export_snapshot(),
            }
            SNAPSHOT_PATH.write_text(
                json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        self.restore_active_state()


def create_fast_test_state() -> FastTestState:
    """Create the in-process state manager used by the fast suite."""
    apply_fast_test_env()
    modules = load_backend_modules()
    fake_s3 = FakeS3Service()
    fake_lakefs = FakeLakeFSClient(
        s3_service=fake_s3,
        default_bucket=modules.config_module.cfg.s3.bucket,
    )
    state = FastTestState(modules=modules, fake_s3=fake_s3, fake_lakefs=fake_lakefs)
    state.install_fakes()
    return state
