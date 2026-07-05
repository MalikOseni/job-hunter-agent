from __future__ import annotations

import unittest

from job_hunter_agent.scoring import add_job


class ScoringSourceFiltersTests(unittest.TestCase):
    def test_linkedin_hybrid_without_relocation_is_filtered(self) -> None:
        jobs: list[dict[str, object]] = []
        add_job(
            jobs,
            "linkedin/guest",
            "ExampleCo",
            "Identity Engineer",
            "London, United Kingdom (Hybrid)",
            "https://example.com/job-1",
            "Hybrid schedule with onsite collaboration.",
            "2026-07-01",
        )
        self.assertEqual(len(jobs), 0)

    def test_linkedin_hybrid_with_relocation_is_kept(self) -> None:
        jobs: list[dict[str, object]] = []
        add_job(
            jobs,
            "linkedin/guest",
            "ExampleCo",
            "Identity Engineer",
            "London, United Kingdom (Hybrid)",
            "https://example.com/job-2",
            "Hybrid role with visa sponsorship and relocation support.",
            "2026-07-01",
        )
        self.assertEqual(len(jobs), 1)
        self.assertIn("hybrid", str(jobs[0]["tags"]).lower())
        self.assertIn("visa/relocation", str(jobs[0]["tags"]).lower())

    def test_niche_source_without_mobility_signal_is_filtered(self) -> None:
        jobs: list[dict[str, object]] = []
        add_job(
            jobs,
            "weworkremotely",
            "ExampleCo",
            "Identity Engineer",
            "United Kingdom",
            "https://example.com/job-3",
            "Role requires onsite attendance only with office-based schedule.",
            "2026-07-01",
        )
        self.assertEqual(len(jobs), 0)


if __name__ == "__main__":
    unittest.main()
