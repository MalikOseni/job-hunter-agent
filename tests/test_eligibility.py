from __future__ import annotations

import unittest

from job_hunter_agent.eligibility import evaluate_jobs


class EligibilityTests(unittest.TestCase):
    def test_ineligible_when_score_below_threshold(self) -> None:
        jobs = [
            {
                "score": 1,
                "source": "board",
                "company": "Acme",
                "title": "Engineer",
                "location": "Remote",
                "url": "https://example.com/1",
                "tags": "remote",
                "skills": "intune",
                "posted": "2026-07-01",
            }
        ]
        evaluation = evaluate_jobs(
            jobs,
            min_score=2,
            max_age_days=21,
            relocation_assistance_required=False,
        )
        self.assertEqual(len(evaluation.eligible_jobs), 0)
        self.assertEqual(evaluation.reason_counts.get("score_below_min_score"), 1)

    def test_ineligible_when_missing_posted_date(self) -> None:
        jobs = [
            {
                "score": 10,
                "source": "board",
                "company": "Acme",
                "title": "Engineer",
                "location": "Remote",
                "url": "https://example.com/2",
                "tags": "remote",
                "skills": "intune",
                "posted": "",
            }
        ]
        evaluation = evaluate_jobs(
            jobs,
            min_score=2,
            max_age_days=21,
            relocation_assistance_required=False,
        )
        self.assertEqual(evaluation.reason_counts.get("missing_posted_date"), 1)

    def test_ineligible_when_relocation_signal_missing(self) -> None:
        jobs = [
            {
                "score": 10,
                "source": "board",
                "company": "Acme",
                "title": "Engineer",
                "location": "London, United Kingdom",
                "url": "https://example.com/3",
                "tags": "target-country",
                "skills": "intune",
                "posted": "2026-07-01",
            }
        ]
        evaluation = evaluate_jobs(
            jobs,
            min_score=2,
            max_age_days=21,
            relocation_assistance_required=True,
        )
        self.assertEqual(evaluation.reason_counts.get("missing_relocation_signal"), 1)

    def test_ineligible_when_hybrid_without_relocation_signal(self) -> None:
        jobs = [
            {
                "score": 10,
                "source": "linkedin/guest",
                "company": "Acme",
                "title": "Identity Engineer",
                "location": "London, United Kingdom (Hybrid)",
                "url": "https://example.com/hybrid",
                "tags": "hybrid, target-country",
                "skills": "intune",
                "posted": "2026-07-01",
            }
        ]
        evaluation = evaluate_jobs(
            jobs,
            min_score=2,
            max_age_days=21,
            relocation_assistance_required=False,
        )
        self.assertEqual(
            evaluation.reason_counts.get("hybrid_missing_relocation_signal"),
            1,
        )

    def test_eligible_job_has_age_days(self) -> None:
        jobs = [
            {
                "score": 10,
                "source": "board",
                "company": "Acme",
                "title": "Engineer",
                "location": "Remote",
                "url": "https://example.com/4",
                "tags": "remote",
                "skills": "intune",
                "posted": "2026-07-01",
            }
        ]
        evaluation = evaluate_jobs(
            jobs,
            min_score=2,
            max_age_days=3650,
            relocation_assistance_required=False,
        )
        self.assertEqual(len(evaluation.eligible_jobs), 1)
        self.assertIn("age_days", evaluation.eligible_jobs[0])


if __name__ == "__main__":
    unittest.main()
