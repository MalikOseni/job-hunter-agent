from __future__ import annotations

import unittest
from pathlib import Path

from job_hunter_agent.config import PolicyConfig, ProfileConfig, WritingRulesConfig
from job_hunter_agent.policy_engine import PolicyEngine


def _build_policy() -> PolicyConfig:
    return PolicyConfig(
        min_score_default=2,
        max_age_days_default=21,
        require_mobility_match=True,
        accepted_mobility_tags=("visa/relocation", "work-anywhere", "emea-remote", "remote"),
        target_country_keywords=("united kingdom", "uk"),
        require_target_country_for_non_remote=False,
        allow_remote=True,
        allow_work_anywhere=True,
        allow_emea_remote=True,
        notice_default_weeks=4,
        notice_fast_track_weeks=2,
        notice_immediate_weeks=0,
        notice_fast_track_keywords=("two weeks", "2 weeks"),
        notice_immediate_keywords=("asap",),
        salary_default_offer_strategy="top_of_range_if_provided",
        salary_fallback_strategy="market_rate_estimate",
        salary_currency="GBP",
        salary_market_rate_uplift_percent=10.0,
        writing_rules_profile="strict",
    )


def _build_profile() -> ProfileConfig:
    return ProfileConfig(
        full_name="Malik Oseni",
        location="Birmingham, United Kingdom",
        resume_path=Path("/tmp/resume.pdf"),
        notice_period_weeks=4,
        alternate_notice_period_weeks=2,
        relocation_assistance_required=True,
        preferred_remote_regions=("global", "emea"),
        login_email_env_var="JOB_HUNTER_LOGIN_EMAIL",
        login_password_env_var="JOB_HUNTER_LOGIN_PASSWORD",
    )


def _build_rules() -> WritingRulesConfig:
    return WritingRulesConfig(
        ban_em_dash=True,
        ban_ai_tells=True,
        banned_punctuation=("—",),
        banned_phrases=("as an ai",),
        banned_openers=("dear hiring manager",),
    )


class PolicyEngineTests(unittest.TestCase):
    def test_notice_resolution_prefers_immediate_keywords(self) -> None:
        engine = PolicyEngine(_build_policy(), _build_profile(), _build_rules())
        notice = engine.resolve_notice_weeks(
            {
                "title": "Engineer",
                "location": "London",
                "tags": "asap start",
                "score": 10,
                "source": "x",
                "company": "y",
                "url": "u",
                "skills": "",
                "posted": "2026-07-01",
            }
        )
        self.assertEqual(notice, 0)

    def test_apply_relocation_rules_removes_remote_tag_when_disabled(self) -> None:
        policy = _build_policy()
        policy = PolicyConfig(**{**policy.__dict__, "allow_remote": False})
        engine = PolicyEngine(policy, _build_profile(), _build_rules())
        jobs = [
            {
                "score": 10,
                "source": "remotive",
                "company": "Acme",
                "title": "Engineer",
                "location": "Remote - EMEA",
                "url": "https://example.com/role",
                "tags": "remote, work-anywhere",
                "skills": "intune",
                "posted": "2026-07-01",
            }
        ]
        filtered = engine.apply_relocation_rules(jobs)
        self.assertEqual(len(filtered), 1)
        self.assertNotIn("remote", filtered[0]["tags"].lower())


if __name__ == "__main__":
    unittest.main()
