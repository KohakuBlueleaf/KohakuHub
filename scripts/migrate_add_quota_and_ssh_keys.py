"""Database migration: Add quota columns and SSH keys table.

This migration adds:
1. Quota columns to User and Organization tables
2. SSHKey table for Git SSH authentication

Run this script to update your database schema.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kohakuhub.config import cfg
from kohakuhub.db import db
from kohakuhub.logger import get_logger

logger = get_logger("MIGRATION")


def migrate():
    """Run database migration."""
    db.connect(reuse_if_open=True)

    logger.info("Starting database migration...")

    # Check database backend
    if cfg.app.db_backend == "postgres":
        logger.info("Detected PostgreSQL database")
        migrate_postgres()
    else:
        logger.info("Detected SQLite database")
        migrate_sqlite()

    logger.success("Migration completed successfully!")


def migrate_postgres():
    """Migrate PostgreSQL database."""

    # 1. Add quota columns to User table
    logger.info("Adding quota columns to User table...")

    try:
        db.execute_sql("""
            ALTER TABLE "user"
            ADD COLUMN IF NOT EXISTS private_quota_bytes BIGINT DEFAULT NULL
        """)
        logger.success("Added private_quota_bytes to User")
    except Exception as e:
        logger.warning(f"Column private_quota_bytes might already exist: {e}")

    try:
        db.execute_sql("""
            ALTER TABLE "user"
            ADD COLUMN IF NOT EXISTS public_quota_bytes BIGINT DEFAULT NULL
        """)
        logger.success("Added public_quota_bytes to User")
    except Exception as e:
        logger.warning(f"Column public_quota_bytes might already exist: {e}")

    try:
        db.execute_sql("""
            ALTER TABLE "user"
            ADD COLUMN IF NOT EXISTS private_used_bytes BIGINT DEFAULT 0
        """)
        logger.success("Added private_used_bytes to User")
    except Exception as e:
        logger.warning(f"Column private_used_bytes might already exist: {e}")

    try:
        db.execute_sql("""
            ALTER TABLE "user"
            ADD COLUMN IF NOT EXISTS public_used_bytes BIGINT DEFAULT 0
        """)
        logger.success("Added public_used_bytes to User")
    except Exception as e:
        logger.warning(f"Column public_used_bytes might already exist: {e}")

    # 2. Add quota columns to Organization table
    logger.info("Adding quota columns to Organization table...")

    try:
        db.execute_sql("""
            ALTER TABLE organization
            ADD COLUMN IF NOT EXISTS private_quota_bytes BIGINT DEFAULT NULL
        """)
        logger.success("Added private_quota_bytes to Organization")
    except Exception as e:
        logger.warning(f"Column private_quota_bytes might already exist: {e}")

    try:
        db.execute_sql("""
            ALTER TABLE organization
            ADD COLUMN IF NOT EXISTS public_quota_bytes BIGINT DEFAULT NULL
        """)
        logger.success("Added public_quota_bytes to Organization")
    except Exception as e:
        logger.warning(f"Column public_quota_bytes might already exist: {e}")

    try:
        db.execute_sql("""
            ALTER TABLE organization
            ADD COLUMN IF NOT EXISTS private_used_bytes BIGINT DEFAULT 0
        """)
        logger.success("Added private_used_bytes to Organization")
    except Exception as e:
        logger.warning(f"Column private_used_bytes might already exist: {e}")

    try:
        db.execute_sql("""
            ALTER TABLE organization
            ADD COLUMN IF NOT EXISTS public_used_bytes BIGINT DEFAULT 0
        """)
        logger.success("Added public_used_bytes to Organization")
    except Exception as e:
        logger.warning(f"Column public_used_bytes might already exist: {e}")

    # 3. Create SSHKey table
    logger.info("Creating SSHKey table...")

    try:
        db.execute_sql("""
            CREATE TABLE IF NOT EXISTS sshkey (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                key_type VARCHAR(255) NOT NULL,
                public_key TEXT NOT NULL,
                fingerprint VARCHAR(255) NOT NULL UNIQUE,
                title VARCHAR(255) NOT NULL,
                last_used TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.success("Created SSHKey table")
    except Exception as e:
        logger.warning(f"SSHKey table might already exist: {e}")

    # Create indexes
    try:
        db.execute_sql("""
            CREATE INDEX IF NOT EXISTS sshkey_user_id ON sshkey(user_id)
        """)
        logger.success("Created index on sshkey.user_id")
    except Exception as e:
        logger.warning(f"Index might already exist: {e}")

    try:
        db.execute_sql("""
            CREATE INDEX IF NOT EXISTS sshkey_fingerprint ON sshkey(fingerprint)
        """)
        logger.success("Created index on sshkey.fingerprint")
    except Exception as e:
        logger.warning(f"Index might already exist: {e}")

    try:
        db.execute_sql("""
            CREATE UNIQUE INDEX IF NOT EXISTS sshkey_user_fingerprint
            ON sshkey(user_id, fingerprint)
        """)
        logger.success("Created unique index on sshkey(user_id, fingerprint)")
    except Exception as e:
        logger.warning(f"Index might already exist: {e}")


def migrate_sqlite():
    """Migrate SQLite database."""

    # SQLite doesn't support ADD COLUMN IF NOT EXISTS in older versions
    # We'll check if columns exist first

    # 1. Check and add quota columns to User table
    logger.info("Checking User table schema...")

    cursor = db.execute_sql("PRAGMA table_info(user)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    if "private_quota_bytes" not in existing_columns:
        logger.info("Adding private_quota_bytes to User...")
        db.execute_sql("ALTER TABLE user ADD COLUMN private_quota_bytes INTEGER DEFAULT NULL")
        logger.success("Added private_quota_bytes")

    if "public_quota_bytes" not in existing_columns:
        logger.info("Adding public_quota_bytes to User...")
        db.execute_sql("ALTER TABLE user ADD COLUMN public_quota_bytes INTEGER DEFAULT NULL")
        logger.success("Added public_quota_bytes")

    if "private_used_bytes" not in existing_columns:
        logger.info("Adding private_used_bytes to User...")
        db.execute_sql("ALTER TABLE user ADD COLUMN private_used_bytes INTEGER DEFAULT 0")
        logger.success("Added private_used_bytes")

    if "public_used_bytes" not in existing_columns:
        logger.info("Adding public_used_bytes to User...")
        db.execute_sql("ALTER TABLE user ADD COLUMN public_used_bytes INTEGER DEFAULT 0")
        logger.success("Added public_used_bytes")

    # 2. Check and add quota columns to Organization table
    logger.info("Checking Organization table schema...")

    cursor = db.execute_sql("PRAGMA table_info(organization)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    if "private_quota_bytes" not in existing_columns:
        logger.info("Adding private_quota_bytes to Organization...")
        db.execute_sql("ALTER TABLE organization ADD COLUMN private_quota_bytes INTEGER DEFAULT NULL")
        logger.success("Added private_quota_bytes")

    if "public_quota_bytes" not in existing_columns:
        logger.info("Adding public_quota_bytes to Organization...")
        db.execute_sql("ALTER TABLE organization ADD COLUMN public_quota_bytes INTEGER DEFAULT NULL")
        logger.success("Added public_quota_bytes")

    if "private_used_bytes" not in existing_columns:
        logger.info("Adding private_used_bytes to Organization...")
        db.execute_sql("ALTER TABLE organization ADD COLUMN private_used_bytes INTEGER DEFAULT 0")
        logger.success("Added private_used_bytes")

    if "public_used_bytes" not in existing_columns:
        logger.info("Adding public_used_bytes to Organization...")
        db.execute_sql("ALTER TABLE organization ADD COLUMN public_used_bytes INTEGER DEFAULT 0")
        logger.success("Added public_used_bytes")

    # 3. Create SSHKey table
    logger.info("Creating SSHKey table...")

    db.execute_sql("""
        CREATE TABLE IF NOT EXISTS sshkey (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            key_type VARCHAR(255) NOT NULL,
            public_key TEXT NOT NULL,
            fingerprint VARCHAR(255) NOT NULL UNIQUE,
            title VARCHAR(255) NOT NULL,
            last_used TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    logger.success("Created SSHKey table")

    # Create indexes
    db.execute_sql("CREATE INDEX IF NOT EXISTS sshkey_user_id ON sshkey(user_id)")
    db.execute_sql("CREATE INDEX IF NOT EXISTS sshkey_fingerprint ON sshkey(fingerprint)")
    db.execute_sql("CREATE UNIQUE INDEX IF NOT EXISTS sshkey_user_fingerprint ON sshkey(user_id, fingerprint)")
    logger.success("Created indexes on SSHKey table")


if __name__ == "__main__":
    migrate()
