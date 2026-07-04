from __future__ import annotations
import csv
import html
import re
from datetime import datetime, timezone
from pathlib import Path

from .types import JobRecord


def parse_posted_date(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
        if not match:
            return None
        try:
            dt = datetime.strptime(match.group(0), "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def keep_recent_live_jobs(jobs: list[JobRecord], max_age_days: int) -> list[JobRecord]:
    today = datetime.now(timezone.utc).date()
    fresh: list[JobRecord] = []
    for job in jobs:
        posted_dt = parse_posted_date(job.get("posted", ""))
        if posted_dt is None:
            continue
        age_days = (today - posted_dt.date()).days
        if age_days < 0:
            age_days = 0
        if age_days > max_age_days:
            continue
        row = dict(job)
        row["posted"] = posted_dt.date().isoformat()
        row["age_days"] = age_days
        fresh.append(row)
    return fresh


def append_live_jobs_log(jobs: list[JobRecord], out_dir: Path) -> Path:
    log_path = out_dir / "live_jobs_log.csv"
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fieldnames = [
        "run_timestamp_utc",
        "score",
        "title",
        "company",
        "location",
        "tags",
        "skills",
        "source",
        "posted",
        "age_days",
        "url",
    ]
    write_header = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for job in jobs:
            writer.writerow(
                {
                    "run_timestamp_utc": timestamp,
                    "score": job.get("score", 0),
                    "title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "location": job.get("location", ""),
                    "tags": job.get("tags", ""),
                    "skills": job.get("skills", ""),
                    "source": job.get("source", ""),
                    "posted": job.get("posted", ""),
                    "age_days": job.get("age_days", ""),
                    "url": job.get("url", ""),
                }
            )
    return log_path


def write_reports(
    jobs: list[JobRecord],
    min_score: int,
    max_age_days: int,
    out_dir: Path,
    review_dir: Path,
) -> list[JobRecord]:
    out_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    filtered = [job for job in jobs if job["score"] >= min_score]
    filtered = keep_recent_live_jobs(filtered, max_age_days)

    seen: set[str] = set()
    unique: list[JobRecord] = []
    for job in sorted(
        filtered,
        key=lambda row: (
            -row["score"],
            row.get("age_days", 9999),
            row.get("posted", ""),
            row["company"].lower(),
            row["title"].lower(),
        ),
    ):
        key = (
            job.get("url", "").strip().lower()
            or f"{job['company'].strip().lower()}::{job['title'].strip().lower()}"
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    csv_paths = [
        out_dir / f"job_matches_{stamp}.csv",
        review_dir / f"live_jobs_{stamp}.csv",
        review_dir / "latest_live_jobs.csv",
    ]
    csv_fields = [
        "score",
        "posted",
        "age_days",
        "title",
        "company",
        "location",
        "tags",
        "skills",
        "source",
        "url",
    ]
    for csv_path in csv_paths:
        with open(csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=csv_fields)
            writer.writeheader()
            writer.writerows(unique)

    rows = "".join(
        f"<tr><td>{job['score']}</td><td><a href='{html.escape(job['url'])}'>"
        f"{html.escape(job['title'])}</a></td><td>{html.escape(job['company'])}</td>"
        f"<td>{html.escape(job['location'])}</td>"
        f"<td>{html.escape(job['posted'])}</td><td>{job.get('age_days', '')}</td>"
        f"<td>{html.escape(job['tags'])}</td><td>{html.escape(job['skills'])}</td></tr>"
        for job in unique
    )
    html_doc = f"""<!doctype html><meta charset="utf-8">
<title>Job matches {stamp}</title>
<style>body{{font-family:system-ui;margin:24px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ddd;padding:6px 8px;font-size:14px;text-align:left}}
th{{background:#f5f5f5}}tr:nth-child(even){{background:#fafafa}}</style>
<h1>Job matches — {stamp} ({len(unique)} roles)</h1>
<p>Only roles with valid posted dates from the last {max_age_days} days are shown.
Ranked by skill match against Malik's resume.</p>
<table><tr><th>Score</th><th>Role</th><th>Company</th><th>Location</th>
<th>Posted</th><th>Age (days)</th><th>Mobility</th><th>Matched skills</th></tr>{rows}</table>"""
    html_paths = [
        out_dir / f"job_matches_{stamp}.html",
        review_dir / f"live_jobs_{stamp}.html",
        review_dir / "latest_live_jobs.html",
    ]
    for html_path in html_paths:
        html_path.write_text(html_doc, encoding="utf-8")

    log_path = append_live_jobs_log(unique, out_dir)
    print(
        f"\n{len(unique)} unique matches -> "
        f"{(review_dir / 'latest_live_jobs.csv').name}, "
        f"{(review_dir / 'latest_live_jobs.html').name}, {log_path.name}"
    )
    return unique
