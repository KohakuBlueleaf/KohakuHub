#!/usr/bin/env python3
"""
Docker Compose Generator for KohakuHub

This script generates a docker-compose.yml file based on user preferences.
Can read configuration from kohakuhub.conf file for automation.
"""

import argparse
import configparser
import os
import re
import secrets
import shutil
import sys
import tomllib
from datetime import datetime
from pathlib import Path


def generate_secret(length: int = 32) -> str:
    """Generate a random URL-safe secret key.

    Args:
        length: Number of random bytes (result will be ~1.33x longer due to base64 encoding)
                Common values: 32 (→43 chars), 48 (→64 chars)

    Returns:
        URL-safe base64 encoded string
    """
    return secrets.token_urlsafe(length)


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question."""
    default_str = "Y/n" if default else "y/N"
    while True:
        response = input(f"{prompt} [{default_str}]: ").strip().lower()
        if not response:
            return default
        if response in ["y", "yes"]:
            return True
        if response in ["n", "no"]:
            return False
        print("Please answer 'y' or 'n'")


def ask_string(prompt: str, default: str = "") -> str:
    """Ask for a string input."""
    if default:
        response = input(f"{prompt} [{default}]: ").strip()
        return response if response else default
    else:
        while True:
            response = input(f"{prompt}: ").strip()
            if response:
                return response
            print("This field is required")


def ask_int(prompt: str, default: int) -> int:
    """Ask for an integer input."""
    while True:
        response = input(f"{prompt} [{default}]: ").strip()
        if not response:
            return default
        try:
            return int(response)
        except ValueError:
            print("Please enter a valid number")


def generate_postgres_service(config: dict) -> str:
    """Generate PostgreSQL service configuration."""
    if config["postgres_builtin"]:
        return f"""  postgres:
    image: postgres:15
    container_name: postgres
    restart: always
    environment:
      - POSTGRES_USER={config["postgres_user"]}
      - POSTGRES_PASSWORD={config["postgres_password"]}
      - POSTGRES_DB={config["postgres_db"]}
    ports:
      - "25432:5432" # Optional: for external access
    volumes:
      - ./hub-meta/postgres-data:/var/lib/postgresql/data
"""
    return ""


def generate_minio_service(config: dict) -> str:
    """Generate MinIO service configuration."""
    if config["s3_builtin"] and config.get("s3_provider") == "minio":
        return f"""  minio:
    image: quay.io/minio/minio:latest
    container_name: minio
    command: server /data --console-address ":29000"
    environment:
      - MINIO_ROOT_USER={config["s3_access_key"]}
      - MINIO_ROOT_PASSWORD={config["s3_secret_key"]}
    ports:
      - "29001:9000"    # S3 API
      - "29000:29000"   # Web Console
    volumes:
      - ./hub-storage/minio-data:/data
      - ./hub-meta/minio-data:/root/.minio
"""
    return ""


def generate_garage_service(config: dict) -> str:
    """Generate Garage S3 service configuration."""
    if config["s3_builtin"] and config.get("s3_provider") == "garage":
        # Generate Garage secrets
        garage_rpc_secret = config.get("garage_rpc_secret", secrets.token_hex(32))
        garage_admin_token = config.get("garage_admin_token", generate_secret(32))
        garage_metrics_token = config.get("garage_metrics_token", generate_secret(32))

        return f"""  garage:
    image: dxflrs/garage:v2.1.0
    container_name: garage
    restart: unless-stopped
    ports:
      - "39000:39000"    # S3 API
      - "39001:39001"    # RPC/Admin API
      - "39002:39002"    # S3 Web
      - "39003:39003"    # Admin API
    environment:
      - RUST_LOG=garage=info
      - GARAGE_RPC_SECRET={garage_rpc_secret}
      - GARAGE_ADMIN_TOKEN={garage_admin_token}
      - GARAGE_METRICS_TOKEN={garage_metrics_token}
    volumes:
      - ./docker/garage/garage.toml:/etc/garage.toml
      - ./hub-storage/garage-meta:/var/lib/garage/meta
      - ./hub-storage/garage-data:/var/lib/garage/data
"""
    return ""


def generate_lakefs_service(config: dict) -> str:
    """Generate LakeFS service configuration."""
    depends_on = []

    if config["s3_builtin"]:
        if config.get("s3_provider") == "minio":
            depends_on.append("minio")
        elif config.get("s3_provider") == "garage":
            depends_on.append("garage")

    if config["postgres_builtin"] and config["lakefs_use_postgres"]:
        depends_on.append("postgres")

    depends_on_str = ""
    if depends_on:
        depends_on_str = "    depends_on:\n"
        for dep in depends_on:
            depends_on_str += f"      - {dep}\n"

    # LakeFS database configuration
    if config["lakefs_use_postgres"]:
        if config["postgres_builtin"]:
            lakefs_db_config = f"""      - LAKEFS_DATABASE_TYPE=postgres
      - LAKEFS_DATABASE_POSTGRES_CONNECTION_STRING=postgres://{config["postgres_user"]}:{config["postgres_password"]}@postgres:5432/{config["lakefs_db"]}?sslmode=disable"""
            # Add environment variables for init script
            init_env_vars = f"""      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_USER={config["postgres_user"]}
      - POSTGRES_PASSWORD={config["postgres_password"]}
      - POSTGRES_DB={config["postgres_db"]}
      - LAKEFS_DB={config["lakefs_db"]}"""
        else:
            lakefs_db_config = f"""      - LAKEFS_DATABASE_TYPE=postgres
      - LAKEFS_DATABASE_POSTGRES_CONNECTION_STRING=postgres://{config["postgres_user"]}:{config["postgres_password"]}@{config["postgres_host"]}:{config["postgres_port"]}/{config["lakefs_db"]}?sslmode=disable"""
            # Add environment variables for init script
            init_env_vars = f"""      - POSTGRES_HOST={config["postgres_host"]}
      - POSTGRES_PORT={config["postgres_port"]}
      - POSTGRES_USER={config["postgres_user"]}
      - POSTGRES_PASSWORD={config["postgres_password"]}
      - POSTGRES_DB={config["postgres_db"]}
      - LAKEFS_DB={config["lakefs_db"]}"""
    else:
        lakefs_db_config = """      - LAKEFS_DATABASE_TYPE=local
      - LAKEFS_DATABASE_LOCAL_PATH=/var/lakefs/data/metadata.db"""
        init_env_vars = ""

    # S3 blockstore configuration
    if config["s3_builtin"]:
        if config.get("s3_provider") == "garage":
            s3_endpoint = "http://garage:39000"
            force_path_style = "true"
            s3_region = "garage"  # Garage uses custom region name
        else:  # minio
            s3_endpoint = "http://minio:9000"
            force_path_style = "true"
            s3_region = "us-east-1"  # MinIO works with us-east-1
        s3_bucket = "hub-storage"
    else:
        s3_endpoint = config["s3_endpoint"]
        # Use path-style for all non-AWS endpoints (MinIO, CloudFlare R2, custom S3)
        # Only AWS S3 (*.amazonaws.com) should use virtual-hosted style
        force_path_style = "false" if "amazonaws.com" in s3_endpoint.lower() else "true"
        s3_region = config.get("s3_region", "us-east-1")
        s3_bucket = config.get("s3_bucket") or "hub-storage"

    # Add entrypoint and volumes for database initialization
    entrypoint_config = ""
    volumes_config = """      - ./hub-meta/lakefs-data:/var/lakefs/data
      - ./hub-meta/lakefs-cache:/lakefs/data/cache"""

    if config["lakefs_use_postgres"]:
        entrypoint_config = """    entrypoint: ["/bin/sh", "/scripts/lakefs-entrypoint.sh"]
    command: ["run"]"""
        volumes_config += """
      - ./docker/lakefs/lakefs-entrypoint.sh:/scripts/lakefs-entrypoint.sh:ro
      - ./docker/lakefs/init-databases.sh:/scripts/init-databases.sh:ro"""

    # Add external network if needed (for external postgres or s3)
    lakefs_networks_str = ""
    if config.get("external_network") and (
        not config["postgres_builtin"] or not config["s3_builtin"]
    ):
        lakefs_networks_str = f"""    networks:
      - default
      - {config["external_network"]}
"""

    return f"""  lakefs:
    build:
      context: ./docker/lakefs
    container_name: lakefs
{entrypoint_config}
    environment:
{lakefs_db_config}
{init_env_vars}
      - LAKEFS_BLOCKSTORE_TYPE=s3
      - LAKEFS_BLOCKSTORE_S3_ENDPOINT={s3_endpoint}
      - LAKEFS_BLOCKSTORE_S3_BUCKET={s3_bucket}
      - LAKEFS_BLOCKSTORE_S3_FORCE_PATH_STYLE={force_path_style}
      - LAKEFS_BLOCKSTORE_S3_CREDENTIALS_ACCESS_KEY_ID={config["s3_access_key"]}
      - LAKEFS_BLOCKSTORE_S3_CREDENTIALS_SECRET_ACCESS_KEY={config["s3_secret_key"]}
      - LAKEFS_BLOCKSTORE_S3_REGION={s3_region}
      - LAKEFS_AUTH_ENCRYPT_SECRET_KEY={config["lakefs_encrypt_key"]}
      - LAKEFS_LOGGING_FORMAT=text
      - LAKEFS_LISTEN_ADDRESS=0.0.0.0:28000
    ports:
      - "28000:28000"   # LakeFS admin UI (optional)
    user: "${{UID}}:${{GID}}"
{depends_on_str}    volumes:
{volumes_config}
{lakefs_networks_str}"""


