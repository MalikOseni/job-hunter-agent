from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class QueueStatus(str, Enum):
    APPLIED = "applied"
    INTERVIEW_PROGRESS = "interview_progressed"
    PENDING_REVIEW = "pending_review"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    SKIPPED_NOT_ELIGIBLE = "skipped_not_eligible"


STATUS_COLORS = {
    QueueStatus.APPLIED: "green",
    QueueStatus.INTERVIEW_PROGRESS: "blue",
    QueueStatus.PENDING_REVIEW: "yellow",
    QueueStatus.BLOCKED: "orange",
    QueueStatus.REJECTED: "red",
    QueueStatus.SKIPPED_NOT_ELIGIBLE: "gray",
}

APPLIED_STAGES = {"applied", "submitted"}
PROGRESSED_STAGES = {"interview", "interview_scheduled", "assessment", "offer"}
BLOCKED_STAGES = {"blocked", "waiting_on_candidate", "needs_credentials"}
REJECTED_STAGES = {"rejected", "declined", "not_selected", "withdrawn"}
PENDING_STAGES = {"pending_review", "queued", "draft"}


@dataclass(frozen=True)
class StatusDecision:
    status: QueueStatus
    color: str
    reason: str


def resolve_job_status(
    *,
    policy_eligible: bool,
    shortlisted: bool,
    application_stage: str | None,
) -> StatusDecision:
    stage = (application_stage or "").strip().lower()

    if stage in REJECTED_STAGES:
        return StatusDecision(
            status=QueueStatus.REJECTED,
            color=STATUS_COLORS[QueueStatus.REJECTED],
            reason=f"application_stage:{stage}",
        )
    if stage in BLOCKED_STAGES:
        return StatusDecision(
            status=QueueStatus.BLOCKED,
            color=STATUS_COLORS[QueueStatus.BLOCKED],
            reason=f"application_stage:{stage}",
        )
    if stage in PROGRESSED_STAGES:
        return StatusDecision(
            status=QueueStatus.INTERVIEW_PROGRESS,
            color=STATUS_COLORS[QueueStatus.INTERVIEW_PROGRESS],
            reason=f"application_stage:{stage}",
        )
    if stage in APPLIED_STAGES:
        return StatusDecision(
            status=QueueStatus.APPLIED,
            color=STATUS_COLORS[QueueStatus.APPLIED],
            reason=f"application_stage:{stage}",
        )
    if stage in PENDING_STAGES:
        return StatusDecision(
            status=QueueStatus.PENDING_REVIEW,
            color=STATUS_COLORS[QueueStatus.PENDING_REVIEW],
            reason=f"application_stage:{stage}",
        )

    if shortlisted:
        return StatusDecision(
            status=QueueStatus.PENDING_REVIEW,
            color=STATUS_COLORS[QueueStatus.PENDING_REVIEW],
            reason="awaiting_review",
        )
    if policy_eligible:
        return StatusDecision(
            status=QueueStatus.SKIPPED_NOT_ELIGIBLE,
            color=STATUS_COLORS[QueueStatus.SKIPPED_NOT_ELIGIBLE],
            reason="not_shortlisted_by_score_or_freshness",
        )
    return StatusDecision(
        status=QueueStatus.SKIPPED_NOT_ELIGIBLE,
        color=STATUS_COLORS[QueueStatus.SKIPPED_NOT_ELIGIBLE],
        reason="failed_policy_filters",
    )
