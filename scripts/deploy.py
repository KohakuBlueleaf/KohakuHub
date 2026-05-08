#!/usr/bin/env python3
"""Deploy KohakuHub using Docker Compose.

This script:
1. Installs frontend workspace dependencies with pnpm
2. Builds frontend bundles from the repo root
3. Runs docker compose up -d --build
"""

import platform
import subprocess
import sys
from pathlib import Path


PNPM_CMD = "pnpm.cmd" if platform.system() == "Windows" else "pnpm"


def run_command(cmd: list[str], cwd: Path | None = None, description: str = ""):
    """Run a command and exit if it fails."""
    if description:
        print(f"\n{'=' * 60}")
        print(f"  {description}")
        print(f"{'=' * 60}")

    print(f"Running: {' '.join(cmd)}")
    if cwd:
        print(f"  in: {cwd}")

    # Use shell=True on Windows (npm is npm.cmd), False on Unix
    use_shell = platform.system() == "Windows"

    result = subprocess.run(cmd, cwd=cwd, shell=use_shell)

    if result.returncode != 0:
        print(f"\n❌ Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    print(f"✓ {description or 'Command'} completed")


def main():
    """Main function."""
    root_dir = Path(__file__).parent.parent

    print("\nKohakuHub Deployment")
    print("=" * 60)

    # Step 1: Install frontend workspace dependencies
    run_command([PNPM_CMD, "install"], cwd=root_dir, description="Installing frontend dependencies")

    # Step 2: Build frontend bundles
    run_command([PNPM_CMD, "run", "build"], cwd=root_dir, description="Building frontend bundles")

    # Step 3: Docker Compose up
    run_command(
        ["docker", "compose", "up", "-d", "--build"],
        cwd=root_dir,
        description="Starting Docker containers",
    )

    print("\n" + "=" * 60)
    print("[OK] KohakuHub deployed successfully!")
    print("=" * 60)
    print("\nAccess Points:")
    print("   Main UI:    http://localhost:28080")
    print("   Admin UI:   http://localhost:28080/admin")
    print("   API:        http://localhost:28080/api")
    print("   API Docs:   http://localhost:28080/docs")
    print("\nTip: View logs with: docker compose logs -f")


if __name__ == "__main__":
    main()