def generate_hub_api_service(config: dict) -> str:
    """Generate hub-api service configuration."""
    depends_on = ["lakefs"]

    if config["postgres_builtin"]:
        depends_on.insert(0, "postgres")

    if config["s3_builtin"]:
        if config.get("s3_provider") == "minio":
            depends_on.append("minio")
        elif config.get("s3_provider") == "garage":
            depends_on.append("garage")

    depends_on_str = "    depends_on:\n"
    for dep in depends_on:
        depends_on_str += f"      - {dep}\n"

    # Add external network if needed (for external postgres or s3)
    networks_str = ""
    if config.get("external_network") and (
        not config["postgres_builtin"] or not config["s3_builtin"]
    ):
        networks_str = f"""    networks:
      - default
      - {config["external_network"]}
"""

    # Database configuration
    if config["postgres_builtin"]:
        db_url = f"postgresql://{config['postgres_user']}:{config['postgres_password']}@postgres:5432/{config['postgres_db']}"
    else:
        db_url = f"postgresql://{config['postgres_user']}:{config['postgres_password']}@{config['postgres_host']}:{config['postgres_port']}/{config['postgres_db']}"

    # S3 configuration
    if config["s3_builtin"]:
        if config.get("s3_provider") == "garage":
            s3_endpoint_internal = "http://garage:39000"
            s3_endpoint_public = "http://127.0.0.1:39000"
            s3_region = "garage"  # Garage uses custom region name
            # Garage: MUST use s3v4 (only signature version supported)
            s3_sig_version_line = (
                "      - KOHAKU_HUB_S3_SIGNATURE_VERSION=s3v4  # Required for Garage"
            )
        else:  # minio
            s3_endpoint_internal = "http://minio:9000"
            s3_endpoint_public = "http://127.0.0.1:29001"
            s3_region = "us-east-1"  # MinIO works with us-east-1
            # MinIO: Don't set signature_version (uses default/s3v2-compatible)
            s3_sig_version_line = "      # - KOHAKU_HUB_S3_SIGNATURE_VERSION=s3v4  # Uncomment for R2/AWS S3 (leave commented for MinIO)"
    else:
        s3_endpoint_internal = config["s3_endpoint"]
        s3_endpoint_public = config["s3_endpoint"]
        s3_region = config.get("s3_region", "us-east-1")
        # External S3: Use configured value or default to s3v4
        s3_sig_version = config.get("s3_signature_version", "s3v4")
        if s3_sig_version:
            s3_sig_version_line = (
                f"      - KOHAKU_HUB_S3_SIGNATURE_VERSION={s3_sig_version}"
            )
        else:
            s3_sig_version_line = (
                "      # - KOHAKU_HUB_S3_SIGNATURE_VERSION=s3v4  # Uncomment if needed"
            )

    # No Garage-specific config needed (manual setup)
    garage_config_section = ""

    return f"""  hub-api:
    build: .
    container_name: hub-api
    restart: always
    ports:
      - "48888:48888" # Internal API port (optional, for debugging)
{depends_on_str}    environment:
      ## ===== CRITICAL: Endpoint Configuration (MUST CHANGE) =====
      ## These determine how users access your KohakuHub instance
      - KOHAKU_HUB_BASE_URL=http://127.0.0.1:28080 # Change to your public URL (e.g., https://hub.example.com)
      - KOHAKU_HUB_S3_PUBLIC_ENDPOINT={s3_endpoint_public} # Change to your S3 public URL

      ## ===== CRITICAL: Security Configuration (MUST CHANGE) =====
      - KOHAKU_HUB_SESSION_SECRET={config["session_secret"]}
      - KOHAKU_HUB_ADMIN_SECRET_TOKEN={config["admin_secret"]}
      - KOHAKU_HUB_DATABASE_KEY={config["database_key"]}

      ## ===== Performance Configuration =====
      - KOHAKU_HUB_WORKERS=4 # Number of worker processes (1-8, recommend: CPU cores)

      ## ===== Database Configuration =====
      - KOHAKU_HUB_DB_BACKEND=postgres
      - KOHAKU_HUB_DATABASE_URL={db_url}

      ## ===== S3 Storage Configuration =====
      - KOHAKU_HUB_S3_ENDPOINT={s3_endpoint_internal}
      - KOHAKU_HUB_S3_ACCESS_KEY={config["s3_access_key"]}
      - KOHAKU_HUB_S3_SECRET_KEY={config["s3_secret_key"]}
      - KOHAKU_HUB_S3_BUCKET={config["s3_bucket"]}
      - KOHAKU_HUB_S3_REGION={s3_region}  # auto (recommended), us-east-1, or your AWS region
{s3_sig_version_line}

      ## ===== LakeFS Configuration =====
      - KOHAKU_HUB_LAKEFS_ENDPOINT=http://lakefs:28000
      - KOHAKU_HUB_LAKEFS_REPO_NAMESPACE=hf
      # LakeFS credentials auto-generated on first start

      ## ===== Application Configuration =====
      - KOHAKU_HUB_SITE_NAME=KohakuHub
      - KOHAKU_HUB_LFS_THRESHOLD_BYTES=1000000
      - KOHAKU_HUB_LFS_MULTIPART_THRESHOLD_BYTES=100_000_000 # 100MB - use multipart for files larger than this
      - KOHAKU_HUB_LFS_MULTIPART_CHUNK_SIZE_BYTES=50_000_000 # 50MB - size of each part (min 5MB except last)
      - KOHAKU_HUB_LFS_KEEP_VERSIONS=5
      - KOHAKU_HUB_LFS_AUTO_GC=true
      - KOHAKU_HUB_AUTO_MIGRATE=true # Auto-confirm database migrations (required for Docker)
      - KOHAKU_HUB_LOG_LEVEL=INFO
      - KOHAKU_HUB_LOG_FORMAT=terminal
      - KOHAKU_HUB_LOG_DIR=logs/

      ## ===== Auth & SMTP Configuration =====
      - KOHAKU_HUB_REQUIRE_EMAIL_VERIFICATION=false
      - KOHAKU_HUB_INVITATION_ONLY=false # Set to true to require invitation for registration
      - KOHAKU_HUB_SESSION_EXPIRE_HOURS=168
      - KOHAKU_HUB_TOKEN_EXPIRE_DAYS=365
      - KOHAKU_HUB_ADMIN_ENABLED=true
      # SMTP (Optional - for email verification)
      - KOHAKU_HUB_SMTP_ENABLED=false
      - KOHAKU_HUB_SMTP_HOST=smtp.gmail.com
      - KOHAKU_HUB_SMTP_PORT=587
      - KOHAKU_HUB_SMTP_USERNAME=
      - KOHAKU_HUB_SMTP_PASSWORD=
      - KOHAKU_HUB_SMTP_FROM=noreply@kohakuhub.local
      - KOHAKU_HUB_SMTP_TLS=true

      ## ===== Storage Quota Configuration (Optional) =====
      - KOHAKU_HUB_DEFAULT_USER_PRIVATE_QUOTA_BYTES=10_000_000
      - KOHAKU_HUB_DEFAULT_USER_PUBLIC_QUOTA_BYTES=100_000_000
      - KOHAKU_HUB_DEFAULT_ORG_PRIVATE_QUOTA_BYTES=10_000_000
      - KOHAKU_HUB_DEFAULT_ORG_PUBLIC_QUOTA_BYTES=100_000_000{garage_config_section}
    volumes:
      - ./hub-meta/hub-api:/hub-api-creds
{networks_str}"""


