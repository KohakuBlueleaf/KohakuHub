#!/usr/bin/env python3
"""Run the local-development reset helper directly inside the application."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(1, str(SRC_DIR))

from reset import reset_local_data


async def main() -> int:
    try:
        payload = await reset_local_data()
    except Exception as exc:
        print(f"Local reset failed: {exc}", file=sys.stderr)
        return 1

    print("Local KohakuHub dev data has been cleared.")
    print(json.dumps(payload.get("summary", {}), indent=2, sort_keys=True))

    warnings = payload.get("warnings") or []
    if warnings:
        print("Warnings:", file=sys.stderr)
        for warning in warnings:
            print(f"- {warning}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
