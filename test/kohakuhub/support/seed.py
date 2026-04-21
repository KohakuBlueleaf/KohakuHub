"""Baseline data preparation for backend tests."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable

import httpx

from test.kohakuhub.support.bootstrap import DEFAULT_PASSWORD


def _encode_lines(lines: Iterable[dict]) -> bytes:
    return "\n".join(json.dumps(line, sort_keys=True) for line in lines).encode("utf-8")


async def _login(
    client: httpx.AsyncClient, username: str, password: str = DEFAULT_PASSWORD
) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    response.raise_for_status()


async def build_baseline(
    client: httpx.AsyncClient,
    s3_client,
    cfg,
) -> None:
    """Create a deterministic baseline dataset through backend APIs."""
    accounts = (
        ("owner", "owner@example.com"),
        ("member", "member@example.com"),
        ("visitor", "visitor@example.com"),
        ("outsider", "outsider@example.com"),
    )
    for username, email in accounts:
        response = await client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": DEFAULT_PASSWORD},
        )
        response.raise_for_status()

    await _login(client, "owner")
    response = await client.put(
        "/api/users/owner/settings",
        json={
            "email": "owner@example.com",
            "full_name": "Owner Test",
            "bio": "Primary test account.",
            "website": "https://example.com/owner",
            "social_media": {"github": "owner-tests"},
        },
    )
    response.raise_for_status()

    response = await client.post(
        "/org/create",
        json={"name": "acme-labs", "description": "Test organization"},
    )
    response.raise_for_status()

    response = await client.post(
        "/org/acme-labs/members",
        json={"username": "member", "role": "admin"},
    )
    response.raise_for_status()

    response = await client.post(
        "/org/acme-labs/members",
        json={"username": "visitor", "role": "visitor"},
    )
    response.raise_for_status()

    response = await client.post(
        "/api/repos/create",
        json={"type": "model", "name": "demo-model", "private": False},
    )
    response.raise_for_status()

    response = await client.post(
        "/api/repos/create",
        json={
            "type": "dataset",
            "name": "private-dataset",
            "private": True,
            "organization": "acme-labs",
        },
    )
    response.raise_for_status()

    regular_commit = _encode_lines(
        [
            {
                "key": "header",
                "value": {
                    "summary": "Initial regular commit",
                    "description": "Seed regular files.",
                },
            },
            {
                "key": "file",
                "value": {
                    "path": "README.md",
                    "content": base64.b64encode(b"# Demo Model\n\nseed data\n").decode(
                        "ascii"
                    ),
                    "encoding": "base64",
                },
            },
            {
                "key": "file",
                "value": {
                    "path": "config.json",
                    "content": base64.b64encode(b'{"arch":"tiny"}').decode("ascii"),
                    "encoding": "base64",
                },
            },
        ]
    )
    response = await client.post(
        "/api/models/owner/demo-model/commit/main",
        content=regular_commit,
        headers={"Content-Type": "application/x-ndjson"},
    )
    response.raise_for_status()

    lfs_bytes = b"safe tensor payload"
    oid = hashlib.sha256(lfs_bytes).hexdigest()
    lfs_key = f"lfs/{oid[:2]}/{oid[2:4]}/{oid}"
    s3_client.put_object(
        Bucket=cfg.s3.bucket,
        Key=lfs_key,
        Body=lfs_bytes,
        ContentType="application/octet-stream",
    )

    lfs_commit = _encode_lines(
        [
            {
                "key": "header",
                "value": {"summary": "Add weights", "description": "Seed LFS file."},
            },
            {
                "key": "lfsFile",
                "value": {
                    "path": "weights/model.safetensors",
                    "oid": oid,
                    "size": len(lfs_bytes),
                    "algo": "sha256",
                },
            },
        ]
    )
    response = await client.post(
        "/api/models/owner/demo-model/commit/main",
        content=lfs_commit,
        headers={"Content-Type": "application/x-ndjson"},
    )
    response.raise_for_status()

    response = await client.post(
        "/api/datasets/acme-labs/private-dataset/commit/main",
        content=_encode_lines(
            [
                {
                    "key": "header",
                    "value": {
                        "summary": "Seed dataset",
                        "description": "Private dataset seed.",
                    },
                },
                {
                    "key": "file",
                    "value": {
                        "path": "data/train.jsonl",
                        "content": base64.b64encode(b'{"text":"hello"}\n').decode(
                            "ascii"
                        ),
                        "encoding": "base64",
                    },
                },
            ]
        ),
        headers={"Content-Type": "application/x-ndjson"},
    )
    response.raise_for_status()

    response = await client.post("/api/models/owner/demo-model/like")
    response.raise_for_status()