def generate_hub_ui_service() -> str:
    """Generate hub-ui service configuration."""
    return """  hub-ui:
    image: nginx:alpine
    container_name: hub-ui
    restart: always
    ports:
      - "28080:80" # Public web interface
    volumes:
      - ./src/kohaku-hub-ui/dist:/usr/share/nginx/html
      - ./src/kohaku-hub-admin/dist:/usr/share/nginx/html-admin
      - ./docker/nginx/default.conf:/etc/nginx/conf.d/default.conf
    depends_on:
      - hub-api
"""


def generate_docker_compose(config: dict) -> str:
    """Generate the complete docker-compose.yml content."""
    services = []

    # Add services in order
    services.append(generate_hub_ui_service())
    services.append(generate_hub_api_service(config))

    if config["s3_builtin"]:
        if config.get("s3_provider") == "garage":
            services.append(generate_garage_service(config))
        else:
            services.append(generate_minio_service(config))

    services.append(generate_lakefs_service(config))

    if config["postgres_builtin"]:
        services.append(generate_postgres_service(config))

    content = """# docker-compose.yml - KohakuHub Configuration
# Generated by KohakuHub docker-compose generator
# Customize for your deployment

services:
"""
    content += "\n".join(services)

    # Network configuration
    content += "\nnetworks:\n  default:\n    name: hub-net\n"

    # Add external network if specified
    if config.get("external_network"):
        content += f"""  {config["external_network"]}:
    external: true
"""

    return content


def load_config_file(config_path: Path) -> dict:
    """Load configuration from INI file."""
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)

    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    config = {}

    # PostgreSQL section
    if parser.has_section("postgresql"):
        pg = parser["postgresql"]
        config["postgres_builtin"] = pg.getboolean("builtin", fallback=True)
        config["postgres_host"] = pg.get("host", fallback="postgres")
        config["postgres_port"] = pg.getint("port", fallback=5432)
        config["postgres_user"] = pg.get("user", fallback="hub")
        config["postgres_password"] = pg.get("password", fallback="hubpass")
        config["postgres_db"] = pg.get("database", fallback="kohakuhub")
    else:
        config["postgres_builtin"] = True
        config["postgres_user"] = "hub"
        config["postgres_password"] = "hubpass"
        config["postgres_db"] = "kohakuhub"
        config["postgres_host"] = "postgres"
        config["postgres_port"] = 5432

    # LakeFS section
    if parser.has_section("lakefs"):
        lakefs = parser["lakefs"]
        config["lakefs_use_postgres"] = lakefs.getboolean("use_postgres", fallback=True)
        config["lakefs_db"] = lakefs.get("database", fallback="lakefs")
        config["lakefs_encrypt_key"] = lakefs.get(
            "encrypt_key",
            fallback=generate_secret(32),  # 43 chars
        )
    else:
        config["lakefs_use_postgres"] = True
        config["lakefs_db"] = "lakefs"
        config["lakefs_encrypt_key"] = generate_secret(32)  # 43 chars

    # S3 section
    if parser.has_section("s3"):
        s3 = parser["s3"]
        config["s3_builtin"] = s3.getboolean("builtin", fallback=True)
        config["s3_provider"] = s3.get(
            "provider", fallback="minio"
        )  # minio (default) or garage

        # Set defaults based on provider
        if config["s3_provider"] == "garage":
            default_endpoint = "http://garage:3900"
            default_region = "garage"
            default_sig_version = "s3v4"  # Garage requires s3v4
        else:  # minio
            default_endpoint = "http://minio:9000"
            default_region = "us-east-1"
            default_sig_version = ""  # MinIO uses default

        config["s3_endpoint"] = s3.get("endpoint", fallback=default_endpoint)
        config["s3_access_key"] = s3.get(
            "access_key", fallback=generate_secret(24)
        )  # 32 chars
        config["s3_secret_key"] = s3.get(
            "secret_key", fallback=generate_secret(48)
        )  # 64 chars
        config["s3_region"] = s3.get("region", fallback=default_region)
        config["s3_signature_version"] = s3.get(
            "signature_version", fallback=default_sig_version
        )
    else:
        config["s3_builtin"] = True
        config["s3_provider"] = "minio"  # Default to MinIO (works out of box)
        config["s3_endpoint"] = "http://garage:3900"
        config["s3_access_key"] = generate_secret(24)  # 32 chars
        config["s3_secret_key"] = generate_secret(48)  # 64 chars
        config["s3_region"] = "garage"
        config["s3_signature_version"] = "s3v4"  # Garage requires s3v4

    # Security section
    if parser.has_section("security"):
        sec = parser["security"]
        config["session_secret"] = sec.get(
            "session_secret", fallback=generate_secret(48)
        )  # 64 chars
        config["admin_secret"] = sec.get(
            "admin_secret", fallback=generate_secret(48)
        )  # 64 chars
        config["database_key"] = sec.get(
            "database_key", fallback=generate_secret(32)
        )  # 43 chars
        # Garage secrets (if using Garage)
        config["garage_rpc_secret"] = sec.get(
            "garage_rpc_secret", fallback=secrets.token_hex(32)
        )  # 64 hex chars
        config["garage_admin_token"] = sec.get(
            "garage_admin_token", fallback=generate_secret(32)
        )  # Admin API token
        config["garage_metrics_token"] = sec.get(
            "garage_metrics_token", fallback=generate_secret(32)
        )  # Metrics API token
    else:
        config["session_secret"] = generate_secret(48)  # 64 chars
        config["admin_secret"] = generate_secret(48)  # 64 chars
        config["database_key"] = generate_secret(32)  # 43 chars for encryption
        config["garage_rpc_secret"] = secrets.token_hex(32)  # 64 hex chars for Garage
        config["garage_admin_token"] = generate_secret(32)
        config["garage_metrics_token"] = generate_secret(32)

    # Network section
    if parser.has_section("network"):
        net = parser["network"]
        config["external_network"] = net.get("external_network", fallback="")
    else:
        config["external_network"] = ""

    return config


