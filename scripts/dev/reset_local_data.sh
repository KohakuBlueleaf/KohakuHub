#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_DIR="${ROOT_DIR}/hub-meta/dev"
CONFIRM_PHRASE="DELETE KOHAKUHUB DEV DATA"

warn_red() {
  printf '\033[1;31m%s\033[0m\n' "$1"
}

warn_red "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
warn_red "!! DANGER: THIS PERMANENTLY DELETES LOCAL KOHAKUHUB DEV DATA !!"
warn_red "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
warn_red "This removes:"
warn_red "  - PostgreSQL data under hub-meta/dev/postgres-data"
warn_red "  - MinIO objects under hub-meta/dev/minio-data"
warn_red "  - LakeFS metadata/cache under hub-meta/dev/lakefs-*"
warn_red "  - persisted bootstrap credentials and demo seed manifest"
warn_red ""
warn_red "Consequence:"
warn_red "  - all local accounts, repos, orgs, commits, likes, and download stats are lost"
warn_red "  - next bootstrap behaves like a brand new environment"
warn_red ""
warn_red ".env.dev is NOT removed."
echo

read -r -p "Type '${CONFIRM_PHRASE}' to continue: " first_confirmation
if [[ "${first_confirmation}" != "${CONFIRM_PHRASE}" ]]; then
  echo "Aborted. Local data was not changed."
  exit 1
fi

echo
warn_red "SECOND CONFIRMATION REQUIRED"
read -r -p "Type '${CONFIRM_PHRASE}' again to permanently delete the local data: " second_confirmation
if [[ "${second_confirmation}" != "${CONFIRM_PHRASE}" ]]; then
  echo "Aborted. Local data was not changed."
  exit 1
fi

"${ROOT_DIR}/scripts/dev/down_infra.sh"

if [[ -d "${DATA_DIR}" ]]; then
  rm -rf "${DATA_DIR}"
  echo "Removed ${DATA_DIR}"
else
  echo "No persisted local dev data found at ${DATA_DIR}"
fi

echo "Local KohakuHub dev data has been cleared."
