#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.dev"
LAKEFS_CREDENTIALS_FILE="${ROOT_DIR}/hub-meta/dev/lakefs/credentials.env"
PREPARE_ONLY=false
SKIP_SEED=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prepare-only)
      PREPARE_ONLY=true
      ;;
    --skip-seed)
      SKIP_SEED=true
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: $0 [--prepare-only] [--skip-seed]"
      exit 1
      ;;
  esac
  shift
done

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}"
  echo "Create it first: cp .env.dev.example .env.dev"
  exit 1
fi

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-python}"
elif [[ -x "${ROOT_DIR}/venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

# LakeFS bootstrap credentials are only returned once, so persist and reuse them locally.
mkdir -p "${ROOT_DIR}/hub-meta/dev/lakefs"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/dev/init_lakefs.py" \
  --credentials-file "${LAKEFS_CREDENTIALS_FILE}"

if [[ -f "${LAKEFS_CREDENTIALS_FILE}" ]]; then
  # Source the persisted credentials so the local backend can talk to LakeFS directly.
  set -a
  # shellcheck disable=SC1090
  source "${LAKEFS_CREDENTIALS_FILE}"
  set +a
fi

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/run_migrations.py"

if [[ "${KOHAKU_HUB_DEV_AUTO_SEED:-true}" == "true" && "${SKIP_SEED}" != "true" ]]; then
  # Keep local demo data creation on the same bootstrap path as normal backend startup.
  "${PYTHON_BIN}" "${ROOT_DIR}/scripts/dev/seed_demo_data.py"
fi

if [[ "${PREPARE_ONLY}" == "true" ]]; then
  echo "Backend bootstrap completed."
  exit 0
fi

exec "${PYTHON_BIN}" -m uvicorn kohakuhub.main:app \
  --reload \
  --host 0.0.0.0 \
  --port 48888