def generate_config_template(output_path: Path):
    """Generate a template configuration file."""
    template = """# KohakuHub Configuration Template
# Use this file to automate docker-compose.yml generation
# Usage: python scripts/generate_docker_compose.py --config kohakuhub.conf

[postgresql]
# Use built-in PostgreSQL container (true) or external server (false)
builtin = true

# If builtin = false, specify connection details:
# host = your-postgres-host.com
# port = 5432

# PostgreSQL credentials
user = hub
password = hubpass
database = kohakuhub

[lakefs]
# Use PostgreSQL for LakeFS (true) or SQLite (false)
use_postgres = true

# LakeFS database name (separate from hub-api database)
database = lakefs

# LakeFS encryption key (auto-generated if not specified)
# encrypt_key = your-secret-key-here

[s3]
# Use built-in S3 container (true) or external S3 (false)
builtin = true

# S3 Provider: minio (default, auto-setup) or garage (manual setup, no CVEs)
provider = minio

    # If builtin = false, specify S3 endpoint and credentials:
# endpoint = https://your-s3-endpoint.com
# access_key = your-access-key
# secret_key = your-secret-key
# bucket = hub-storage
# region = us-east-1  # us-east-1 (default), auto for R2, garage for Garage, or specific AWS region
# signature_version = s3v4  # s3v4 for Garage/R2/AWS S3, leave empty for MinIO

# If builtin = true, credentials are auto-generated (recommended)
# You can override by uncommenting and setting custom values:
# access_key = your-custom-access-key
# secret_key = your-custom-secret-key
# For Garage:
#   region = garage
#   signature_version = s3v4  # Required for Garage
# For MinIO:
#   region = us-east-1
#   signature_version =  # Leave empty for MinIO (uses default)

[security]
# Session and admin secrets (auto-generated if not specified)
# session_secret = your-session-secret-here
# admin_secret = your-admin-secret-here
# database_key = your-database-encryption-key-here  # For encrypting external fallback tokens

[network]
# External bridge network (optional)
# Use this if PostgreSQL or S3 are in different Docker Compose setups
# Create the network first: docker network create shared-network
# external_network = shared-network
"""

    output_path.write_text(template, encoding="utf-8")
    print(f"[OK] Generated configuration template: {output_path}")
    print()
    print("Edit this file with your settings, then run:")
    print(f"  python scripts/generate_docker_compose.py --config {output_path}")


