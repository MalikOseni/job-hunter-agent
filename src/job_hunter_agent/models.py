from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import JobRecord


@dataclass(frozen=True)
class JobQueueRecord:
    external_key: str
    source: str
    company: str
    title: str
    location: str
    url: str
    score: int
    tags: str
    skills: str
    posted: str
    age_days: int | None
    status: str
    status_color: str
    status_reason: str
    policy_eligible: bool
    shortlisted: bool


@dataclass(frozen=True)
class RunIngestionSummary:
    run_id: str
    jobs_seen: int
    jobs_policy_eligible: int
    jobs_shortlisted: int
    jobs_upserted: int
    auto_submit_enabled: bool = False
    auto_submit_shortlisted_considered: int = 0
    auto_submit_attempted: int = 0
    auto_submit_applied: int = 0
    auto_submit_blocked: int = 0
    auto_submit_skipped: int = 0


def canonical_job_key(job: JobRecord) -> str:
    url = (job.get("url", "") or "").strip().lower()
    if url:
        return url
    company = (job.get("company", "") or "").strip().lower()
    title = (job.get("title", "") or "").strip().lower()
    location = (job.get("location", "") or "").strip().lower()
    return f"{company}::{title}::{location}"


def normalize_age_days(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
