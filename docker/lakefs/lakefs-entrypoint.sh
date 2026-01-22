#!/bin/sh
# LakeFS entrypoint wrapper
# Runs database initialization before starting LakeFS

set -e

# PostgreSQL client must be installed in the image (for database initialization)
if ! command -v psql >/dev/null 2>&1; then
    echo "⚠ psql not available. Build the lakefs image once before running."
    echo "  Example: docker-compose build lakefs"
fi

# Run database initialization if PostgreSQL is configured and script exists
if [ -f /scripts/init-databases.sh ]; then
    if command -v psql >/dev/null 2>&1; then
        echo "Running database initialization..."
        sh /scripts/init-databases.sh || echo "Database initialization failed (continuing anyway)"
    else
        echo "psql not available, skipping database initialization"
        echo "  Please ensure databases exist manually:"
        echo "  - ${POSTGRES_DB:-kohakuhub}"
        echo "  - ${LAKEFS_DB:-lakefs}"
    fi
else
    echo "/scripts/init-databases.sh not found, skipping database initialization"
fi

# Start LakeFS with original command
echo "Starting LakeFS..."
exec /app/lakefs "$@"
