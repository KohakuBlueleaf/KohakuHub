"""Shared helpers for API tests."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable


def encode_ndjson(lines: Iterable[dict]) -> bytes:
    """Encode a sequence of objects into an NDJSON payload."""
    return "\n".join(json.dumps(line, sort_keys=True) for line in lines).encode("utf-8")


def file_op(path: str, content: bytes) -> dict:
    """Build a regular file operation for the commit endpoint."""
    return {
        "key": "file",
        "value": {
            "path": path,
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        },
    }
