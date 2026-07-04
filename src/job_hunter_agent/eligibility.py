from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import canonical_job_key
from .reporting import parse_posted_date
from .types import JobRecord

RELOCATION_COMPATIBLE_TAGS = {
    "visa/relocation",
    "work-anywhere",
    "emea-remote",
    "remote",
}


@dataclass(frozen=True)
class EligibilityDecision:
    external_key: str
    eligible: bool
    reason_code: str
    reason_detail: str
    normalized_job: JobRecord


@dataclass(frozen=True)
class EligibilityEvaluation:
    decisions: list[EligibilityDecision]
    eligible_jobs: list[JobRecord]
    ineligible_jobs: list[JobRecord]
    by_key: dict[str, EligibilityDecision]
    reason_counts: dict[str, int]


def evaluate_jobs(
    jobs: list[JobRecord],
    *,
    min_score: int,
    max_age_days: int,
    relocation_assistance_required: bool,
) -> EligibilityEvaluation:
    deduped_jobs = _deduplicate_highest_score(jobs)
    decisions: list[EligibilityDecision] = []
    by_key: dict[str, EligibilityDecision] = {}
    eligible_jobs: list[JobRecord] = []
    ineligible_jobs: list[JobRecord] = []
    reason_counter: Counter[str] = Counter()

    for key, job in deduped_jobs.items():
        decision = evaluate_job(
            job,
            external_key=key,
            min_score=min_score,
            max_age_days=max_age_days,
            relocation_assistance_required=relocation_assistance_required,
        )
        decisions.append(decision)
        by_key[key] = decision
        reason_counter[decision.reason_code] += 1
        if decision.eligible:
            eligible_jobs.append(decision.normalized_job)
        else:
            ineligible_jobs.append(decision.normalized_job)

    return EligibilityEvaluation(
        decisions=decisions,
        eligible_jobs=eligible_jobs,
        ineligible_jobs=ineligible_jobs,
        by_key=by_key,
        reason_counts=dict(reason_counter),
    )


def evaluate_job(
    job: JobRecord,
    *,
    external_key: str,
    min_score: int,
    max_age_days: int,
    relocation_assistance_required: bool,
) -> EligibilityDecision:
    normalized = dict(job)
    tags = _parse_tags(normalized.get("tags", ""))
    score = int(normalized.get("score", 0))

    if relocation_assistance_required and not (tags & RELOCATION_COMPATIBLE_TAGS):
        return EligibilityDecision(
            external_key=external_key,
            eligible=False,
            reason_code="missing_relocation_signal",
            reason_detail=(
                "Role does not advertise visa sponsorship or compatible remote terms."
            ),
            normalized_job=normalized,
        )

    if score < min_score:
        return EligibilityDecision(
            external_key=external_key,
            eligible=False,
            reason_code="score_below_min_score",
            reason_detail=f"Score {score} is below configured minimum {min_score}.",
            normalized_job=normalized,
        )

    posted_dt = parse_posted_date(normalized.get("posted", ""))
    if posted_dt is None:
        return EligibilityDecision(
            external_key=external_key,
            eligible=False,
            reason_code="missing_posted_date",
            reason_detail="Role does not have a parseable posted date.",
            normalized_job=normalized,
        )

    age_days = _age_days(posted_dt)
    normalized["posted"] = posted_dt.date().isoformat()
    normalized["age_days"] = age_days

    if age_days > max_age_days:
        return EligibilityDecision(
            external_key=external_key,
            eligible=False,
            reason_code="posting_too_old",
            reason_detail=f"Role age {age_days}d exceeds max age {max_age_days}d.",
            normalized_job=normalized,
        )

    return EligibilityDecision(
        external_key=external_key,
        eligible=True,
        reason_code="eligible",
        reason_detail="Passed relocation, freshness, and score gates.",
        normalized_job=normalized,
    )


def summarize_reason_counts(reason_counts: dict[str, int]) -> str:
    if not reason_counts:
        return "none"
    parts = [f"{reason}:{count}" for reason, count in sorted(reason_counts.items())]
    return ", ".join(parts)


def _deduplicate_highest_score(jobs: list[JobRecord]) -> dict[str, JobRecord]:
    deduped: dict[str, JobRecord] = {}
    for job in jobs:
        key = canonical_job_key(job)
        existing = deduped.get(key)
        if existing is None or int(job.get("score", 0)) >= int(existing.get("score", 0)):
            deduped[key] = dict(job)
    return deduped


def _parse_tags(raw_tags: str) -> set[str]:
    parsed = {
        piece.strip().lower()
        for piece in (raw_tags or "").split(",")
        if piece.strip()
    }
    parsed.discard("check posting")
    return parsed


def _age_days(posted_dt) -> int:
    today = posted_dt.astimezone(timezone.utc).date()
    now_date = datetime.now(timezone.utc).date()
    delta = (now_date - today).days
    if delta < 0:
        return 0
    return delta
