#!/usr/bin/env python3
"""
Configuration Migration Script for KohakuHub

This script migrates existing docker-compose.yml and config.toml to the latest format.
It reads existing values and only prompts for new fields that don't exist.

Usage:
    python scripts/migrate_config.py                    # Interactive migration
    python scripts/migrate_config.py --auto             # Auto-migrate with defaults for new fields
    python scripts/migrate_config.py --backup-only      # Create backups without migration
"""

import argparse
import os
import re
import secrets
import shutil
import sys
import tomllib
from datetime import datetime
from pathlib import Path


def generate_secret(length: int = 32) -> str:
    """Generate a random URL-safe secret key."""
    return secrets.token_urlsafe(length)


def backup_file(filepath: Path) -> Path | None:
    """Create a timestamped backup of a file.

    Args:
        filepath: Path to file to backup

    Returns:
        Path to backup file
    """
    if not filepath.exists():
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = filepath.parent / f"{filepath.name}.backup.{timestamp}"
    shutil.copy2(filepath, backup_path)
    print(f"✓ Created backup: {backup_path}")
    return backup_path


def read_existing_docker_compose(filepath: Path) -> dict:
    """Parse existing docker-compose.yml and extract environment variables.

    Returns:
        Dict of environment variable name -> value
    """
    if not filepath.exists():
        return {}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract environment variables from hub-api service
        env_vars = {}
        in_hub_api = False
        in_environment = False

        for line in content.split("\n"):
            stripped = line.strip()

            # Detect hub-api service
            if stripped.startswith("hub-api:"):
                in_hub_api = True
                in_environment = False
            elif in_hub_api and stripped.startswith("environment:"):
                in_environment = True
            elif in_hub_api and in_environment:
                # Check if we've left the environment section
                if (
                    stripped
                    and not stripped.startswith("-")
                    and not stripped.startswith("#")
                ):
                    # New section started
                    in_environment = False
                    in_hub_api = False
                elif stripped.startswith("- KOHAKU_HUB_"):
                    # Parse environment variable
                    # Format: - KOHAKU_HUB_KEY=value
                    match = re.match(r"- (KOHAKU_HUB_\w+)=(.+?)(?:\s+#.*)?$", stripped)
                    if match:
                        key, value = match.groups()
                        env_vars[key] = value.strip()

        print(f"✓ Loaded {len(env_vars)} environment variables from {filepath}")
        return env_vars

    except Exception as e:
        print(f"⚠ Failed to parse {filepath}: {e}")
        return {}


def read_existing_config_toml(filepath: Path) -> dict:
    """Parse existing config.toml.

    Returns:
        Dict of nested configuration
    """
    if not filepath.exists():
        return {}

    try:
        with open(filepath, "rb") as f:
            config = tomllib.load(f)
        print(f"✓ Loaded configuration from {filepath}")
        return config
    except Exception as e:
        print(f"⚠ Failed to parse {filepath}: {e}")
        return {}


def get_nested_value(config: dict, path: str, default=None):
    """Get nested value from config dict using dot notation.

    Args:
        config: Config dict
        path: Dot-separated path (e.g., "auth.session_secret")
        default: Default value if not found

    Returns:
        Value at path or default
    """
    keys = path.split(".")
    current = config
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current


def set_nested_value(config: dict, path: str, value):
    """Set nested value in config dict using dot notation.

    Args:
        config: Config dict to modify
        path: Dot-separated path (e.g., "auth.session_secret")
        value: Value to set
    """
    keys = path.split(".")
    current = config
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def ask_or_default(prompt: str, existing_value=None, default=None, auto_mode=False):
    """Ask user for value or use existing/default.

    Args:
        prompt: Question to ask
        existing_value: Existing value from old config
        default: Default value if no existing value
        auto_mode: If True, use existing or default without prompting

    Returns:
        Selected value
    """
    if existing_value is not None:
        if auto_mode:
            return existing_value
        response = input(f"{prompt} [existing: {existing_value}]: ").strip()
        return response if response else existing_value

    if auto_mode:
        return default

    if default is not None:
        response = input(f"{prompt} [default: {default}]: ").strip()
        return response if response else default

    return input(f"{prompt}: ").strip()