def migrate_existing_config(docker_compose_path: Path, config_toml_path: Path) -> dict:
    """Migrate existing configuration files interactively.

    Reads existing values and only prompts for new fields.

    Args:
        docker_compose_path: Path to docker-compose.yml
        config_toml_path: Path to config.toml

    Returns:
        Config dict with migrated values
    """
    config = {}

    # Read existing docker-compose.yml
    existing_env = {}
    if docker_compose_path.exists():
        try:
            with open(docker_compose_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Extract environment variables
            for line in content.split("\n"):
                match = re.match(
                    r"\s*- (KOHAKU_HUB_\w+)=(.+?)(?:\s+#.*)?$", line.strip()
                )
                if match:
                    key, value = match.groups()
                    existing_env[key] = value.strip()

            print(f"✓ Loaded {len(existing_env)} settings from docker-compose.yml")
        except Exception as e:
            print(f"⚠ Failed to read docker-compose.yml: {e}")

    # Read existing config.toml
    existing_toml = {}
    if config_toml_path.exists():
        try:
            with open(config_toml_path, "rb") as f:
                existing_toml = tomllib.load(f)
            print(f"✓ Loaded settings from config.toml")
        except Exception as e:
            print(f"⚠ Failed to read config.toml: {e}")

    print()
    print("=" * 60)
    print("Migration Mode - Only New Fields Will Be Asked")
    print("=" * 60)
    print()

    # Helper to get value from env or toml
    def get_existing(env_key: str, toml_path: str | None = None):
        if env_key in existing_env:
            return existing_env[env_key]
        if toml_path:
            keys = toml_path.split(".")
            val = existing_toml
            for key in keys:
                if isinstance(val, dict) and key in val:
                    val = val[key]
                else:
                    return None
            return val
        return None

    # PostgreSQL Configuration
    print("--- PostgreSQL Configuration ---")
    config["postgres_builtin"] = get_existing("KOHAKU_HUB_DB_BACKEND") != "sqlite"
    print(f"Using: {'Built-in PostgreSQL' if config['postgres_builtin'] else 'SQLite'}")

    if config["postgres_builtin"]:
        # Parse DATABASE_URL
        db_url = get_existing("KOHAKU_HUB_DATABASE_URL", "app.database_url")
        if isinstance(db_url, str) and db_url.startswith("postgresql://"):
            # Parse: postgresql://user:pass@host:port/db
            match = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", db_url)
            if match:
                user, password, host, port, db = match.groups()
                config["postgres_user"] = user
                config["postgres_password"] = password
                config["postgres_host"] = host
                config["postgres_port"] = int(port)
                config["postgres_db"] = db
                print(f"   User: {user}")
                print(f"   Database: {db}")
            else:
                # Fallback defaults
                config["postgres_user"] = "hub"
                config["postgres_password"] = "hubpass"
                config["postgres_host"] = "postgres"
                config["postgres_port"] = 5432
                config["postgres_db"] = "kohakuhub"
        else:
            config["postgres_user"] = "hub"
            config["postgres_password"] = "hubpass"
            config["postgres_host"] = "postgres"
            config["postgres_port"] = 5432
            config["postgres_db"] = "kohakuhub"
    else:
        config["postgres_user"] = ""
        config["postgres_password"] = ""
        config["postgres_host"] = ""
        config["postgres_port"] = 5432
        config["postgres_db"] = ""

    # LakeFS Configuration
    config["lakefs_use_postgres"] = True  # Most installations use postgres
    config["lakefs_db"] = "kohakuhub_lakefs"

    # S3 Configuration
    print("\n--- S3 Configuration ---")
    s3_endpoint = get_existing("KOHAKU_HUB_S3_ENDPOINT", "s3.endpoint")
    if isinstance(s3_endpoint, dict):
        s3_endpoint = None

    # Detect provider from endpoint
    if s3_endpoint:
        if "minio" in s3_endpoint:
            config["s3_builtin"] = True
            config["s3_provider"] = "minio"
        elif "garage" in s3_endpoint or ":3900" in s3_endpoint:
            config["s3_builtin"] = True
            config["s3_provider"] = "garage"
        else:
            config["s3_builtin"] = False
            config["s3_provider"] = "external"
    else:
        # Default to Garage (no CVE)
        config["s3_builtin"] = True
        config["s3_provider"] = "garage"

    print(
        f"Using: {'Built-in ' + config['s3_provider'].title() if config['s3_builtin'] else 'External S3'}"
    )

    config["s3_access_key"] = get_existing("KOHAKU_HUB_S3_ACCESS_KEY", "s3.access_key")
    if isinstance(config["s3_access_key"], dict):
        config["s3_access_key"] = None
    config["s3_access_key"] = config["s3_access_key"] or generate_secret(24)

    config["s3_secret_key"] = get_existing("KOHAKU_HUB_S3_SECRET_KEY", "s3.secret_key")
    if isinstance(config["s3_secret_key"], dict):
        config["s3_secret_key"] = None
    config["s3_secret_key"] = config["s3_secret_key"] or generate_secret(48)
    config["s3_bucket"] = get_existing("KOHAKU_HUB_S3_BUCKET", "s3.bucket")
    if isinstance(config["s3_bucket"], dict):
        config["s3_bucket"] = None
    config["s3_bucket"] = config["s3_bucket"] or "hub-storage"

    # Set endpoint based on provider
    if not s3_endpoint:
        s3_endpoint = (
            "http://garage:3900"
            if config["s3_provider"] == "garage"
            else "http://minio:9000"
        )
    config["s3_endpoint"] = s3_endpoint

    # Set region based on provider
    existing_region = get_existing("KOHAKU_HUB_S3_REGION", "s3.region")
    if isinstance(existing_region, dict):
        existing_region = None
    if existing_region:
        config["s3_region"] = existing_region
    else:
        config["s3_region"] = (
            "garage" if config["s3_provider"] == "garage" else "us-east-1"
        )

    # Set signature version
    existing_sig = get_existing(
        "KOHAKU_HUB_S3_SIGNATURE_VERSION", "s3.signature_version"
    )
    if isinstance(existing_sig, dict):
        existing_sig = None
    if existing_sig:
        config["s3_signature_version"] = existing_sig
    else:
        config["s3_signature_version"] = (
            "s3v4" if config["s3_provider"] == "garage" else ""
        )

    # Security Configuration
    print("\n--- Security Configuration ---")
    config["session_secret"] = get_existing(
        "KOHAKU_HUB_SESSION_SECRET", "auth.session_secret"
    )
    if isinstance(config["session_secret"], dict):
        config["session_secret"] = None

    config["admin_secret"] = get_existing(
        "KOHAKU_HUB_ADMIN_SECRET_TOKEN", "admin.secret_token"
    )
    if isinstance(config["admin_secret"], dict):
        config["admin_secret"] = None

    # NEW FIELD: database_key
    config["database_key"] = get_existing("KOHAKU_HUB_DATABASE_KEY", "app.database_key")
    if isinstance(config["database_key"], dict):
        config["database_key"] = None
    if not config["database_key"]:
        print("\n🆕 New field: DATABASE_KEY (for encrypting external fallback tokens)")
        default_db_key = generate_secret(32)
        print(f"   Generated: {default_db_key}")
        use_generated = ask_yes_no("Use generated database key?", default=True)
        config["database_key"] = (
            default_db_key if use_generated else ask_string("Database encryption key")
        )
    else:
        print(f"   Database key: (exists)")

    # Preserve existing secrets (DO NOT regenerate!)
    if not config["session_secret"]:
        print("\n⚠ Session secret missing - generating new one")
        config["session_secret"] = generate_secret(48)
    else:
        print(f"   Session secret: (exists)")

    if not config["admin_secret"]:
        print("\n⚠ Admin secret missing - generating new one")
        config["admin_secret"] = generate_secret(48)
    else:
        print(f"   Admin secret: (exists)")

    # LakeFS encryption key - MUST preserve existing value or generate only if missing
    config["lakefs_encrypt_key"] = get_existing("LAKEFS_ENCRYPT_SECRET_KEY")
    if isinstance(config["lakefs_encrypt_key"], dict):
        config["lakefs_encrypt_key"] = None
    if not config["lakefs_encrypt_key"]:
        print("\n⚠ LakeFS encryption key missing - generating new one")
        print("   WARNING: This will make existing LakeFS data inaccessible!")
        config["lakefs_encrypt_key"] = generate_secret(32)
    else:
        print(f"   LakeFS encrypt key: (exists - PRESERVED)")

    # Garage secrets - MUST preserve existing values or generate only if missing
    config["garage_rpc_secret"] = get_existing("GARAGE_RPC_SECRET")
    if isinstance(config["garage_rpc_secret"], dict):
        config["garage_rpc_secret"] = None
    if not config["garage_rpc_secret"]:
        print("\n⚠ Garage RPC secret missing - generating new one")
        config["garage_rpc_secret"] = secrets.token_hex(32)
    else:
        print(f"   Garage RPC secret: (exists - PRESERVED)")

    config["garage_admin_token"] = get_existing("GARAGE_ADMIN_TOKEN")
    if isinstance(config["garage_admin_token"], dict):
        config["garage_admin_token"] = None
    if not config["garage_admin_token"]:
        config["garage_admin_token"] = generate_secret(32)

    config["garage_metrics_token"] = get_existing("GARAGE_METRICS_TOKEN")
    if isinstance(config["garage_metrics_token"], dict):
        config["garage_metrics_token"] = None
    if not config["garage_metrics_token"]:
        config["garage_metrics_token"] = generate_secret(32)

    # Network
    config["external_network"] = ""

    print("\n✓ Migration complete - all existing values preserved")
    print("✓ New fields added")

    # Write updated values back to docker-compose.yml IN PLACE
    if docker_compose_path.exists():
        update_docker_compose_inplace(
            docker_compose_path,
            {
                "KOHAKU_HUB_DATABASE_KEY": config["database_key"],
                "LAKEFS_ENCRYPT_SECRET_KEY": config["lakefs_encrypt_key"],
                "LAKEFS_BLOCKSTORE_S3_BUCKET": config["s3_bucket"],
            },
        )

    # Write updated config.toml IN PLACE
    if config_toml_path.exists():
        update_config_toml_inplace(
            config_toml_path,
            {
                "app.database_key": config["database_key"],
                "s3.bucket": config["s3_bucket"],
                "fallback.require_auth": False,
            },
        )

    return config


def update_docker_compose_inplace(filepath: Path, new_vars: dict):
    """Update docker-compose.yml in place, adding new environment variables.

    Args:
        filepath: Path to docker-compose.yml
        new_vars: Dict of {ENV_VAR: value} to add/update
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    output_lines = []
    in_hub_api_env = False
    added_vars = set()

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Detect hub-api environment section
        if "hub-api:" in line:
            in_hub_api_env = False
        elif in_hub_api_env and stripped.startswith("environment:"):
            in_hub_api_env = True
        elif (
            "environment:" in line
            and i > 0
            and "hub-api" in "".join(lines[max(0, i - 10) : i])
        ):
            in_hub_api_env = True

        # Check if this is an env var line
        if in_hub_api_env and stripped.startswith("- "):
            for var_name, var_value in new_vars.items():
                if var_name in line:
                    # Update existing variable
                    indent = len(line) - len(line.lstrip())
                    comment_match = re.search(r"(#.+)$", line)
                    comment = " " + comment_match.group(1) if comment_match else ""
                    output_lines.append(
                        f"{' ' * indent}- {var_name}={var_value}{comment}\n"
                    )
                    added_vars.add(var_name)
                    break
            else:
                # Not updating this line, keep as-is
                output_lines.append(line)

            # If we're at the last env var and haven't added new vars, add them
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if not isinstance(next_line, str):
                    next_line = ""
                if not next_line.strip().startswith("- "):
                    for var_name, var_value in new_vars.items():
                        if var_name not in added_vars:
                            indent = len(line) - len(line.lstrip())
                            output_lines.append(
                                f"{' ' * indent}- {var_name}={var_value}\n"
                            )
                            added_vars.add(var_name)
        else:
            output_lines.append(line)

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(output_lines)

    print(f"✓ Updated {filepath} with {len(added_vars)} new variables")


def update_config_toml_inplace(filepath: Path, new_fields: dict):
    """Update config.toml in place, adding new fields.

    Args:
        filepath: Path to config.toml
        new_fields: Dict of {"section.key": value} to add
    """
    try:
        with open(filepath, "rb") as f:
            existing = tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        existing = {}

    # Add new fields
    for path, value in new_fields.items():
        keys = path.split(".")
        current = existing
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        # Only add if doesn't exist
        if keys[-1] not in current:
            current[keys[-1]] = value

    # Write back (simple TOML format)
    lines = []
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
        if section in existing:
            lines.append(f"[{section}]")
            for key, val in existing[section].items():
                if isinstance(val, bool):
                    lines.append(f"{key} = {str(val).lower()}")
                elif isinstance(val, (int, float)):
                    lines.append(f"{key} = {val}")
                elif isinstance(val, str):
                    lines.append(f'{key} = "{val}"')
                else:
                    lines.append(f'{key} = "{val}"')
            lines.append("")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✓ Updated {filepath}")


def main():
    """Main function."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Generate docker-compose.yml for KohakuHub"
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        help="Path to configuration file (kohakuhub.conf)",
    )
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="Generate a template configuration file",
    )
    args = parser.parse_args()

    # Generate template if requested
    if args.generate_config:
        template_path = Path(__file__).parent.parent / "kohakuhub.conf"
        generate_config_template(template_path)
        return

    print("=" * 60)
    print("KohakuHub Docker Compose Generator")
    print("=" * 60)
    print()

    # Check for existing configuration files
    repo_root = Path(__file__).parent.parent
    existing_docker_compose = repo_root / "docker-compose.yml"
    existing_config_toml = repo_root / "config.toml"

    has_existing_config = (
        existing_docker_compose.exists() or existing_config_toml.exists()
    )

    if has_existing_config and not args.config:
        print("🔍 Found existing configuration files:")
        if existing_docker_compose.exists():
            print(f"   ✓ {existing_docker_compose}")
        if existing_config_toml.exists():
            print(f"   ✓ {existing_config_toml}")
        print()

        use_migrate = ask_yes_no(
            "Use migration mode? (preserves existing values, only asks for new fields)",
            default=True,
        )
        print()

        if use_migrate:
            # Create timestamped backups
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if existing_docker_compose.exists():
                backup_path = repo_root / f"docker-compose.yml.backup.{timestamp}"
                shutil.copy2(existing_docker_compose, backup_path)
                print(f"✓ Created backup: {backup_path}")
            if existing_config_toml.exists():
                backup_path = repo_root / f"config.toml.backup.{timestamp}"
                shutil.copy2(existing_config_toml, backup_path)
                print(f"✓ Created backup: {backup_path}")
            print()

            # Load existing config and migrate
            config = migrate_existing_config(
                existing_docker_compose, existing_config_toml
            )
        else:
            print("⚠ Starting fresh configuration (existing files will be overwritten)")
            print()
            config = interactive_config()
    elif args.config:
        print(f"Loading configuration from: {args.config}")
        print()
        config = load_config_file(args.config)
        # Show loaded configuration
        print("Loaded configuration:")
        print(
            f"  PostgreSQL: {'Built-in' if config['postgres_builtin'] else 'External'}"
        )
        if not config["postgres_builtin"]:
            print(f"    Host: {config['postgres_host']}:{config['postgres_port']}")
        print(f"    Database: {config['postgres_db']}")
        print(
            f"  LakeFS: {'PostgreSQL' if config['lakefs_use_postgres'] else 'SQLite'}"
        )
        if config["lakefs_use_postgres"]:
            print(f"    Database: {config['lakefs_db']}")
        print(f"  S3: {'Built-in MinIO' if config['s3_builtin'] else 'External S3'}")
        if not config["s3_builtin"]:
            print(f"    Endpoint: {config['s3_endpoint']}")
        print()
    else:
        # Interactive mode - fresh config
        config = interactive_config()

    # Generate and write files
    generate_and_write_files(config)


