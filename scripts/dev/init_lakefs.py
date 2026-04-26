#!/usr/bin/env python3
"""Initialize local LakeFS and persist credentials for the Python backend."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx


def wait_for_lakefs(endpoint: str, timeout_seconds: int) -> None:
    health_url = f"{endpoint.rstrip('/')}/_health"
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        try:
            response = httpx.get(health_url, timeout=2.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)

    raise TimeoutError(f"Timed out waiting for LakeFS: {health_url}")


def read_credentials(path: Path) -> dict[str, str]:
    credentials: dict[str, str] = {}
    if not path.exists():
        return credentials

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        credentials[key] = value
    return credentials


def write_credentials(path: Path, access_key: str, secret_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"KOHAKU_HUB_LAKEFS_ACCESS_KEY={access_key}",
                f"KOHAKU_HUB_LAKEFS_SECRET_KEY={secret_key}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def is_initialized(client: httpx.Client, endpoint: str) -> bool:
    response = client.get(f"{endpoint.rstrip('/')}/api/v1/setup_lakefs", timeout=5.0)
    if response.status_code != 200:
        return False

    try:
        return bool(response.json().get("initialized"))
    except Exception:
        return False


def initialize_lakefs(
    endpoint: str,
    credentials_file: Path,
    admin_user: str,
    timeout_seconds: int,
) -> int:
    wait_for_lakefs(endpoint, timeout_seconds)

    existing = read_credentials(credentials_file)
    if existing.get("KOHAKU_HUB_LAKEFS_ACCESS_KEY") and existing.get(
        "KOHAKU_HUB_LAKEFS_SECRET_KEY"
    ):
        print(f"LakeFS credentials already exist: {credentials_file}")
        return 0

    with httpx.Client() as client:
        if is_initialized(client, endpoint):
            print(
                "LakeFS is already initialized but no local credentials file was found.\n"
                f"Expected: {credentials_file}\n"
                "Either restore that file or remove hub-meta/dev/lakefs-data to re-initialize.",
                file=sys.stderr,
            )
            return 1

        response = client.post(
            f"{endpoint.rstrip('/')}/api/v1/setup_lakefs",
            json={"username": admin_user},
            headers={"accept": "application/json"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()

    access_key = payload["access_key_id"]
    secret_key = payload["secret_access_key"]
    write_credentials(credentials_file, access_key, secret_key)
    print(f"Initialized LakeFS and wrote credentials to {credentials_file}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize local LakeFS and persist credentials."
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("KOHAKU_HUB_LAKEFS_ENDPOINT", "http://127.0.0.1:28000"),
        help="LakeFS endpoint",
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        default=Path("hub-meta/dev/lakefs/credentials.env"),
        help="Path to the generated credentials.env",
    )
    parser.add_argument(
        "--admin-user",
        default=os.environ.get("LAKEFS_ADMIN_USER", "admin"),
        help="LakeFS bootstrap admin username",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=60,
        help="How long to wait for LakeFS to become healthy",
    )
    args = parser.parse_args()

    try:
        return initialize_lakefs(
            endpoint=args.endpoint,
            credentials_file=args.credentials_file,
            admin_user=args.admin_user,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        print(f"Failed to initialize LakeFS: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
