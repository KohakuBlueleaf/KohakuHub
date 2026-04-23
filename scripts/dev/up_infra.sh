#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.dev"

NETWORK_NAME="kohakuhub-dev"
POSTGRES_CONTAINER="kohakuhub-dev-postgres"
MINIO_CONTAINER="kohakuhub-dev-minio"
LAKEFS_CONTAINER="kohakuhub-dev-lakefs"

POSTGRES_DATA_DIR="${ROOT_DIR}/hub-meta/dev/postgres-data"
MINIO_DATA_DIR="${ROOT_DIR}/hub-meta/dev/minio-data"
MINIO_CONFIG_DIR="${ROOT_DIR}/hub-meta/dev/minio-config"
LAKEFS_DATA_DIR="${ROOT_DIR}/hub-meta/dev/lakefs-data"
LAKEFS_CACHE_DIR="${ROOT_DIR}/hub-meta/dev/lakefs-cache"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

: "${DEV_POSTGRES_USER:=hub_dev}"
: "${DEV_POSTGRES_PASSWORD:=hub_dev_password}"
: "${DEV_POSTGRES_DB:=kohakuhub_dev}"
: "${DEV_MINIO_ROOT_USER:=minioadmin}"
: "${DEV_MINIO_ROOT_PASSWORD:=minioadmin}"
: "${DEV_LAKEFS_ENCRYPT_SECRET_KEY:=dev-lakefs-encrypt-key-32chars}"
: "${KOHAKU_HUB_S3_BUCKET:=hub-storage}"
: "${KOHAKU_HUB_S3_REGION:=us-east-1}"
: "${KOHAKU_HUB_S3_ACCESS_KEY:=${DEV_MINIO_ROOT_USER}}"
: "${KOHAKU_HUB_S3_SECRET_KEY:=${DEV_MINIO_ROOT_PASSWORD}}"

container_exists() {
  docker ps -a --format '{{.Names}}' | grep -Fxq "$1"
}

container_running() {
  docker ps --format '{{.Names}}' | grep -Fxq "$1"
}

ensure_network() {
  if ! docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
    docker network create "${NETWORK_NAME}" >/dev/null
    echo "Created network ${NETWORK_NAME}"
  fi
}

ensure_directories() {
  # Keep state on the host so recreating containers does not reset local dev data.
  mkdir -p \
    "${POSTGRES_DATA_DIR}" \
    "${MINIO_DATA_DIR}" \
    "${MINIO_CONFIG_DIR}" \
    "${LAKEFS_DATA_DIR}" \
    "${LAKEFS_CACHE_DIR}"
}

ensure_postgres() {
  if container_running "${POSTGRES_CONTAINER}"; then
    echo "Postgres already running"
    return
  fi

  if container_exists "${POSTGRES_CONTAINER}"; then
    docker start "${POSTGRES_CONTAINER}" >/dev/null
    echo "Started existing ${POSTGRES_CONTAINER}"
    return
  fi

  docker run -d \
    --name "${POSTGRES_CONTAINER}" \
    --network "${NETWORK_NAME}" \
    -p 25432:5432 \
    -e POSTGRES_USER="${DEV_POSTGRES_USER}" \
    -e POSTGRES_PASSWORD="${DEV_POSTGRES_PASSWORD}" \
    -e POSTGRES_DB="${DEV_POSTGRES_DB}" \
    -v "${POSTGRES_DATA_DIR}:/var/lib/postgresql/data" \
    postgres:15 >/dev/null

  echo "Created ${POSTGRES_CONTAINER}"
}

ensure_minio() {
  if container_running "${MINIO_CONTAINER}"; then
    echo "MinIO already running"
    return
  fi

  if container_exists "${MINIO_CONTAINER}"; then
    docker start "${MINIO_CONTAINER}" >/dev/null
    echo "Started existing ${MINIO_CONTAINER}"
    return
  fi

  docker run -d \
    --name "${MINIO_CONTAINER}" \
    --network "${NETWORK_NAME}" \
    -p 29001:9000 \
    -p 29000:29000 \
    -e MINIO_ROOT_USER="${DEV_MINIO_ROOT_USER}" \
    -e MINIO_ROOT_PASSWORD="${DEV_MINIO_ROOT_PASSWORD}" \
    -v "${MINIO_DATA_DIR}:/data" \
    -v "${MINIO_CONFIG_DIR}:/root/.minio" \
    quay.io/minio/minio:latest \
    server /data --console-address ":29000" >/dev/null

  echo "Created ${MINIO_CONTAINER}"
}