def interactive_config() -> dict:
    """Run interactive configuration."""
    config = {}

    # PostgreSQL Configuration
    print("--- PostgreSQL Configuration ---")
    config["postgres_builtin"] = ask_yes_no(
        "Use built-in PostgreSQL container?", default=True
    )

    if config["postgres_builtin"]:
        config["postgres_user"] = ask_string("PostgreSQL username", default="hub")
        config["postgres_password"] = ask_string(
            "PostgreSQL password", default="hubpass"
        )
        config["postgres_db"] = ask_string(
            "PostgreSQL database name for hub-api", default="kohakuhub"
        )
        config["postgres_host"] = "postgres"
        config["postgres_port"] = 5432
    else:
        config["postgres_host"] = ask_string("PostgreSQL host")
        config["postgres_port"] = ask_int("PostgreSQL port", default=5432)
        config["postgres_user"] = ask_string("PostgreSQL username")
        config["postgres_password"] = ask_string("PostgreSQL password")
        config["postgres_db"] = ask_string(
            "PostgreSQL database name for hub-api", default="kohakuhub"
        )

    # LakeFS database configuration
    print()
    print("--- LakeFS Database Configuration ---")
    config["lakefs_use_postgres"] = ask_yes_no(
        "Use PostgreSQL for LakeFS? (No = use local SQLite)", default=True
    )

    if config["lakefs_use_postgres"]:
        config["lakefs_db"] = ask_string(
            "PostgreSQL database name for LakeFS", default="lakefs"
        )
    else:
        config["lakefs_db"] = None

    print()

    # S3 Configuration
    print("--- S3 Storage Configuration ---")
    config["s3_builtin"] = ask_yes_no("Use built-in S3 container?", default=True)

    if config["s3_builtin"]:
        print()
        print("Available S3 providers:")
        print("  1. MinIO (default, works out of box, has unresolved CVEs)")
        print("  2. Garage (lightweight, no CVEs, requires manual setup)")
        while True:
            choice = input("Choose S3 provider [1]: ").strip()
            if not choice or choice == "1":
                config["s3_provider"] = "minio"
                break
            elif choice == "2":
                config["s3_provider"] = "garage"
                break
            else:
                print("Please choose 1 or 2")

        # Generate secure random credentials
        default_access_key = generate_secret(24)  # 32 chars
        default_secret_key = generate_secret(48)  # 64 chars

        provider_name = config["s3_provider"].title()
        print(f"\nGenerated {provider_name} access key: {default_access_key}")
        print(f"Generated {provider_name} secret key: {default_secret_key}")
        use_generated = ask_yes_no(
            f"Use generated {provider_name} credentials?", default=True
        )

        if use_generated:
            config["s3_access_key"] = default_access_key
            config["s3_secret_key"] = default_secret_key
        else:
            config["s3_access_key"] = ask_string(f"{provider_name} access key")
            config["s3_secret_key"] = ask_string(f"{provider_name} secret key")

        # Set provider-specific defaults
        if config["s3_provider"] == "garage":
            config["s3_endpoint"] = "http://garage:3900"
            config["s3_region"] = "garage"
            config["s3_signature_version"] = "s3v4"  # Garage requires s3v4
        else:  # minio
            config["s3_endpoint"] = "http://minio:9000"
            config["s3_region"] = "us-east-1"
            config["s3_signature_version"] = ""  # MinIO uses default (don't set)
    else:
        config["s3_provider"] = "external"
        config["s3_endpoint"] = ask_string("S3 endpoint URL")
        config["s3_access_key"] = ask_string("S3 access key")
        config["s3_secret_key"] = ask_string("S3 secret key")
        config["s3_region"] = ask_string("S3 region", default="us-east-1")

        # Ask about signature version for external S3
        print()
        print("Signature version:")
        print("  - (empty): Use default (for MinIO compatibility)")
        print("  - s3v4: Cloudflare R2, AWS S3, Garage (recommended for R2/AWS/Garage)")
        sig_input = ask_string(
            "S3 signature version (s3v4 or leave empty)", default="s3v4"
        )
        config["s3_signature_version"] = (
            sig_input if sig_input.lower() != "none" else ""
        )

    print()

    # Security Configuration
    print("--- Security Configuration ---")
    default_session_secret = generate_secret(48)  # 64 chars for session encryption
    print(f"Generated session secret: {default_session_secret}")
    use_generated = ask_yes_no("Use generated session secret?", default=True)

    if use_generated:
        config["session_secret"] = default_session_secret
    else:
        config["session_secret"] = ask_string("Session secret key")

    print()
    same_as_session = ask_yes_no("Use same secret for admin token?", default=False)

    if same_as_session:
        config["admin_secret"] = config["session_secret"]
    else:
        default_admin_secret = generate_secret(48)  # 64 chars for admin token
        print(f"Generated admin secret: {default_admin_secret}")
        use_generated_admin = ask_yes_no("Use generated admin secret?", default=True)

        if use_generated_admin:
            config["admin_secret"] = default_admin_secret
        else:
            config["admin_secret"] = ask_string("Admin secret token")

    # Database encryption key (for external tokens)
    print()
    default_database_key = generate_secret(32)  # 43 chars for Fernet encryption
    print(f"Generated database encryption key: {default_database_key}")
    use_generated_db = ask_yes_no("Use generated database key?", default=True)

    if use_generated_db:
        config["database_key"] = default_database_key
    else:
        config["database_key"] = ask_string("Database encryption key")

    # LakeFS encryption key
    config["lakefs_encrypt_key"] = generate_secret(32)  # 43 chars

    # Garage secrets (if using Garage)
    config["garage_rpc_secret"] = secrets.token_hex(32)  # 64 hex chars
    config["garage_admin_token"] = generate_secret(32)
    config["garage_metrics_token"] = generate_secret(32)

    # Network configuration
    print()
    print("--- Network Configuration ---")
    use_external_network = False
    if not config["postgres_builtin"] or not config["s3_builtin"]:
        use_external_network = ask_yes_no(
            "Use external Docker network for cross-compose communication?",
            default=False,
        )

    if use_external_network:
        config["external_network"] = ask_string(
            "External network name", default="shared-network"
        )
        print()
        print(f"Note: Make sure the network exists:")
        print(f"  docker network create {config['external_network']}")
    else:
        config["external_network"] = ""

    return config


