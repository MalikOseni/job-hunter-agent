#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
OUT_DIR="${JOB_HUNTER_OUT_DIR:-${PROJECT_ROOT}/outputs}"
REVIEW_DIR="${JOB_HUNTER_REVIEW_DIR:-${PROJECT_ROOT}/review}"
DB_PATH="${JOB_HUNTER_DB_PATH:-${PROJECT_ROOT}/data/job_hunter.db}"
CONFIG_DIR="${JOB_HUNTER_CONFIG_DIR:-${PROJECT_ROOT}/config}"
NOTIFY_DESKTOP="${JOB_HUNTER_NOTIFY_DESKTOP:-1}"
SEND_DAILY_EMAIL="${JOB_HUNTER_SEND_DAILY_EMAIL:-1}"
REPORT_EMAIL="${JOB_HUNTER_REPORT_EMAIL:-malik@malikoseni.com}"

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
generate_summary_report() {
  local summary_file="${REVIEW_DIR}/latest_job_findings_summary.txt"
  /usr/bin/python3 - "${REVIEW_DIR}" "${PROJECT_ROOT}/logs" "${summary_file}" <<'PY'
from pathlib import Path
import csv
import re
import sys
from collections import Counter
from datetime import datetime

review_dir = Path(sys.argv[1])
logs_dir = Path(sys.argv[2])
summary_path = Path(sys.argv[3])
live_csv = review_dir / "latest_live_jobs.csv"
status_csv = review_dir / "latest_application_status.csv"

if not live_csv.exists() or not status_csv.exists():
    missing = [str(p) for p in (live_csv, status_csv) if not p.exists()]
    raise SystemExit(f"Missing report inputs: {', '.join(missing)}")

live_rows = list(csv.DictReader(live_csv.open(encoding="utf-8")))
status_rows = list(csv.DictReader(status_csv.open(encoding="utf-8")))
company_counts = Counter((r.get("company") or "").strip().lower() for r in live_rows)
source_counts = Counter((r.get("source") or "").strip().lower() for r in live_rows)
location_counts = Counter((r.get("location") or "").strip() for r in live_rows)
status_counts = Counter((r.get("status") or "").strip() for r in status_rows)

remote_count = sum(1 for r in live_rows if "remote" in (r.get("tags") or "").lower())
visa_count = sum(1 for r in live_rows if "visa/relocation" in (r.get("tags") or "").lower())
work_anywhere_count = sum(1 for r in live_rows if "work-anywhere" in (r.get("tags") or "").lower())
emea_remote_count = sum(1 for r in live_rows if "emea-remote" in (r.get("tags") or "").lower())
target_country_count = sum(1 for r in live_rows if "target-country" in (r.get("tags") or "").lower())

ngo_companies = {"mozilla", "khanacademy", "codeforamerica", "aclu", "humanrightswatch", "wri"}
ngo_rows = [r for r in live_rows if (r.get("company") or "").strip().lower() in ngo_companies]

run_id = "unknown"
for log_name in ("job_hunter_cron.out.log", "job_hunter_runner.out.log"):
    log_path = logs_dir / log_name
    if not log_path.exists():
        continue
    text = log_path.read_text(encoding="utf-8", errors="replace")
    ids = re.findall(r"run_id=([^,\s]+)", text)
    if ids:
        run_id = ids[-1]

lines = [
    "Job Hunter - Latest Findings Summary",
    f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    f"Run ID: {run_id}",
    "",
    "High-level metrics",
    f"- Shortlisted roles: {len(live_rows)}",
    f"- Tracked opportunities (status table): {len(status_rows)}",
    f"- Unique companies in shortlist: {len(company_counts)}",
    f"- Unique sources in shortlist: {len(source_counts)}",
    "",
    "Mobility and location signal",
    f"- Remote-tagged roles: {remote_count}",
    f"- Visa/relocation-tagged roles: {visa_count}",
    f"- Work-anywhere-tagged roles: {work_anywhere_count}",
    f"- EMEA-remote-tagged roles: {emea_remote_count}",
    f"- Target-country-tagged roles: {target_country_count}",
    "",
    "Top companies (shortlist)",
]
for company, count in company_counts.most_common(12):
    lines.append(f"- {company}: {count}")

lines.append("")
lines.append("Top source boards (shortlist)")
for source, count in source_counts.most_common(12):
    lines.append(f"- {source}: {count}")

lines.append("")
lines.append("Top locations (shortlist)")
for location, count in location_counts.most_common(12):
    lines.append(f"- {location}: {count}")

lines.append("")
lines.append("Status distribution")
for status, count in sorted(status_counts.items()):
    lines.append(f"- {status}: {count}")

lines.append("")
lines.append(f"NGO/mission-driven roles surfaced: {len(ngo_rows)}")
for row in ngo_rows[:10]:
    lines.append(f"- {row.get('company')} | {row.get('title')} | {row.get('location')} | {row.get('tags')}")

summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

send_daily_email_report() {
  local summary_file="${REVIEW_DIR}/latest_job_findings_summary.txt"
  local shortlist_csv="${REVIEW_DIR}/latest_live_jobs.csv"
  local status_csv="${REVIEW_DIR}/latest_application_status.csv"

  if [[ "${SEND_DAILY_EMAIL}" == "0" ]]; then
    echo "Daily email report skipped (JOB_HUNTER_SEND_DAILY_EMAIL=0)."
    return 0
  fi

  if [[ -z "${REPORT_EMAIL}" ]]; then
    echo "Daily email report skipped (JOB_HUNTER_REPORT_EMAIL not set)." >&2
    return 0
  fi

  /usr/bin/python3 - "${REPORT_EMAIL}" "${summary_file}" "${shortlist_csv}" "${status_csv}" <<'PY'
from email.message import EmailMessage
from pathlib import Path
import datetime
import mimetypes
import subprocess
import sys

recipient = sys.argv[1]
summary_path = Path(sys.argv[2])
attachments = [Path(sys.argv[3]), Path(sys.argv[4])]

if not summary_path.exists():
    raise SystemExit(f"Summary file not found: {summary_path}")

if not Path("/usr/sbin/sendmail").exists():
    raise SystemExit("sendmail is not available at /usr/sbin/sendmail")

subject_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
message = EmailMessage()
message["To"] = recipient
message["From"] = recipient
message["Subject"] = f"Job Hunter Daily Findings Summary {subject_time}"
message.set_content(summary_path.read_text(encoding="utf-8", errors="replace"))

for file_path in attachments:
    if not file_path.exists():
        continue
    ctype, _ = mimetypes.guess_type(file_path.name)
    if ctype is None:
        maintype, subtype = "application", "octet-stream"
    else:
        maintype, subtype = ctype.split("/", 1)
    message.add_attachment(
        file_path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=file_path.name,
    )

subprocess.run(
    ["/usr/sbin/sendmail", "-t", "-oi"],
    input=message.as_bytes(),
    check=True,
)
PY
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
  email_status="sent"
  if generate_summary_report && send_daily_email_report; then
    echo "Daily email report sent to ${REPORT_EMAIL} with attachments: latest_live_jobs.csv, latest_application_status.csv."
  else
    email_status="failed"
    echo "Daily email report failed. Check runner logs for details." >&2
  fi
  send_desktop_notification \
    "Job Hunter Daily Run" \
    "Completed successfully" \
    "Artifacts updated in ${REVIEW_DIR}. Email report status: ${email_status}."
else
  send_desktop_notification \
    "Job Hunter Daily Run" \
    "Failed (exit ${run_exit_code})" \
    "Check ${PROJECT_ROOT}/logs/job_hunter_runner.err.log for details." \
    "Basso"
fi

exit "${run_exit_code}"
