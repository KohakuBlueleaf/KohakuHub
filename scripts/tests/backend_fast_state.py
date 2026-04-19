"""Cross-platform helpers for preparing and running the fast backend test suite."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from test.kohakuhub.support.bootstrap import ACTIVE_DB_PATH, STATE_DIR
from test.kohakuhub.support.state import create_fast_test_state


async def _prepare_state() -> None:
    state = create_fast_test_state()
    await state.prepare()
    state.restore_active_state()


def _cmd_prepare() -> int:
    asyncio.run(_prepare_state())
    print(f"Prepared fast backend test baseline in {STATE_DIR}")
    return 0


def _cmd_restore() -> int:
    state = create_fast_test_state()
    try:
        state.restore_active_state()
    except FileNotFoundError as exc:
        print(f"{exc}. Run 'prepare' first.", file=sys.stderr)
        return 1
    print(f"Restored active fast backend test state in {ACTIVE_DB_PATH}")
    return 0


def _cmd_clean() -> int:
    shutil.rmtree(STATE_DIR.parent, ignore_errors=True)
    print(f"Removed fast backend test state under {STATE_DIR.parent}")
    return 0


def _cmd_pytest(pytest_args: list[str]) -> int:
    asyncio.run(_prepare_state())

    env = os.environ.copy()
    env["KOHAKUHUB_TEST_PROFILE"] = "fast"

    command = [sys.executable, "-m", "pytest", *(pytest_args or ["test", "-q"])]
    completed = subprocess.run(command, cwd=ROOT_DIR, env=env, check=False)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage KohakuHub fast backend test state."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("prepare", help="Build the fast backend test baseline")
    subparsers.add_parser(
        "restore", help="Restore the active sqlite/service state from the baseline"
    )
    subparsers.add_parser("clean", help="Delete the fast backend test state directory")

    pytest_parser = subparsers.add_parser(
        "pytest", help="Prepare the baseline and run pytest with KOHAKUHUB_TEST_PROFILE=fast"
    )
    pytest_parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to pytest after '--'",
    )

    args = parser.parse_args()

    if args.command == "prepare":
        return _cmd_prepare()
    if args.command == "restore":
        return _cmd_restore()
    if args.command == "clean":
        return _cmd_clean()
    if args.command == "pytest":
        pytest_args = args.pytest_args
        if pytest_args and pytest_args[0] == "--":
            pytest_args = pytest_args[1:]
        return _cmd_pytest(pytest_args)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
