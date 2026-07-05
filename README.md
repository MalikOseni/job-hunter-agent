# Job Hunter Agent
Relocation-first job sourcing and application-tracking pipeline with daily automation, sqlite persistence, and status dashboards.

## What this project does
- Aggregates jobs from multiple sources.
- Applies relocation and eligibility policy filters.
- Scores and shortlists jobs.
- Persists job/application state to sqlite.
- Generates daily review artifacts:
  - live jobs CSV/HTML
  - application status CSV/HTML/JSON dashboard
- Supports daily scheduled execution via cron.

## Repository layout
- `src/job_hunter_agent/` — core pipeline modules.
- `config/` — policy/profile/writing config.
- `sql/` — sqlite schema.
- `scripts/` — operational scripts (`run_daily.sh`, `init_db.py`).
- `tests/` — unit tests.
- `outputs/` — generated run artifacts/log CSV.
- `review/` — latest review-facing CSV/HTML/JSON outputs.
- `data/` — sqlite database.
- `logs/` — scheduler stdout/stderr logs.

## Prerequisites
- macOS or Linux shell environment.
- Python 3 (project metadata is `>=3.10`).
- `make` (optional, but recommended for common tasks).

## Setup
1. Clone and enter the repo.
2. Initialize the database schema:
   - `make init-db`
3. (Optional) configure credential environment variables for login/application flows:
   - Copy `.env.example` and export values in your shell.

Example:
- `export JOB_HUNTER_LOGIN_EMAIL="you@example.com"`
- `export JOB_HUNTER_LOGIN_PASSWORD="your-secure-password"`

## Configuration
Primary config files live in `config/`:
- `config/policy.yaml`
- `config/profile.yaml`
- `config/writing_rules.yaml`

Runtime directory overrides (used by `scripts/run_daily.sh`):
- `JOB_HUNTER_OUT_DIR` (default: `./outputs`)
- `JOB_HUNTER_REVIEW_DIR` (default: `./review`)
- `JOB_HUNTER_DB_PATH` (default: `./data/job_hunter.db`)
- `JOB_HUNTER_CONFIG_DIR` (default: `./config`)
- `JOB_HUNTER_NOTIFY_DESKTOP` (default: `1`; set `0` to disable macOS desktop notifications)
- `JOB_HUNTER_SEND_DAILY_EMAIL` (default: `1`; set `0` to disable daily email sending)
- `JOB_HUNTER_REPORT_EMAIL` (default: `malik@malikoseni.com`; recipient for daily summary emails)

CLI flags (module entrypoint):
- `--min-score`
- `--max-age-days`
- `--out-dir`
- `--review-dir`
- `--config-dir`
- `--db-path`

## Usage
### Quick commands
- Run once with defaults:
  - `make run`
- Run daily operational script:
  - `make run-daily`
- Run tests:
  - `make test`
- Lint/compile checks:
  - `make lint`

### Direct module execution
- `PYTHONPATH="$PWD/src" /usr/bin/python3 -m job_hunter_agent.main --out-dir "$PWD/outputs" --review-dir "$PWD/review" --db-path "$PWD/data/job_hunter.db" --config-dir "$PWD/config"`

## Daily automation (cron)
Example daily cron entry (09:00 local time):
- `0 9 * * * /bin/zsh /Users/malikoseni/job-hunter-agent/scripts/run_daily.sh >> /Users/malikoseni/job-hunter-agent/logs/job_hunter_cron.out.log 2>> /Users/malikoseni/job-hunter-agent/logs/job_hunter_cron.err.log`

Desktop notifications:
- Each run sends a macOS notification on completion:
  - success: artifacts updated in `review/` (includes email status)
  - failure: points to `logs/job_hunter_runner.err.log`

Daily email reports:
- On successful runs, `scripts/run_daily.sh` generates `review/latest_job_findings_summary.txt`.
- The script emails that summary and attaches:
  - `review/latest_live_jobs.csv`
  - `review/latest_application_status.csv`
- Email errors are logged but do not fail the pipeline run.

## Generated outputs
### `outputs/`
- `job_matches_YYYY-MM-DD.csv`
- `job_matches_YYYY-MM-DD.html`
- `live_jobs_log.csv`

### `review/`
- `latest_live_jobs.csv`
- `latest_live_jobs.html`
- `live_jobs_YYYY-MM-DD.csv`
- `live_jobs_YYYY-MM-DD.html`
- `latest_application_status.csv`
- `latest_application_status.html`
- `latest_application_status.json`
- `latest_job_findings_summary.txt`
- `application_status_YYYY-MM-DD.csv`
- `application_status_YYYY-MM-DD.html`
- `application_status_YYYY-MM-DD.json`

### `logs/`
- `job_hunter_runner.out.log`
- `job_hunter_runner.err.log`
- `job_hunter_cron.out.log`
- `job_hunter_cron.err.log`

## Monitoring and verification
- Follow logs live:
  - `tail -n 100 -f logs/job_hunter_runner.out.log logs/job_hunter_runner.err.log`
  - `tail -n 100 -f logs/job_hunter_cron.out.log logs/job_hunter_cron.err.log`
- Scan errors:
  - `grep -niE "error|traceback|failed|exception|permission" logs/job_hunter_runner.err.log`
- Confirm successful run markers:
  - `grep -n "Queue DB updated" logs/job_hunter_runner.out.log`
  - `grep -n "Status dashboard updated" logs/job_hunter_runner.out.log`
- Check latest review artifacts:
  - `ls -lt review/latest_*`

## Notes
- External job boards may occasionally return 404s; those entries are skipped and logged.
- Missing login credentials are reported as warnings unless account-driven flows are required.
