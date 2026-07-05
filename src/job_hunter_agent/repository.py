from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from .auto_submit import AutoSubmitOutcome, AutoSubmitter, goal_alignment_priority
from .eligibility import EligibilityDecision

from .models import JobQueueRecord, RunIngestionSummary, canonical_job_key, normalize_age_days
from .salary_policy import SalaryDecision
from .status import QueueStatus, StatusDecision, resolve_job_status
from .types import JobRecord


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


class JobRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def ingest_scrape_results(
        self,
        *,
        run_id: str,
        started_at: datetime,
        completed_at: datetime,
        all_jobs: list[JobRecord],
        policy_eligible_jobs: list[JobRecord],
        shortlisted_jobs: list[JobRecord],
        notice_weeks_default: int,
        salary_currency: str,
        relocation_required: bool,
        eligibility_decisions: dict[str, EligibilityDecision] | None = None,
        salary_decisions: dict[str, SalaryDecision] | None = None,
        auto_submitter: AutoSubmitter | None = None,
    ) -> RunIngestionSummary:
        eligibility_decisions = eligibility_decisions or {}
        salary_decisions = salary_decisions or {}
        canonical_jobs = self._deduplicate_jobs(all_jobs)
        policy_keys = {canonical_job_key(job) for job in policy_eligible_jobs}
        shortlist_keys = {canonical_job_key(job) for job in shortlisted_jobs}
        jobs_seen = len(canonical_jobs)
        jobs_policy_eligible = sum(1 for key in canonical_jobs if key in policy_keys)
        jobs_shortlisted = sum(1 for key in canonical_jobs if key in shortlist_keys)

        started_at_iso = _utc_iso(started_at)
        completed_at_iso = _utc_iso(completed_at)
        self._start_run_log(
            run_id=run_id,
            started_at=started_at_iso,
            notes="Day 3 queue ingestion run",
        )

        jobs_upserted = 0
        ordered_jobs = sorted(
            canonical_jobs.items(),
            key=lambda item: _job_processing_sort_key(item[0], item[1], shortlist_keys),
        )
        for key, job in ordered_jobs:
            policy_eligible = key in policy_keys
            shortlisted = key in shortlist_keys
            eligibility_decision = eligibility_decisions.get(key)
            status_reason_override = _status_reason_override(eligibility_decision)
            existing_job_id = self._find_job_id_by_external_key(key)
            application_stage = (
                self._latest_application_stage(existing_job_id)
                if existing_job_id is not None
                else None
            )
            status_decision = resolve_job_status(
                policy_eligible=policy_eligible,
                shortlisted=shortlisted,
                application_stage=application_stage,
            )
            record = self._build_job_record(
                key=key,
                job=job,
                status_decision=status_decision,
                policy_eligible=policy_eligible,
                shortlisted=shortlisted,
                status_reason_override=status_reason_override,
            )
            job_id = self._upsert_job(record, observed_at=completed_at_iso)
            final_application_stage = application_stage
            final_status_reason_override = status_reason_override

            if shortlisted:
                stage = application_stage or QueueStatus.PENDING_REVIEW.value
                stage_decision = resolve_job_status(
                    policy_eligible=True,
                    shortlisted=True,
                    application_stage=stage,
                )
                salary_decision = salary_decisions.get(key)
                app_id, created = self._ensure_queue_application(
                    job_id=job_id,
                    stage=stage,
                    stage_color=stage_decision.color,
                    observed_at=completed_at_iso,
                    notice_weeks=notice_weeks_default,
                    salary_currency=salary_currency,
                    relocation_required=relocation_required,
                    salary_decision=salary_decision,
                )
                if created:
                    self._insert_application_event(
                        application_id=app_id,
                        event_type="queued",
                        event_at=completed_at_iso,
                        notes="Added to queue from scrape shortlist.",
                    )
                if auto_submitter is not None:
                    auto_submit_outcome = auto_submitter.maybe_submit(
                        job=job,
                        source=record.source,
                        current_stage=stage,
                    )
                    final_application_stage = auto_submit_outcome.final_stage
                    should_persist_auto_submit_outcome = (
                        auto_submit_outcome.attempted
                        or auto_submit_outcome.final_stage != stage
                    )
                    if auto_submit_outcome.attempted:
                        final_status_reason_override = auto_submit_outcome.reason_code
                    if should_persist_auto_submit_outcome:
                        self._apply_auto_submit_outcome(
                            application_id=app_id,
                            observed_at=completed_at_iso,
                            outcome=auto_submit_outcome,
                        )
                        if auto_submit_outcome.applied:
                            event_type = "auto_submit_applied"
                        elif auto_submit_outcome.attempted:
                            event_type = "auto_submit_blocked"
                        else:
                            event_type = "auto_submit_requeued"
                        self._insert_application_event(
                            application_id=app_id,
                            event_type=event_type,
                            event_at=completed_at_iso,
                            notes=auto_submit_outcome.notes,
                        )
                else:
                    final_application_stage = stage
                if salary_decision is not None:
                    self._insert_salary_benchmark(
                        job_id=job_id,
                        observed_at=completed_at_iso,
                        salary_decision=salary_decision,
                    )
            final_status_decision = resolve_job_status(
                policy_eligible=policy_eligible,
                shortlisted=shortlisted,
                application_stage=final_application_stage,
            )
            final_record = self._build_job_record(
                key=key,
                job=job,
                status_decision=final_status_decision,
                policy_eligible=policy_eligible,
                shortlisted=shortlisted,
                status_reason_override=final_status_reason_override,
            )
            if (
                final_record.status != record.status
                or final_record.status_color != record.status_color
                or final_record.status_reason != record.status_reason
            ):
                self._upsert_job(final_record, observed_at=completed_at_iso)
            self._insert_snapshot(
                job_id=job_id,
                run_id=run_id,
                captured_at=completed_at_iso,
                record=final_record,
                raw_job=job,
            )
            jobs_upserted += 1

        self._finish_run_log(
            run_id=run_id,
            completed_at=completed_at_iso,
            run_status="succeeded",
            jobs_seen=jobs_seen,
            jobs_policy_eligible=jobs_policy_eligible,
            jobs_shortlisted=jobs_shortlisted,
            jobs_upserted=jobs_upserted,
        )
        self.conn.commit()
        auto_submit_summary = auto_submitter.summary() if auto_submitter is not None else None

        return RunIngestionSummary(
            run_id=run_id,
            jobs_seen=jobs_seen,
            jobs_policy_eligible=jobs_policy_eligible,
            jobs_shortlisted=jobs_shortlisted,
            jobs_upserted=jobs_upserted,
            auto_submit_enabled=(auto_submit_summary.enabled if auto_submit_summary else False),
            auto_submit_shortlisted_considered=(
                auto_submit_summary.shortlisted_considered if auto_submit_summary else 0
            ),
            auto_submit_attempted=(auto_submit_summary.attempted if auto_submit_summary else 0),
            auto_submit_applied=(auto_submit_summary.applied if auto_submit_summary else 0),
            auto_submit_blocked=(auto_submit_summary.blocked if auto_submit_summary else 0),
            auto_submit_skipped=(auto_submit_summary.skipped if auto_submit_summary else 0),
        )

    def _deduplicate_jobs(self, jobs: list[JobRecord]) -> dict[str, JobRecord]:
        deduped: dict[str, JobRecord] = {}
        for job in jobs:
            key = canonical_job_key(job)
            previous = deduped.get(key)
            if previous is None:
                deduped[key] = dict(job)
                continue
            if int(job.get("score", 0)) >= int(previous.get("score", 0)):
                deduped[key] = dict(job)
        return deduped

    def _build_job_record(
        self,
        *,
        key: str,
        job: JobRecord,
        status_decision: StatusDecision,
        policy_eligible: bool,
        shortlisted: bool,
        status_reason_override: str | None,
    ) -> JobQueueRecord:
        return JobQueueRecord(
            external_key=key,
            source=(job.get("source", "") or "").strip(),
            company=(job.get("company", "") or "").strip(),
            title=(job.get("title", "") or "").strip(),
            location=(job.get("location", "") or "").strip(),
            url=(job.get("url", "") or "").strip(),
            score=int(job.get("score", 0)),
            tags=(job.get("tags", "") or "").strip(),
            skills=(job.get("skills", "") or "").strip(),
            posted=(job.get("posted", "") or "").strip(),
            age_days=normalize_age_days(job.get("age_days")),
            status=status_decision.status.value,
            status_color=status_decision.color,
            status_reason=status_reason_override or status_decision.reason,
            policy_eligible=policy_eligible,
            shortlisted=shortlisted,
        )

    def _start_run_log(self, *, run_id: str, started_at: str, notes: str) -> None:
        self.conn.execute(
            """
            INSERT INTO run_logs (run_id, started_at, run_status, notes)
            VALUES (?, ?, 'running', ?)
            ON CONFLICT(run_id) DO UPDATE SET
              started_at = excluded.started_at,
              run_status = 'running',
              notes = excluded.notes
            """,
            (run_id, started_at, notes),
        )

    def _finish_run_log(
        self,
        *,
        run_id: str,
        completed_at: str,
        run_status: str,
        jobs_seen: int,
        jobs_policy_eligible: int,
        jobs_shortlisted: int,
        jobs_upserted: int,
    ) -> None:
        self.conn.execute(
            """
            UPDATE run_logs
            SET completed_at = ?,
                run_status = ?,
                jobs_seen = ?,
                jobs_policy_eligible = ?,
                jobs_shortlisted = ?,
                jobs_upserted = ?
            WHERE run_id = ?
            """,
            (
                completed_at,
                run_status,
                jobs_seen,
                jobs_policy_eligible,
                jobs_shortlisted,
                jobs_upserted,
                run_id,
            ),
        )

    def _find_job_id_by_external_key(self, external_key: str) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE external_key = ?",
            (external_key,),
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])

    def _latest_application_stage(self, job_id: int | None) -> str | None:
        if job_id is None:
            return None
        row = self.conn.execute(
            """
            SELECT current_stage
            FROM applications
            WHERE job_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row["current_stage"])

    def _upsert_job(self, record: JobQueueRecord, observed_at: str) -> int:
        self.conn.execute(
            """
            INSERT INTO jobs (
              external_key,
              source,
              company,
              title,
              location,
              url,
              latest_score,
              latest_tags,
              latest_skills,
              latest_posted,
              latest_age_days,
              status,
              status_color,
              status_reason,
              eligibility_passed,
              first_seen_at,
              last_seen_at,
              is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(external_key) DO UPDATE SET
              source = excluded.source,
              company = excluded.company,
              title = excluded.title,
              location = excluded.location,
              url = excluded.url,
              latest_score = excluded.latest_score,
              latest_tags = excluded.latest_tags,
              latest_skills = excluded.latest_skills,
              latest_posted = excluded.latest_posted,
              latest_age_days = excluded.latest_age_days,
              status = excluded.status,
              status_color = excluded.status_color,
              status_reason = excluded.status_reason,
              eligibility_passed = excluded.eligibility_passed,
              last_seen_at = excluded.last_seen_at,
              is_active = 1
            """,
            (
                record.external_key,
                record.source or "unknown",
                record.company or "unknown",
                record.title or "untitled",
                record.location or "n/a",
                record.url,
                record.score,
                record.tags,
                record.skills,
                record.posted or None,
                record.age_days,
                record.status,
                record.status_color,
                record.status_reason,
                1 if record.policy_eligible else 0,
                observed_at,
                observed_at,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE external_key = ?",
            (record.external_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Job upsert failed to return an identifier.")
        return int(row["id"])

    def _insert_snapshot(
        self,
        *,
        job_id: int,
        run_id: str,
        captured_at: str,
        record: JobQueueRecord,
        raw_job: JobRecord,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO job_snapshots (
              job_id,
              run_id,
              captured_at,
              score,
              tags,
              skills,
              posted,
              age_days,
              status,
              status_color,
              status_reason,
              is_shortlisted,
              raw_payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, run_id) DO UPDATE SET
              captured_at = excluded.captured_at,
              score = excluded.score,
              tags = excluded.tags,
              skills = excluded.skills,
              posted = excluded.posted,
              age_days = excluded.age_days,
              status = excluded.status,
              status_color = excluded.status_color,
              status_reason = excluded.status_reason,
              is_shortlisted = excluded.is_shortlisted,
              raw_payload_json = excluded.raw_payload_json
            """,
            (
                job_id,
                run_id,
                captured_at,
                record.score,
                record.tags,
                record.skills,
                record.posted or None,
                record.age_days,
                record.status,
                record.status_color,
                record.status_reason,
                1 if record.shortlisted else 0,
                json.dumps(dict(raw_job), sort_keys=True, ensure_ascii=False),
            ),
        )

    def _ensure_queue_application(
        self,
        *,
        job_id: int,
        stage: str,
        stage_color: str,
        observed_at: str,
        notice_weeks: int,
        salary_currency: str,
        relocation_required: bool,
        salary_decision: SalaryDecision | None,
    ) -> tuple[int, bool]:
        salary_expectation = salary_decision.target_amount if salary_decision else None
        effective_currency = salary_decision.currency if salary_decision else salary_currency
        metadata_json = _salary_metadata_json(salary_decision)
        row = self.conn.execute(
            """
            SELECT id, metadata_json
            FROM applications
            WHERE job_id = ? AND source = 'queue'
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is not None:
            application_id = int(row["id"])
            merged_metadata = _parse_metadata_json(str(row["metadata_json"] or "{}"))
            merged_metadata.update(_parse_metadata_json(metadata_json))
            self.conn.execute(
                """
                UPDATE applications
                SET current_stage = ?,
                    status_color = ?,
                    updated_at = ?,
                    notice_weeks = ?,
                    salary_expectation = ?,
                    salary_currency = ?,
                    relocation_assistance_required = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    stage,
                    stage_color,
                    observed_at,
                    notice_weeks,
                    salary_expectation,
                    effective_currency,
                    1 if relocation_required else 0,
                    json.dumps(merged_metadata, sort_keys=True),
                    application_id,
                ),
            )
            return application_id, False

        cursor = self.conn.execute(
            """
            INSERT INTO applications (
              job_id,
              source,
              current_stage,
              status_color,
              submitted_at,
              updated_at,
              notice_weeks,
              salary_expectation,
              salary_currency,
              relocation_assistance_required,
              metadata_json
            )
            VALUES (?, 'queue', ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                stage,
                stage_color,
                observed_at,
                notice_weeks,
                salary_expectation,
                effective_currency,
                1 if relocation_required else 0,
                metadata_json,
            ),
        )
        return int(cursor.lastrowid), True
    def _apply_auto_submit_outcome(
        self,
        *,
        application_id: int,
        observed_at: str,
        outcome: AutoSubmitOutcome,
    ) -> None:
        row = self.conn.execute(
            """
            SELECT metadata_json, submitted_at, external_application_id
            FROM applications
            WHERE id = ?
            """,
            (application_id,),
        ).fetchone()
        metadata = _parse_metadata_json(row["metadata_json"] if row is not None else "{}")
        if outcome.attempted:
            previous_attempts = int(metadata.get("auto_submit_attempts", 0) or 0)
            metadata["auto_submit_attempts"] = previous_attempts + 1
            metadata["auto_submit_last_attempted_at"] = observed_at
        else:
            previous_skips = int(metadata.get("auto_submit_skips", 0) or 0)
            metadata["auto_submit_skips"] = previous_skips + 1
        metadata["auto_submit_last_reason"] = outcome.reason_code
        metadata["auto_submit_last_notes"] = outcome.notes
        metadata["auto_submit_last_evaluated_at"] = observed_at
        if outcome.applied:
            metadata["auto_submit_last_result"] = "applied"
        elif outcome.attempted:
            metadata["auto_submit_last_result"] = "blocked"
        else:
            metadata["auto_submit_last_result"] = "skipped"
        if outcome.external_application_id:
            metadata["auto_submit_external_application_id"] = outcome.external_application_id

        submitted_flag = 1 if outcome.applied else 0
        self.conn.execute(
            """
            UPDATE applications
            SET current_stage = ?,
                status_color = ?,
                updated_at = ?,
                submitted_at = CASE
                    WHEN ? = 1 THEN COALESCE(submitted_at, ?)
                    ELSE submitted_at
                END,
                external_application_id = COALESCE(?, external_application_id),
                metadata_json = ?
            WHERE id = ?
            """,
            (
                outcome.final_stage,
                outcome.stage_color,
                observed_at,
                submitted_flag,
                observed_at,
                outcome.external_application_id,
                json.dumps(metadata, sort_keys=True),
                application_id,
            ),
        )

    def _insert_salary_benchmark(
        self,
        *,
        job_id: int,
        observed_at: str,
        salary_decision: SalaryDecision,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO salary_benchmarks (
              job_id,
              role_family,
              market_region,
              currency,
              low_amount,
              median_amount,
              high_amount,
              source,
              collected_at,
              metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'salary_policy_stub', ?, ?)
            """,
            (
                job_id,
                salary_decision.role_family,
                salary_decision.market_region,
                salary_decision.currency,
                salary_decision.benchmark_low,
                salary_decision.benchmark_median,
                salary_decision.benchmark_high,
                observed_at,
                json.dumps(
                    {
                        "strategy": salary_decision.strategy,
                        "reason_code": salary_decision.reason_code,
                        "target_amount": salary_decision.target_amount,
                    },
                    sort_keys=True,
                ),
            ),
        )

    def _insert_application_event(
        self,
        *,
        application_id: int,
        event_type: str,
        event_at: str,
        notes: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO application_events (
              application_id,
              event_type,
              event_at,
              notes,
              metadata_json
            )
            VALUES (?, ?, ?, ?, '{}')
            """,
            (application_id, event_type, event_at, notes),
        )


def _status_reason_override(decision: EligibilityDecision | None) -> str | None:
    if decision is None:
        return None
    if decision.eligible:
        return None
    return f"eligibility:{decision.reason_code}"


def _salary_metadata_json(salary_decision: SalaryDecision | None) -> str:
    if salary_decision is None:
        return "{}"
    return json.dumps(
        {
            "salary_reason_code": salary_decision.reason_code,
            "salary_strategy": salary_decision.strategy,
            "role_family": salary_decision.role_family,
            "market_region": salary_decision.market_region,
        },
        sort_keys=True,
    )


def _parse_metadata_json(value: str) -> dict[str, object]:
    try:
        loaded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    if isinstance(loaded, dict):
        return dict(loaded)
    return {}


def _job_processing_sort_key(
    key: str,
    job: JobRecord,
    shortlist_keys: set[str],
) -> tuple[int, int, int, str]:
    shortlisted_rank = 0 if key in shortlist_keys else 1
    goal_rank = -goal_alignment_priority(job.get("tags", ""))
    score_rank = -_coerce_int(job.get("score"))
    return (shortlisted_rank, goal_rank, score_rank, key)


def _coerce_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
