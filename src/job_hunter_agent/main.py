from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import ConfigError, load_runtime_settings
from .dashboard import update_live_jobs_html_with_status_section, write_status_dashboard
from .db import initialize_database, open_database, resolve_db_path
from .eligibility import evaluate_jobs, summarize_reason_counts
from .export import fetch_status_rows, write_status_exports
from .policy_engine import PolicyEngine, RunPolicySnapshot
from .repository import JobRepository
from .reporting import write_reports
from .salary_policy import build_salary_decisions, summarize_salary_decisions
from .secrets import AccountCredentials, load_account_credentials
from .sources import run_all_sweeps
from .types import JobRecord

DEFAULT_REVIEW_DIR = Path.home() / "Desktop" / "Job Hunt Morning Review"


def resolve_out_dir(cli_out_dir: Path | None) -> Path:
    if cli_out_dir is not None:
        return cli_out_dir
    env_value = os.environ.get("JOB_HUNTER_OUT_DIR")
    if env_value:
        return Path(env_value).expanduser()
    return Path.cwd()


def resolve_review_dir(cli_review_dir: Path | None) -> Path:
    if cli_review_dir is not None:
        return cli_review_dir
    env_value = os.environ.get("JOB_HUNTER_REVIEW_DIR")
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_REVIEW_DIR


def _print_runtime_policy_summary(policy_snapshot: RunPolicySnapshot, candidate_name: str) -> None:
    print(
        "Policy config loaded: "
        f"candidate={candidate_name}; "
        f"min_score={policy_snapshot.min_score}; "
        f"max_age_days={policy_snapshot.max_age_days}; "
        f"salary={policy_snapshot.salary_default_offer_strategy} "
        f"(fallback={policy_snapshot.salary_fallback_strategy}, "
        f"currency={policy_snapshot.salary_currency}); "
        f"default_notice_weeks={policy_snapshot.notice_default_weeks}"
    )


def _print_credential_status(credentials: AccountCredentials) -> None:
    if credentials.is_complete:
        print("Credentials loaded from environment variables.")
    else:
        missing = ", ".join(credentials.missing_env_vars)
        print(f"Credentials not set (missing: {missing}).")


def run(
    min_score: int | None = None,
    max_age_days: int | None = None,
    out_dir: Path | None = None,
    review_dir: Path | None = None,
    config_dir: Path | None = None,
    db_path: Path | None = None,
) -> list[JobRecord]:
    run_started_at = datetime.now(timezone.utc)
    run_id = f"run-{run_started_at.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    settings = load_runtime_settings(config_dir=config_dir)
    policy_engine = PolicyEngine(
        policy=settings.policy,
        profile=settings.profile,
        writing_rules=settings.writing_rules,
    )
    credentials = load_account_credentials(settings.profile)
    policy_snapshot = policy_engine.policy_snapshot(
        cli_min_score=min_score,
        cli_max_age_days=max_age_days,
    )

    _print_runtime_policy_summary(policy_snapshot, settings.profile.full_name)
    _print_credential_status(credentials)

    resolved_out_dir = resolve_out_dir(out_dir)
    resolved_review_dir = resolve_review_dir(review_dir)
    jobs: list[JobRecord] = []
    run_all_sweeps(jobs)
    policy_filtered_jobs = policy_engine.apply_relocation_rules(jobs)
    eligibility_evaluation = evaluate_jobs(
        policy_filtered_jobs,
        min_score=policy_snapshot.min_score,
        max_age_days=policy_snapshot.max_age_days,
        relocation_assistance_required=settings.profile.relocation_assistance_required,
    )
    eligible_jobs = eligibility_evaluation.eligible_jobs
    print(
        "Eligibility results: "
        f"eligible={len(eligible_jobs)}, "
        f"ineligible={len(eligibility_evaluation.ineligible_jobs)}; "
        f"reasons={summarize_reason_counts(eligibility_evaluation.reason_counts)}"
    )
    top = write_reports(
        eligible_jobs,
        0,
        policy_snapshot.max_age_days,
        resolved_out_dir,
        resolved_review_dir,
    )
    salary_decisions = build_salary_decisions(
        top,
        policy=settings.policy,
        profile=settings.profile,
    )
    print(
        "Salary policy results: "
        f"decisions={len(salary_decisions)}; "
        f"reasons={summarize_salary_decisions(salary_decisions)}"
    )
    resolved_db_path = resolve_db_path(db_path)
    status_rows = []
    with open_database(resolved_db_path) as conn:
        initialize_database(conn)
        repository = JobRepository(conn)
        ingestion_summary = repository.ingest_scrape_results(
            run_id=run_id,
            started_at=run_started_at,
            completed_at=datetime.now(timezone.utc),
            all_jobs=jobs,
            policy_eligible_jobs=eligible_jobs,
            shortlisted_jobs=top,
            notice_weeks_default=policy_snapshot.notice_default_weeks,
            salary_currency=policy_snapshot.salary_currency,
            relocation_required=settings.profile.relocation_assistance_required,
            eligibility_decisions=eligibility_evaluation.by_key,
            salary_decisions=salary_decisions,
        )
        status_rows = fetch_status_rows(conn)

    stamp = run_started_at.strftime("%Y-%m-%d")
    export_artifacts = write_status_exports(status_rows, resolved_review_dir, stamp)
    dashboard_artifacts = write_status_dashboard(status_rows, resolved_review_dir, stamp)
    panel_updated = update_live_jobs_html_with_status_section(
        resolved_review_dir / "latest_live_jobs.html",
        dashboard_artifacts=dashboard_artifacts,
        export_artifacts=export_artifacts,
    )
    print(
        "Status dashboard updated: "
        f"{dashboard_artifacts.latest_html.name}, {export_artifacts.latest_csv.name}, "
        f"{export_artifacts.latest_json.name}; "
        f"live_jobs_panel={'yes' if panel_updated else 'no'}"
    )
    print(
        "Queue DB updated: "
        f"run_id={ingestion_summary.run_id}, "
        f"jobs_seen={ingestion_summary.jobs_seen}, "
        f"policy_eligible={ingestion_summary.jobs_policy_eligible}, "
        f"shortlisted={ingestion_summary.jobs_shortlisted}, "
        f"upserted={ingestion_summary.jobs_upserted}"
    )
    for job in top[:15]:
        print(
            f"  [{job['score']:>2}] {job['title']} — {job['company']} "
            f"({job['location']}) [{job['tags']}]"
        )
    return top


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=int, default=None)
    parser.add_argument("--max-age-days", type=int, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--review-dir", type=Path, default=None)
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=None)
    return parser


def cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(
            min_score=args.min_score,
            max_age_days=args.max_age_days,
            out_dir=args.out_dir,
            review_dir=args.review_dir,
            config_dir=args.config_dir,
            db_path=args.db_path,
        )
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
