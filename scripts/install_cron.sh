#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
SCHEDULE="${JOB_HUNTER_CRON_SCHEDULE:-0 9 * * *}"
CRON_COMMAND="/bin/zsh ${PROJECT_ROOT}/scripts/run_daily.sh >> ${PROJECT_ROOT}/logs/job_hunter_cron.out.log 2>> ${PROJECT_ROOT}/logs/job_hunter_cron.err.log"
CRON_LINE="${SCHEDULE} ${CRON_COMMAND}"
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: scripts/install_cron.sh [--dry-run]

Installs the Job Hunter daily run cron entry for this clone path.

Options:
  --dry-run   Print the cron entry that would be installed without changing crontab.

Environment:
  JOB_HUNTER_CRON_SCHEDULE  Override default schedule (default: "0 9 * * *").
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

mkdir -p "${PROJECT_ROOT}/logs"

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "${CRON_LINE}"
  exit 0
fi

existing_crontab="$(crontab -l 2>/dev/null || true)"
filtered_crontab="$(printf '%s\n' "${existing_crontab}" | grep -Fv "${PROJECT_ROOT}/scripts/run_daily.sh" || true)"

{
  if [[ -n "${filtered_crontab}" ]]; then
    printf '%s\n' "${filtered_crontab}"
  fi
  printf '%s\n' "${CRON_LINE}"
} | crontab -

echo "Installed cron entry:"
crontab -l | grep -F "${PROJECT_ROOT}/scripts/run_daily.sh"
