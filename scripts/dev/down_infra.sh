#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="kohakuhub-dev"
CONTAINERS=(
  "kohakuhub-dev-lakefs"
  "kohakuhub-dev-minio"
  "kohakuhub-dev-postgres"
)

container_exists() {
  docker ps -a --format '{{.Names}}' | grep -Fxq "$1"
}

for container in "${CONTAINERS[@]}"; do
  if container_exists "${container}"; then
    docker rm -f "${container}" >/dev/null
    echo "Removed ${container}"
  fi
done

if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
  docker network rm "${NETWORK_NAME}" >/dev/null || true
fi

echo "Infra stopped. Persistent data is still under hub-meta/dev/."