def migrate_docker_compose(existing_env: dict, auto_mode: bool = False) -> dict:
    """Migrate docker-compose environment variables.

    Args:
        existing_env: Dict of existing KOHAKU_HUB_* variables
        auto_mode: If True, use defaults for new fields without prompting

    Returns:
        Dict of all environment variables (old + new)
    """
    print("\n" + "=" * 60)
    print("Migrating docker-compose.yml Environment Variables")
    print("=" * 60 + "\n")

    migrated = existing_env.copy()

    # New fields to check and add if missing
    new_fields = {
        "KOHAKU_HUB_DATABASE_KEY": {
            "prompt": "Database encryption key (for external tokens)",
            "default": generate_secret(32),  # 43 chars
            "comment": "For encrypting external fallback tokens (generate with: openssl rand -hex 32)",
        },
        "LAKEFS_BLOCKSTORE_S3_BUCKET": {
            "prompt": "LakeFS S3 bucket name",
            "default": "hub-storage",
            "comment": "S3 bucket for LakeFS blockstore",
        },
        "KOHAKU_HUB_FALLBACK_REQUIRE_AUTH": {
            "prompt": "Require authentication for fallback access? (true/false)",
            "default": "false",
            "comment": "Set true to require authentication for fallback access",
        },
    }

    for key, config in new_fields.items():
        if key not in migrated:
            print(f"\n🆕 New field: {key}")
            print(f"   {config['comment']}")

            if auto_mode:
                value = config["default"]
                print(f"   Using default: {value}")
            else:
                value = ask_or_default(
                    f"   Enter value",
                    existing_value=None,
                    default=config["default"],
                    auto_mode=auto_mode,
                )

            migrated[key] = value

    return migrated


def migrate_config_toml(existing_config: dict, auto_mode: bool = False) -> dict:
    """Migrate config.toml configuration.

    Args:
        existing_config: Existing config dict
        auto_mode: If True, use defaults for new fields without prompting

    Returns:
        Migrated config dict
    """
    print("\n" + "=" * 60)
    print("Migrating config.toml")
    print("=" * 60 + "\n")

    migrated = existing_config.copy()

    # New fields to check and add if missing (using dot notation for nested paths)
    new_fields = {
        "app.database_key": {
            "prompt": "Database encryption key (for external tokens)",
            "default": generate_secret(32),
            "comment": "For encrypting external fallback tokens",
        },
        "lakefs.s3_bucket": {
            "prompt": "LakeFS S3 bucket name",
            "default": "hub-storage",
            "comment": "S3 bucket for LakeFS blockstore",
        },
        "fallback.require_auth": {
            "prompt": "Require authentication for fallback access? (true/false)",
            "default": False,
            "comment": "Set true to require authentication for fallback access",
        },
    }

    for path, config in new_fields.items():
        existing_value = get_nested_value(migrated, path)

        if existing_value is None:
            print(f"\n🆕 New field: {path}")
            print(f"   {config['comment']}")

            if auto_mode:
                value = config["default"]
                print(f"   Using default: {value}")
            else:
                value = ask_or_default(
                    f"   Enter value",
                    existing_value=None,
                    default=config["default"],
                    auto_mode=auto_mode,
                )

            set_nested_value(migrated, path, value)

    return migrated


def write_docker_compose(
    filepath: Path, env_vars: dict, base_content: str | None = None
):
    """Write updated docker-compose.yml with new environment variables.

    Args:
        filepath: Path to docker-compose.yml
        env_vars: Dict of environment variables
        base_content: Optional base content (if None, reads from example)
    """
    if base_content is None:
        # Read from example file as template
        example_path = Path(__file__).parent.parent / "docker-compose.example.yml"
        if example_path.exists():
            with open(example_path, "r", encoding="utf-8") as f:
                base_content = f.read()
        else:
            print("⚠ docker-compose.example.yml not found, cannot generate")
            return

    # Replace placeholders in environment section
    # This is a simple approach - for production, use proper YAML library
    lines = base_content.split("\n")
    output_lines = []

    for line in lines:
        stripped = line.strip()

        # Replace known variables
        if stripped.startswith("- KOHAKU_HUB_") or stripped.startswith(
            "- LAKEFS_BLOCKSTORE_"
        ):
            match = re.match(
                r"(\s*)- ((?:KOHAKU_HUB|LAKEFS_BLOCKSTORE)_\w+)=(.+?)(?:\s+#.*)?$",
                line,
            )
            if match:
                indent, key, old_value = match.groups()
                if key in env_vars:
                    # Keep comment from original line
                    comment_match = re.search(r"(#.+)$", line)
                    comment = comment_match.group(1) if comment_match else ""
                    output_lines.append(
                        f"{indent}- {key}={env_vars[key]} {comment}".rstrip()
                    )
                    continue

        output_lines.append(line)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"\n✓ Updated {filepath}")


