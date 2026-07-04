#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
OUT_DIR="${JOB_HUNTER_OUT_DIR:-${PROJECT_ROOT}/outputs}"
REVIEW_DIR="${JOB_HUNTER_REVIEW_DIR:-${PROJECT_ROOT}/review}"
DB_PATH="${JOB_HUNTER_DB_PATH:-${PROJECT_ROOT}/data/job_hunter.db}"
CONFIG_DIR="${JOB_HUNTER_CONFIG_DIR:-${PROJECT_ROOT}/config}"
NOTIFY_DESKTOP="${JOB_HUNTER_NOTIFY_DESKTOP:-1}"

mkdir -p "${OUT_DIR}" "${REVIEW_DIR}" "${PROJECT_ROOT}/logs" "${PROJECT_ROOT}/data"

send_desktop_notification() {
  if [[ "${NOTIFY_DESKTOP}" == "0" ]] || [[ ! -x "/usr/bin/osascript" ]]; then
    return 0
  fi
  local title="$1"
  local subtitle="$2"
  local message="$3"
  local sound="${4:-Glass}"
  local escaped_title="${title//\"/\\\"}"
  local escaped_subtitle="${subtitle//\"/\\\"}"
  local escaped_message="${message//\"/\\\"}"
  local escaped_sound="${sound//\"/\\\"}"
  /usr/bin/osascript <<APPLESCRIPT >/dev/null 2>&1 || true
display notification "${escaped_message}" with title "${escaped_title}" subtitle "${escaped_subtitle}" sound name "${escaped_sound}"
APPLESCRIPT
}

set +e
PYTHONPATH="${PROJECT_ROOT}/src" /usr/bin/python3 -m job_hunter_agent.main \
  --out-dir "${OUT_DIR}" \
  --review-dir "${REVIEW_DIR}" \
  --db-path "${DB_PATH}" \
  --config-dir "${CONFIG_DIR}"
run_exit_code=$?
set -e

if [[ "${run_exit_code}" -eq 0 ]]; then
  send_desktop_notification \
    "Job Hunter Daily Run" \
    "Completed successfully" \
    "Artifacts updated in ${REVIEW_DIR}. Open latest_live_jobs.html for review."
else
  send_desktop_notification \
    "Job Hunter Daily Run" \
    "Failed (exit ${run_exit_code})" \
    "Check ${PROJECT_ROOT}/logs/job_hunter_runner.err.log for details." \
    "Basso"
fi

exit "${run_exit_code}"