ensure_lakefs() {
  if container_running "${LAKEFS_CONTAINER}"; then
    echo "LakeFS already running"
    return
  fi

  if container_exists "${LAKEFS_CONTAINER}"; then
    # LakeFS config is stateless here; recreate the container and keep the bind-mounted data.
    docker rm -f "${LAKEFS_CONTAINER}" >/dev/null
    echo "Recreated ${LAKEFS_CONTAINER}"
  fi

  # Match the host user so LakeFS can write to the persisted metadata directory.
  docker run -d \
    --name "${LAKEFS_CONTAINER}" \
    --network "${NETWORK_NAME}" \
    --user "$(id -u):$(id -g)" \
    -p 28000:28000 \
    -e LAKEFS_DATABASE_TYPE=local \
    -e LAKEFS_DATABASE_LOCAL_PATH=/var/lakefs/data/metadata.db \
    -e LAKEFS_BLOCKSTORE_TYPE=s3 \
    -e "LAKEFS_BLOCKSTORE_S3_ENDPOINT=http://${MINIO_CONTAINER}:9000" \
    -e "LAKEFS_BLOCKSTORE_S3_BUCKET=${KOHAKU_HUB_S3_BUCKET}" \
    -e LAKEFS_BLOCKSTORE_S3_FORCE_PATH_STYLE=true \
    -e "LAKEFS_BLOCKSTORE_S3_CREDENTIALS_ACCESS_KEY_ID=${KOHAKU_HUB_S3_ACCESS_KEY}" \
    -e "LAKEFS_BLOCKSTORE_S3_CREDENTIALS_SECRET_ACCESS_KEY=${KOHAKU_HUB_S3_SECRET_KEY}" \
    -e "LAKEFS_BLOCKSTORE_S3_REGION=${KOHAKU_HUB_S3_REGION}" \
    -e "LAKEFS_AUTH_ENCRYPT_SECRET_KEY=${DEV_LAKEFS_ENCRYPT_SECRET_KEY}" \
    -e LAKEFS_LOGGING_FORMAT=text \
    -e LAKEFS_LISTEN_ADDRESS=0.0.0.0:28000 \
    -v "${LAKEFS_DATA_DIR}:/var/lakefs/data" \
    -v "${LAKEFS_CACHE_DIR}:/lakefs/data/cache" \
    treeverse/lakefs:latest >/dev/null

  echo "Created ${LAKEFS_CONTAINER}"
}

wait_for_postgres() {
  until docker exec "${POSTGRES_CONTAINER}" pg_isready -U "${DEV_POSTGRES_USER}" -d "${DEV_POSTGRES_DB}" >/dev/null 2>&1; do
    sleep 1
  done
}

wait_for_http() {
  local url="$1"
  until curl -fsS "${url}" >/dev/null 2>&1; do
    sleep 1
  done
}

ensure_network
ensure_directories
ensure_postgres
ensure_minio
ensure_lakefs

wait_for_postgres
wait_for_http "http://127.0.0.1:29001/minio/health/live"
wait_for_http "http://127.0.0.1:28000/_health"

cat <<EOF
Infra is ready.

Postgres: postgresql://${DEV_POSTGRES_USER}:${DEV_POSTGRES_PASSWORD}@127.0.0.1:25432/${DEV_POSTGRES_DB}
MinIO API: http://127.0.0.1:29001
MinIO Console: http://127.0.0.1:29000
LakeFS: http://127.0.0.1:28000

Next:
  1. cp .env.dev.example .env.dev
  2. ./scripts/dev/run_backend.sh
EOF