def generate_config_toml(config: dict) -> str:
    """Generate config.toml for local dev server."""
    # Adapt endpoints for localhost dev server
    if config["postgres_builtin"]:
        db_url = f"postgresql://{config['postgres_user']}:{config['postgres_password']}@localhost:25432/{config['postgres_db']}"
    else:
        db_url = f"postgresql://{config['postgres_user']}:{config['postgres_password']}@{config['postgres_host']}:{config['postgres_port']}/{config['postgres_db']}"

    # S3 configuration for dev
    if config["s3_builtin"]:
        if config.get("s3_provider") == "garage":
            s3_endpoint_internal = "http://localhost:39000"
            s3_endpoint_public = "http://localhost:39000"
            s3_region = "garage"
        else:  # minio
            s3_endpoint_internal = "http://localhost:29001"
            s3_endpoint_public = "http://localhost:29001"
            s3_region = "us-east-1"
    else:
        s3_endpoint_internal = config["s3_endpoint"]
        s3_endpoint_public = config["s3_endpoint"]
        s3_region = config.get("s3_region", "us-east-1")

    toml_content = f"""# KohakuHub Configuration File (TOML)
# Generated by KohakuHub docker-compose generator
# Use this for local development server

[s3]
endpoint = "{s3_endpoint_internal}"
public_endpoint = "{s3_endpoint_public}"
access_key = "{config["s3_access_key"]}"
secret_key = "{config["s3_secret_key"]}"
bucket = "hub-storage"
region = "{s3_region}"
force_path_style = true
"""

    # Add signature_version if set (required for Garage, R2, AWS S3)
    if config.get("s3_signature_version"):
        toml_content += f'signature_version = "{config["s3_signature_version"]}"\n'
    else:
        # Explicitly omit for MinIO (uses s3v2 by default)
        toml_content += "# signature_version not set (MinIO uses s3v2 by default)\n"

    toml_content += f"""
[lakefs]
endpoint = "http://localhost:28000"
repo_namespace = "hf"
# Credentials auto-generated on first start

[smtp]
enabled = false
host = "smtp.gmail.com"
port = 587
username = ""
password = ""
from_email = "noreply@kohakuhub.local"
use_tls = true

[auth]
require_email_verification = false
invitation_only = false
session_secret = "{config["session_secret"]}"
session_expire_hours = 168  # 7 days
token_expire_days = 365

[admin]
enabled = true
secret_token = "{config["admin_secret"]}"

[quota]
default_user_private_quota_bytes = 10_000_000      # 10MB
default_user_public_quota_bytes = 100_000_000      # 100MB
default_org_private_quota_bytes = 10_000_000       # 10MB
default_org_public_quota_bytes = 100_000_000       # 100MB

[fallback]
enabled = true
cache_ttl_seconds = 300
timeout_seconds = 10
max_concurrent_requests = 5
require_auth = false  # Set true to require authentication for fallback access

[app]
base_url = "http://localhost:48888"  # Dev server URL
api_base = "/api"
db_backend = "postgres"
database_url = "{db_url}"
database_key = "{config["database_key"]}"  # For encrypting external fallback tokens
# LFS Configuration (sizes in decimal: 1MB = 1,000,000 bytes)
lfs_threshold_bytes = 5_000_000  # 5MB - files larger use LFS
lfs_multipart_threshold_bytes = 100_000_000  # 100MB - files larger use multipart upload
lfs_multipart_chunk_size_bytes = 50_000_000  # 50MB - size of each part (min 5MB except last)
lfs_keep_versions = 5  # Keep last K versions of each LFS file
lfs_auto_gc = true  # Automatically delete old LFS objects on commit
# Download tracking settings
download_time_bucket_seconds = 900  # 15 minutes - session deduplication window
download_session_cleanup_threshold = 100  # Trigger cleanup when sessions > this
download_keep_sessions_days = 30  # Keep sessions from last N days
debug_log_payloads = false
site_name = "KohakuHub"
"""

    return toml_content


