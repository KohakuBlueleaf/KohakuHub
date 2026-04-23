#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.dev"
LAKEFS_CREDENTIALS_FILE="${ROOT_DIR}/hub-meta/dev/lakefs/credentials.env"

warn_red() {
  printf '\033[1;31m%s\033[0m\n' "$1"
}

warn_red "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
warn_red "!! DANGER: THIS IRREVERSIBLY CLEARS LOCAL KOHAKUHUB DEV DATA !!"
warn_red "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
warn_red "This clears through the local reset helper:"
warn_red "  - application data in PostgreSQL"
warn_red "  - all objects in the local S3 bucket"
warn_red "  - all LakeFS repositories in the local dev instance"
warn_red "  - the local demo seed manifest"
warn_red ""
warn_red "Consequence:"
warn_red "  - all local accounts, repos, orgs, commits, likes, and download stats are lost"
warn_red "  - the Docker bind-mount directories are kept in place"
warn_red "  - local infra stays running so you can re-seed immediately"
warn_red ""
warn_red ".env.dev and persisted LakeFS credentials are NOT removed."
echo

read -r -p "Continue with local reset? [y/N]: " confirmation
if [[ ! "${confirmation}" =~ ^[Yy]$ ]]; then
  echo "Aborted. Local data was not changed."
  exit 0
fi

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

"${ROOT_DIR}/scripts/dev/up_infra.sh"
"${ROOT_DIR}/scripts/dev/run_backend.sh" --prepare-only --skip-seed

if [[ ! -f "${LAKEFS_CREDENTIALS_FILE}" ]]; then
  echo "Missing ${LAKEFS_CREDENTIALS_FILE}"
  echo "LakeFS bootstrap did not produce reusable credentials."
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${LAKEFS_CREDENTIALS_FILE}"
set +a

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/dev/reset_local_data_direct.py"
