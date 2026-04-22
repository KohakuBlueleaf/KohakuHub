#!/usr/bin/env python3
"""Verify local demo seed fixtures remain navigable and downloadable."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kohakuhub.config import cfg
from kohakuhub.main import app
from kohakuhub.utils.s3 import init_storage

MANIFEST_PATH = ROOT_DIR / "hub-meta" / "dev" / "demo-seed-manifest.json"
EXPECTED_SEED_VERSION = "local-dev-demo-v3"
INTERNAL_BASE_URL = (
    getattr(cfg.app, "internal_base_url", None)
    or cfg.app.base_url
    or "http://127.0.0.1:48888"
)


class VerifyError(RuntimeError):
    """Raised when local demo seed verification fails."""


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise VerifyError(
            "Missing demo seed manifest. Run `make seed-demo` before verification."
        )

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("seed_version") != EXPECTED_SEED_VERSION:
        raise VerifyError(
            f"Expected seed version {EXPECTED_SEED_VERSION}, "
            f"got {manifest.get('seed_version')!r}."
        )
    return manifest


def require_path(entries: list[dict], expected_path: str) -> dict:
    for entry in entries:
        if entry.get("path") == expected_path:
            return entry
    available = ", ".join(sorted(entry.get("path", "<missing>") for entry in entries))
    raise VerifyError(
        f"Expected tree entry {expected_path!r} but only found: {available}"
    )


async def get_json(client: httpx.AsyncClient, path: str):
    response = await client.get(path)
    if response.status_code != 200:
        raise VerifyError(f"GET {path} returned {response.status_code}: {response.text}")
    return response.json()


async def head_ok(client: httpx.AsyncClient, path: str) -> httpx.Response:
    response = await client.head(path)
    if response.status_code != 200:
        raise VerifyError(
            f"HEAD {path} returned {response.status_code}: {response.text}"
        )
    return response


async def resolve_json_via_download(
    client: httpx.AsyncClient,
    path: str,
) -> dict:
    response = await client.get(path, follow_redirects=False)
    if response.status_code != 302:
        raise VerifyError(f"GET {path} returned {response.status_code}: {response.text}")

    location = response.headers.get("location")
    if not location:
        raise VerifyError(f"GET {path} did not return a download location.")

    async with httpx.AsyncClient(timeout=60.0) as download_client:
        download_response = await download_client.get(location)

    if download_response.status_code != 200:
        raise VerifyError(
            f"Downloading {path} from presigned URL returned "
            f"{download_response.status_code}."
        )

    try:
        return download_response.json()
    except json.JSONDecodeError as exc:
        raise VerifyError(f"Downloaded {path} was not valid JSON: {exc}") from exc


async def verify_seed_data() -> dict:
    manifest = load_manifest()
    init_storage()
    transport = httpx.ASGITransport(app=app)

    summary = {
        "seed_version": manifest["seed_version"],
        "verified_checks": [],
    }

    async with httpx.AsyncClient(
        transport=transport,
        base_url=INTERNAL_BASE_URL,
        follow_redirects=False,
        timeout=60.0,
    ) as client:
        table_root = await get_json(
            client, "/api/datasets/open-media-lab/table-scan-fixtures/tree/main"
        )
        require_path(table_root, "metadata")
        summary["verified_checks"].append("table-scan-fixtures root tree")

        metadata_entries = await get_json(
            client,
            "/api/datasets/open-media-lab/table-scan-fixtures/tree/main/metadata",
        )
        require_path(metadata_entries, "metadata/features.json")
        summary["verified_checks"].append("table-scan-fixtures metadata tree")

        metadata_head = await head_ok(
            client,
            "/datasets/open-media-lab/table-scan-fixtures/resolve/main/metadata/features.json",
        )
        metadata_size = int(metadata_head.headers.get("content-length") or "0")
        if metadata_size <= 0:
            raise VerifyError(
                "metadata/features.json returned a non-positive content length."
            )
        summary["verified_checks"].append("table-scan-fixtures metadata HEAD")

        metadata_json = await resolve_json_via_download(
            client,
            "/datasets/open-media-lab/table-scan-fixtures/resolve/main/metadata/features.json",
        )
        if metadata_json != {"id": "string", "label": "string"}:
            raise VerifyError(
                "metadata/features.json returned unexpected content: "
                f"{metadata_json!r}"
            )
        summary["verified_checks"].append("table-scan-fixtures metadata download")

        hierarchy_root = await get_json(
            client,
            "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog",
        )
        require_path(hierarchy_root, "catalog/section-01")
        if any(
            entry.get("path", "").startswith("catalog/catalog/")
            for entry in hierarchy_root
        ):
            raise VerifyError(
                "Hierarchy tree unexpectedly contains duplicated catalog prefixes."
            )
        summary["verified_checks"].append("hierarchy-crawl root tree")

        section_entries = await get_json(
            client,
            "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog/section-01",
        )
        require_path(section_entries, "catalog/section-01/tier-01")
        summary["verified_checks"].append("hierarchy-crawl section tree")

        deep_json = await resolve_json_via_download(
            client,
            "/datasets/open-media-lab/hierarchy-crawl-fixtures/resolve/main/"
            "catalog/section-01/tier-01/branch-01/node-01-01-01/entry-01-01-01.json",
        )
        deep_json_path = (
            "catalog/section-01/tier-01/branch-01/"
            "node-01-01-01/entry-01-01-01.json"
        )
        expected_deep_json = {
            "checksum": hashlib.sha256(deep_json_path.encode("utf-8")).hexdigest(),
            "fixture": "hierarchy-crawl",
            "leaf": 1,
            "section": 1,
            "shard": 1,
        }
        if deep_json != expected_deep_json:
            raise VerifyError(
                "Deep hierarchy JSON returned unexpected content: "
                f"{deep_json!r}"
            )
        summary["verified_checks"].append("hierarchy-crawl deep JSON download")

        expected_fallback_sources = {
            (source["url"].rstrip("/"), source["name"])
            for source in manifest.get("fallback_sources", [])
            if source.get("namespace", "") == ""
        }
        if expected_fallback_sources:
            available_sources = await get_json(
                client, "/api/fallback-sources/available"
            )
            available = {
                (entry.get("url", "").rstrip("/"), entry.get("name"))
                for entry in available_sources
            }
            missing = expected_fallback_sources - available
            if missing:
                raise VerifyError(
                    "Missing seeded fallback sources: "
                    + ", ".join(sorted(f"{name} ({url})" for url, name in missing))
                )
            summary["verified_checks"].append("fallback sources available")

    return summary


def main() -> int:
    try:
        summary = asyncio.run(verify_seed_data())
    except VerifyError as exc:
        print(f"Seed verification failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
