#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
OUT_DIR="${JOB_HUNTER_OUT_DIR:-${PROJECT_ROOT}/outputs}"
REVIEW_DIR="${JOB_HUNTER_REVIEW_DIR:-${HOME}/Desktop/Job Hunt Morning Review}"
DB_PATH="${JOB_HUNTER_DB_PATH:-${PROJECT_ROOT}/data/job_hunter.db}"
CONFIG_DIR="${JOB_HUNTER_CONFIG_DIR:-${PROJECT_ROOT}/config}"

mkdir -p "${OUT_DIR}" "${PROJECT_ROOT}/logs" "${PROJECT_ROOT}/data"

PYTHONPATH="${PROJECT_ROOT}/src" /usr/bin/python3 -m job_hunter_agent.main \
  --out-dir "${OUT_DIR}" \
  --review-dir "${REVIEW_DIR}" \
  --db-path "${DB_PATH}" \
  --config-dir "${CONFIG_DIR}"
