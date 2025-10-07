#!/usr/bin/env python3
import os
import sys
import time
import httpx
import subprocess
from pathlib import Path


CRED_FILE = Path("/hub-api-creds/credentials.env")
LAKEFS_ENDPOINT = os.getenv("KOHAKU_HUB_LAKEFS_ENDPOINT", "http://lakefs:28000")
ADMIN_USER = os.getenv("LAKEFS_ADMIN_USER", "admin")


def wait_for_lakefs():
    url = f"{LAKEFS_ENDPOINT}/_health"
    print(f"[startup] Waiting for lakeFS at {url}...")
    while True:
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code == 200:
                print("[startup] lakeFS is up.")
                return
        except Exception:
            pass
        time.sleep(2)


def is_initialized(client: httpx.Client):
    """Call GET /setup_lakefs to see if already setup."""
    url = f"{LAKEFS_ENDPOINT}/api/v1/setup_lakefs"
    try:
        r = client.get(url, timeout=5)
    except Exception as e:
        print(f"[startup] error calling GET {url}: {e}")
        return False
    print(f"[startup] GET {url} responded {r.status_code} {r.text}")
    if r.status_code == 200:
        try:
            j = r.json()
            if j.get("initialized", False) is True:
                return True
        except Exception:
            pass
    return False


def do_setup(client: httpx.Client):
    url = f"{LAKEFS_ENDPOINT}/api/v1/setup_lakefs"
    payload = {
        "username": ADMIN_USER,
    }
    r = client.post(
        url, json=payload, headers={"accept": "application/json"}, timeout=10
    )
    print(f"[startup] POST {url} responded {r.status_code} {r.text}")
    body = r.json()
    if r.status_code in (200, 201):
        return body["access_key_id"], body["secret_access_key"]
    else:
        print("[startup] Setup failed:", r.status_code, r.text)
        sys.exit(1)


def write_credentials(access_key, secret_key):
    CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CRED_FILE, "w") as f:
        f.write(f"KOHAKU_HUB_LAKEFS_ACCESS_KEY={access_key}\n")
        f.write(f"KOHAKU_HUB_LAKEFS_SECRET_KEY={secret_key}\n")
    print(f"[startup] Saved credentials to {CRED_FILE}")


def load_credentials():
    if "KOHAKU_HUB_LAKEFS_ACCESS_KEY" in os.environ:
        return
    with open(CRED_FILE) as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                os.environ[k] = v
    print(f"[startup] Loaded credentials from {CRED_FILE}")


def init_database():
    """Initialize database and run migrations."""
    print("[startup] Initializing database...")
    try:
        from kohakuhub.db import init_db
        init_db()
        print("[startup] Database initialized successfully")
    except Exception as e:
        print(f"[startup] Database initialization failed: {e}")
        # Don't exit - let the app try to continue


def main():
    wait_for_lakefs()

    if CRED_FILE.exists() or (
        "KOHAKU_HUB_LAKEFS_ACCESS_KEY" in os.environ
        and "KOHAKU_HUB_LAKEFS_SECRET_KEY" in os.environ
    ):
        load_credentials()
    else:
        with httpx.Client() as client:
            if is_initialized(client):
                print(
                    "[startup] lakeFS is already initialized (by GET). But no local credentials file -> cannot proceed."
                )
                sys.exit(1)
            access_key = secret_key = "123"
            try:
                access_key, secret_key = do_setup(client)
                write_credentials(access_key, secret_key)
            except Exception as e:
                print(f"[startup] Setup failed: {e}")
                pass
            os.environ["KOHAKU_HUB_LAKEFS_ACCESS_KEY"] = access_key
            os.environ["KOHAKU_HUB_LAKEFS_SECRET_KEY"] = secret_key

    # Initialize database tables (will create missing tables/columns)
    init_database()

    print("[startup] Starting API server...")
    subprocess.run(
        ["uvicorn", "kohakuhub.main:app", "--host", "0.0.0.0", "--port", "48888"],
        check=True,
        env=os.environ,
    )


if __name__ == "__main__":
    main()
