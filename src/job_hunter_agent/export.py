from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

STATUS_ORDER = (
    "applied",
    "interview_progressed",
    "pending_review",
    "blocked",
    "rejected",
    "skipped_not_eligible",
)

STATUS_LABELS = {
    "applied": "Applied",
    "interview_progressed": "Interview / Progressed",
    "pending_review": "Pending review",
    "blocked": "Blocked",
    "rejected": "Rejected",
    "skipped_not_eligible": "Skipped / Not eligible",
}

STATUS_COLORS = {
    "applied": "green",
    "interview_progressed": "blue",
    "pending_review": "yellow",
    "blocked": "orange",
    "rejected": "red",
    "skipped_not_eligible": "gray",
}


@dataclass(frozen=True)
class StatusRow:
    job_id: int
    status: str
    status_color: str
    status_reason: str
    title: str
    company: str
    location: str
    url: str
    latest_score: int
    latest_posted: str
    latest_age_days: int | None
    application_stage: str
    application_updated_at: str
    salary_expectation: float | None
    salary_currency: str


@dataclass(frozen=True)
class StatusExportArtifacts:
    dated_csv: Path
    latest_csv: Path
    dated_json: Path
    latest_json: Path
    status_counts: dict[str, int]

@dataclass(frozen=True)
class ApplicationKpis:
    auto_submit_enabled: bool
    shortlisted_considered: int
    attempted: int
    applied: int
    blocked: int
    skipped: int
    success_rate: float
    target_shortlist_roles: int = 0
    target_shortlist_ratio: float = 0.0
    attempt_coverage_ratio: float = 0.0
    goal_progress_rating: float = 0.0
    reassess_required: bool = True


def fetch_status_rows(conn: sqlite3.Connection) -> list[StatusRow]:
    rows = conn.execute(
        """
        SELECT
          j.id AS job_id,
          j.status AS status,
          j.status_color AS status_color,
          j.status_reason AS status_reason,
          j.title AS title,
          j.company AS company,
          j.location AS location,
          j.url AS url,
          j.latest_score AS latest_score,
          j.latest_posted AS latest_posted,
          j.latest_age_days AS latest_age_days,
          COALESCE(a.current_stage, '') AS application_stage,
          COALESCE(a.updated_at, j.last_seen_at) AS application_updated_at,
          a.salary_expectation AS salary_expectation,
          COALESCE(a.salary_currency, '') AS salary_currency
        FROM jobs j
        LEFT JOIN applications a
          ON a.job_id = j.id
         AND a.source = 'queue'
        WHERE j.is_active = 1
        """
    ).fetchall()

    status_rows = [
        StatusRow(
            job_id=int(row["job_id"]),
            status=str(row["status"] or "skipped_not_eligible"),
            status_color=str(row["status_color"] or ""),
            status_reason=str(row["status_reason"] or ""),
            title=str(row["title"] or ""),
            company=str(row["company"] or ""),
            location=str(row["location"] or ""),
            url=str(row["url"] or ""),
            latest_score=int(row["latest_score"] or 0),
            latest_posted=str(row["latest_posted"] or ""),
            latest_age_days=int(row["latest_age_days"]) if row["latest_age_days"] is not None else None,
            application_stage=str(row["application_stage"] or ""),
            application_updated_at=str(row["application_updated_at"] or ""),
            salary_expectation=float(row["salary_expectation"]) if row["salary_expectation"] is not None else None,
            salary_currency=str(row["salary_currency"] or ""),
        )
        for row in rows
    ]
    status_rows.sort(key=_status_sort_key)
    return status_rows


def summarize_status_counts(rows: list[StatusRow]) -> dict[str, int]:
    counts = {status: 0 for status in STATUS_ORDER}
    for row in rows:
        key = row.status if row.status in counts else "skipped_not_eligible"
        counts[key] += 1
    return counts


def write_status_exports(
    rows: list[StatusRow],
    review_dir: Path,
    stamp: str,
    application_kpis: ApplicationKpis | None = None,
) -> StatusExportArtifacts:
    review_dir.mkdir(parents=True, exist_ok=True)
    status_counts = summarize_status_counts(rows)

    dated_csv = review_dir / f"application_status_{stamp}.csv"
    latest_csv = review_dir / "latest_application_status.csv"
    dated_json = review_dir / f"application_status_{stamp}.json"
    latest_json = review_dir / "latest_application_status.json"

    csv_fields = [
        "status",
        "status_label",
        "status_color",
        "status_reason",
        "application_stage",
        "company",
        "title",
        "location",
        "score",
        "posted",
        "age_days",
        "salary_expectation",
        "salary_currency",
        "updated_at",
        "url",
    ]

    export_rows = []
    for row in rows:
        export_rows.append(
            {
                "status": row.status,
                "status_label": STATUS_LABELS.get(row.status, row.status),
                "status_color": STATUS_COLORS.get(row.status, row.status_color or "gray"),
                "status_reason": row.status_reason,
                "application_stage": row.application_stage,
                "company": row.company,
                "title": row.title,
                "location": row.location,
                "score": row.latest_score,
                "posted": row.latest_posted,
                "age_days": row.latest_age_days if row.latest_age_days is not None else "",
                "salary_expectation": (
                    f"{row.salary_expectation:.2f}" if row.salary_expectation is not None else ""
                ),
                "salary_currency": row.salary_currency,
                "updated_at": row.application_updated_at,
                "url": row.url,
            }
        )

    for path in (dated_csv, latest_csv):
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=csv_fields)
            writer.writeheader()
            writer.writerows(export_rows)

    json_payload = {
        "generated_for_day": stamp,
        "status_counts": status_counts,
        "application_kpis": asdict(application_kpis) if application_kpis is not None else {},
        "rows": export_rows,
    }
    for path in (dated_json, latest_json):
        path.write_text(
            json.dumps(json_payload, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    return StatusExportArtifacts(
        dated_csv=dated_csv,
        latest_csv=latest_csv,
        dated_json=dated_json,
        latest_json=latest_json,
        status_counts=status_counts,
    )


def _status_sort_key(row: StatusRow) -> tuple[int, int, str, str]:
    try:
        status_index = STATUS_ORDER.index(row.status)
    except ValueError:
        status_index = len(STATUS_ORDER)
    return (
        status_index,
        -row.latest_score,
        row.company.lower(),
        row.title.lower(),
    )