def generate_and_write_files(config: dict):
    """Generate and write docker-compose.yml and related files."""
    print()
    print("=" * 60)
    print("Generating docker-compose.yml and config.toml...")
    print("=" * 60)

    # Generate docker-compose content
    compose_content = generate_docker_compose(config)

    # Write docker-compose.yml
    compose_path = Path(__file__).parent.parent / "docker-compose.yml"
    compose_path.write_text(compose_content, encoding="utf-8")

    print()
    print(f"[OK] Successfully generated: {compose_path}")

    # Generate and write config.toml
    config_content = generate_config_toml(config)
    config_path = Path(__file__).parent.parent / "config.toml"
    config_path.write_text(config_content, encoding="utf-8")

    print(f"[OK] Successfully generated: {config_path}")

    if config["lakefs_use_postgres"]:
        print(
            "[OK] Database initialization scripts will run automatically when LakeFS starts"
        )
        print("     - scripts/init-databases.sh")
        print("     - scripts/lakefs-entrypoint.sh")
    print()
    print("Configuration Summary:")
    print("-" * 60)
    print(f"PostgreSQL: {'Built-in' if config['postgres_builtin'] else 'Custom'}")
    if config["postgres_builtin"]:
        print(f"  Hub-API Database: {config['postgres_db']}")
        if config["lakefs_use_postgres"]:
            print(f"  LakeFS Database: {config['lakefs_db']}")
    else:
        print(f"  Host: {config['postgres_host']}:{config['postgres_port']}")
        print(f"  Hub-API Database: {config['postgres_db']}")
        if config["lakefs_use_postgres"]:
            print(f"  LakeFS Database: {config['lakefs_db']}")
    print(
        f"LakeFS Database Backend: {'PostgreSQL' if config['lakefs_use_postgres'] else 'SQLite'}"
    )
    if config["s3_builtin"]:
        provider_name = config.get("s3_provider", "minio").title()
        print(f"S3 Storage: Built-in {provider_name}")
    else:
        print(f"S3 Storage: Custom S3")
        print(f"  Endpoint: {config['s3_endpoint']}")
    if config.get("external_network"):
        print(f"External Network: {config['external_network']}")
    print(f"Session Secret: {config['session_secret'][:20]}...")
    print(f"Admin Secret: {config['admin_secret'][:20]}...")
    print("-" * 60)
    print()
    print("Next steps:")
    step_num = 1

    if config.get("external_network"):
        print(f"{step_num}. Create external network if not exists:")
        print(f"   docker network create {config['external_network']}")
        step_num += 1
        print()

    print(f"{step_num}. Review the generated files:")
    print("   - docker-compose.yml (for Docker deployment)")
    print("   - config.toml (for local dev server)")
    step_num += 1
    print()

    print("For Docker deployment:")
    print(f"{step_num}. Build frontend: npm run build --prefix ./src/kohaku-hub-ui")
    step_num += 1
    print(f"{step_num}. Start services: docker-compose up -d")
    print()
    if config["lakefs_use_postgres"]:
        print("   Note: Databases will be created automatically on first startup:")
        print(f"   - {config['postgres_db']} (hub-api)")
        print(f"   - {config['lakefs_db']} (LakeFS)")
        print()
    step_num += 1
    print(f"{step_num}. Access at: http://localhost:28080")
    print()

    print("For local dev server:")
    print(
        f"{step_num}. Start infrastructure: docker-compose up -d postgres minio lakefs"
    )
    step_num += 1
    print(
        f"{step_num}. Run dev server: uvicorn kohakuhub.main:app --reload --port 48888"
    )
    step_num += 1
    print(f"{step_num}. Access at: http://localhost:48888")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAborted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nError: {e}")
        sys.exit(1)