def write_config_toml(filepath: Path, config: dict):
    """Write updated config.toml.

    Note: This uses simple formatting, not a full TOML library.
    """
    lines = []

    # Helper to write section
    def write_section(section_name: str, data: dict, indent: int = 0):
        prefix = "  " * indent
        if section_name:
            lines.append(f"{prefix}[{section_name}]")

        for key, value in data.items():
            if isinstance(value, dict):
                # Nested section
                write_section(
                    f"{section_name}.{key}" if section_name else key, value, indent
                )
            elif isinstance(value, bool):
                lines.append(f"{prefix}{key} = {str(value).lower()}")
            elif isinstance(value, (int, float)):
                lines.append(f"{prefix}{key} = {value}")
            elif isinstance(value, str):
                lines.append(f'{prefix}{key} = "{value}"')
            elif isinstance(value, list):
                # Simple list formatting
                items = ", ".join(
                    f'"{item}"' if isinstance(item, str) else str(item)
                    for item in value
                )
                lines.append(f"{prefix}{key} = [{items}]")
            else:
                lines.append(f'{prefix}{key} = "{value}"')

        if section_name:
            lines.append("")  # Blank line after section

    # Write sections in order
    for section in [
        "s3",
        "lakefs",
        "smtp",
        "auth",
        "admin",
        "app",
        "quota",
        "fallback",
    ]:
        if section in config:
            write_section(section, config[section])

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n✓ Updated {filepath}")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate KohakuHub configuration to latest format"
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto-migrate with defaults for new fields (no prompts)",
    )
    parser.add_argument(
        "--backup-only",
        action="store_true",
        help="Create backups without migration",
    )
    parser.add_argument(
        "--docker-compose",
        default="docker-compose.yml",
        help="Path to docker-compose.yml (default: docker-compose.yml)",
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: config.toml)",
    )

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("KohakuHub Configuration Migration Tool")
    print("=" * 60)

    repo_root = Path(__file__).parent.parent
    docker_compose_path = repo_root / args.docker_compose
    config_toml_path = repo_root / args.config

    # Check if files exist
    has_docker_compose = docker_compose_path.exists()
    has_config_toml = config_toml_path.exists()

    if not has_docker_compose and not has_config_toml:
        print("\n⚠ No configuration files found!")
        print(f"   Looking for: {docker_compose_path} or {config_toml_path}")
        print("\n💡 Run generate_docker_compose.py to create initial configuration")
        sys.exit(1)

    print(f"\nFound configuration files:")
    if has_docker_compose:
        print(f"  ✓ {docker_compose_path}")
    if has_config_toml:
        print(f"  ✓ {config_toml_path}")

    # Create backups
    print("\nCreating backups...")
    if has_docker_compose:
        backup_file(docker_compose_path)
    if has_config_toml:
        backup_file(config_toml_path)

    if args.backup_only:
        print("\n✓ Backups created. Exiting (--backup-only mode)")
        sys.exit(0)

    # Migrate docker-compose.yml
    if has_docker_compose:
        print("\n" + "-" * 60)
        existing_env = read_existing_docker_compose(docker_compose_path)
        migrated_env = migrate_docker_compose(existing_env, auto_mode=args.auto)

        # Read base content from existing file
        with open(docker_compose_path, "r", encoding="utf-8") as f:
            base_content = f.read()

        write_docker_compose(docker_compose_path, migrated_env, base_content)

    # Migrate config.toml
    if has_config_toml:
        print("\n" + "-" * 60)
        existing_config = read_existing_config_toml(config_toml_path)
        migrated_config = migrate_config_toml(existing_config, auto_mode=args.auto)
        write_config_toml(config_toml_path, migrated_config)

    print("\n" + "=" * 60)
    print("✓ Migration Complete!")
    print("=" * 60)
    print("\n📋 Summary:")
    print("  • Backups created with timestamp")
    print("  • Configuration files updated with new fields")
    print("  • Existing values preserved")
    print("\n💡 Next steps:")
    print("  1. Review the updated configuration files")
    print("  2. Restart services: docker-compose down && docker-compose up -d")
    print(
        "  3. Run migrations: docker-compose exec hub-api python scripts/run_migrations.py"
    )
    print()


if __name__ == "__main__":
    main()
