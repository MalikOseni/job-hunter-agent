from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_hunter_agent.auto_submit import (
    AutoSubmitConfig,
    AutoSubmitter,
    GreenhouseSubmissionRequest,
    GreenhouseSubmissionResponse,
)


class AutoSubmitterTests(unittest.TestCase):
    def test_disabled_auto_submit_skips_without_attempt(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as resume_file:
            submitter = AutoSubmitter(
                AutoSubmitConfig(
                    enabled=False,
                    max_per_run=5,
                    applicant_first_name="Malik",
                    applicant_last_name="Oseni",
                    applicant_email="malik@example.com",
                    applicant_phone="",
                    resume_path=Path(resume_file.name),
                    greenhouse_api_keys={},
                )
            )
            outcome = submitter.maybe_submit(
                job=_sample_job(),
                source="greenhouse/reddit",
                current_stage="pending_review",
            )

        self.assertFalse(outcome.attempted)
        self.assertTrue(outcome.skipped)
        self.assertEqual("pending_review", outcome.final_stage)
        self.assertEqual("auto_submit_disabled", outcome.reason_code)
        summary = submitter.summary()
        self.assertEqual(1, summary.shortlisted_considered)
        self.assertEqual(0, summary.attempted)
        self.assertEqual(1, summary.skipped)

    def test_missing_email_skips_without_attempt(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as resume_file:
            submitter = AutoSubmitter(
                AutoSubmitConfig(
                    enabled=True,
                    max_per_run=5,
                    applicant_first_name="Malik",
                    applicant_last_name="Oseni",
                    applicant_email="",
                    applicant_phone="",
                    resume_path=Path(resume_file.name),
                    greenhouse_api_keys={},
                )
            )
            outcome = submitter.maybe_submit(
                job=_sample_job(),
                source="greenhouse/reddit",
                current_stage="pending_review",
            )

        self.assertFalse(outcome.attempted)
        self.assertTrue(outcome.skipped)
        self.assertEqual("pending_review", outcome.final_stage)
        self.assertEqual("auto_submit_missing_applicant_email", outcome.reason_code)
        summary = submitter.summary()
        self.assertEqual(0, summary.attempted)
        self.assertEqual(0, summary.blocked)
        self.assertEqual(1, summary.skipped)

    def test_greenhouse_without_board_key_is_skipped(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as resume_file:
            submitter = AutoSubmitter(
                AutoSubmitConfig(
                    enabled=True,
                    max_per_run=5,
                    applicant_first_name="Malik",
                    applicant_last_name="Oseni",
                    applicant_email="malik@example.com",
                    applicant_phone="",
                    resume_path=Path(resume_file.name),
                    greenhouse_api_keys={},
                )
            )
            outcome = submitter.maybe_submit(
                job=_sample_job(),
                source="greenhouse/reddit",
                current_stage="pending_review",
            )

        self.assertFalse(outcome.attempted)
        self.assertFalse(outcome.applied)
        self.assertTrue(outcome.skipped)
        self.assertTrue(outcome.reason_code.startswith("auto_submit_missing_greenhouse_api_key:"))
        self.assertEqual("pending_review", outcome.final_stage)
        summary = submitter.summary()
        self.assertEqual(0, summary.attempted)
        self.assertEqual(0, summary.blocked)
        self.assertEqual(1, summary.skipped)

    def test_greenhouse_success_sets_applied(self) -> None:
        seen_requests: list[GreenhouseSubmissionRequest] = []

        def fake_submit(req: GreenhouseSubmissionRequest) -> GreenhouseSubmissionResponse:
            seen_requests.append(req)
            return GreenhouseSubmissionResponse(
                external_application_id="app-123",
                notes="ok",
            )

        with tempfile.NamedTemporaryFile(suffix=".pdf") as resume_file:
            submitter = AutoSubmitter(
                AutoSubmitConfig(
                    enabled=True,
                    max_per_run=5,
                    applicant_first_name="Malik",
                    applicant_last_name="Oseni",
                    applicant_email="malik@example.com",
                    applicant_phone="",
                    resume_path=Path(resume_file.name),
                    greenhouse_api_keys={"reddit": "token"},
                ),
                greenhouse_submitter=fake_submit,
            )
            outcome = submitter.maybe_submit(
                job=_sample_job(),
                source="greenhouse/reddit",
                current_stage="pending_review",
            )

        self.assertTrue(outcome.attempted)
        self.assertTrue(outcome.applied)
        self.assertEqual("applied", outcome.final_stage)
        self.assertEqual("app-123", outcome.external_application_id)
        self.assertEqual(1, len(seen_requests))
        self.assertEqual("reddit", seen_requests[0].board_token)
        self.assertEqual("8044767", seen_requests[0].job_id)
        summary = submitter.summary()
        self.assertEqual(1, summary.attempted)
        self.assertEqual(1, summary.applied)
        self.assertEqual(100.0, summary.success_rate)

    def test_max_per_run_limit_skips_after_limit(self) -> None:
        def fake_submit(_: GreenhouseSubmissionRequest) -> GreenhouseSubmissionResponse:
            return GreenhouseSubmissionResponse(
                external_application_id="app-999",
                notes="ok",
            )

        with tempfile.NamedTemporaryFile(suffix=".pdf") as resume_file:
            submitter = AutoSubmitter(
                AutoSubmitConfig(
                    enabled=True,
                    max_per_run=1,
                    applicant_first_name="Malik",
                    applicant_last_name="Oseni",
                    applicant_email="malik@example.com",
                    applicant_phone="",
                    resume_path=Path(resume_file.name),
                    greenhouse_api_keys={"reddit": "token"},
                ),
                greenhouse_submitter=fake_submit,
            )
            first = submitter.maybe_submit(
                job=_sample_job(),
                source="greenhouse/reddit",
                current_stage="pending_review",
            )
            second = submitter.maybe_submit(
                job=_sample_job(),
                source="greenhouse/reddit",
                current_stage="pending_review",
            )

        self.assertTrue(first.attempted)
        self.assertEqual("applied", first.final_stage)
        self.assertFalse(second.attempted)
        self.assertTrue(second.skipped)
        self.assertEqual("auto_submit_max_per_run_reached", second.reason_code)
        summary = submitter.summary()
        self.assertEqual(2, summary.shortlisted_considered)
        self.assertEqual(1, summary.attempted)
        self.assertEqual(1, summary.skipped)

    def test_skip_requeues_blocked_stage_to_pending_review(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as resume_file:
            submitter = AutoSubmitter(
                AutoSubmitConfig(
                    enabled=True,
                    max_per_run=5,
                    applicant_first_name="Malik",
                    applicant_last_name="Oseni",
                    applicant_email="",
                    applicant_phone="",
                    resume_path=Path(resume_file.name),
                    greenhouse_api_keys={"reddit": "token"},
                )
            )
            outcome = submitter.maybe_submit(
                job=_sample_job(),
                source="greenhouse/reddit",
                current_stage="blocked",
            )

        self.assertFalse(outcome.attempted)
        self.assertTrue(outcome.skipped)
        self.assertEqual("pending_review", outcome.final_stage)
        self.assertEqual("auto_submit_missing_applicant_email", outcome.reason_code)

    def test_goal_alignment_required_skips_non_target_role(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as resume_file:
            submitter = AutoSubmitter(
                AutoSubmitConfig(
                    enabled=True,
                    max_per_run=5,
                    applicant_first_name="Malik",
                    applicant_last_name="Oseni",
                    applicant_email="malik@example.com",
                    applicant_phone="",
                    resume_path=Path(resume_file.name),
                    greenhouse_api_keys={"reddit": "token"},
                    require_goal_alignment=True,
                )
            )
            non_target_job = dict(_sample_job())
            non_target_job["tags"] = "remote"
            outcome = submitter.maybe_submit(
                job=non_target_job,
                source="greenhouse/reddit",
                current_stage="pending_review",
            )

        self.assertFalse(outcome.attempted)
        self.assertTrue(outcome.skipped)
        self.assertEqual("auto_submit_not_goal_aligned", outcome.reason_code)

    def test_min_score_required_skips_low_quality_role(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as resume_file:
            submitter = AutoSubmitter(
                AutoSubmitConfig(
                    enabled=True,
                    max_per_run=5,
                    applicant_first_name="Malik",
                    applicant_last_name="Oseni",
                    applicant_email="malik@example.com",
                    applicant_phone="",
                    resume_path=Path(resume_file.name),
                    greenhouse_api_keys={"reddit": "token"},
                    min_score_required=14,
                )
            )
            low_score_job = dict(_sample_job())
            low_score_job["score"] = 10
            outcome = submitter.maybe_submit(
                job=low_score_job,
                source="greenhouse/reddit",
                current_stage="pending_review",
            )

        self.assertFalse(outcome.attempted)
        self.assertTrue(outcome.skipped)
        self.assertEqual("auto_submit_below_quality_threshold", outcome.reason_code)


def _sample_job() -> dict[str, object]:
    return {
        "url": "https://job-boards.greenhouse.io/reddit/jobs/8044767",
        "location": "Remote - United States",
        "tags": "work-anywhere",
        "score": 16,
    }


if __name__ == "__main__":
    unittest.main()
