from __future__ import annotations

from dataclasses import dataclass

from .config import PolicyConfig, ProfileConfig, WritingRulesConfig
from .types import JobRecord

REMOTE_TAGS = {"remote", "work-anywhere", "emea-remote"}
ORDERED_TAGS = (
    "visa/relocation",
    "work-anywhere",
    "emea-remote",
    "remote",
    "hybrid",
    "target-country",
)


@dataclass(frozen=True)
class RunPolicySnapshot:
    min_score: int
    max_age_days: int
    salary_default_offer_strategy: str
    salary_fallback_strategy: str
    salary_currency: str
    notice_default_weeks: int


class PolicyEngine:
    def __init__(
        self,
        policy: PolicyConfig,
        profile: ProfileConfig,
        writing_rules: WritingRulesConfig,
    ):
        self.policy = policy
        self.profile = profile
        self.writing_rules = writing_rules

    def resolve_min_score(self, cli_min_score: int | None) -> int:
        if cli_min_score is not None:
            return cli_min_score
        return self.policy.min_score_default

    def resolve_max_age_days(self, cli_max_age_days: int | None) -> int:
        if cli_max_age_days is not None:
            return cli_max_age_days
        return self.policy.max_age_days_default

    def resolve_notice_weeks(self, job: JobRecord | None = None) -> int:
        text = ""
        if job is not None:
            text = (
                f"{job.get('title', '')} "
                f"{job.get('location', '')} "
                f"{job.get('tags', '')}"
            ).lower()
        if text and any(token in text for token in self.policy.notice_immediate_keywords):
            return self.policy.notice_immediate_weeks
        if text and any(token in text for token in self.policy.notice_fast_track_keywords):
            return self.policy.notice_fast_track_weeks
        return self.policy.notice_default_weeks or self.profile.notice_period_weeks

    def resolve_salary_strategy(self, has_salary_band: bool) -> str:
        if has_salary_band:
            return self.policy.salary_default_offer_strategy
        return self.policy.salary_fallback_strategy

    def policy_snapshot(
        self,
        cli_min_score: int | None,
        cli_max_age_days: int | None,
    ) -> RunPolicySnapshot:
        return RunPolicySnapshot(
            min_score=self.resolve_min_score(cli_min_score),
            max_age_days=self.resolve_max_age_days(cli_max_age_days),
            salary_default_offer_strategy=self.policy.salary_default_offer_strategy,
            salary_fallback_strategy=self.policy.salary_fallback_strategy,
            salary_currency=self.policy.salary_currency,
            notice_default_weeks=self.resolve_notice_weeks(),
        )

    def apply_relocation_rules(self, jobs: list[JobRecord]) -> list[JobRecord]:
        filtered: list[JobRecord] = []
        for job in jobs:
            normalized = self._normalize_job(job)
            if not self._passes_mobility(normalized):
                continue
            filtered.append(normalized)
        return filtered

    def _normalize_job(self, job: JobRecord) -> JobRecord:
        normalized = dict(job)
        tags = self._parse_tags(normalized.get("tags", ""))
        tags.discard("target-country")
        if self._is_target_country(normalized.get("location", "")):
            tags.add("target-country")
        tags = self._apply_remote_preferences(tags)
        normalized["tags"] = self._join_tags(tags)
        return normalized

    def _apply_remote_preferences(self, tags: set[str]) -> set[str]:
        adjusted = set(tags)
        if not self.policy.allow_remote:
            adjusted.discard("remote")
        if not self.policy.allow_work_anywhere:
            adjusted.discard("work-anywhere")
        if not self.policy.allow_emea_remote:
            adjusted.discard("emea-remote")
        return adjusted

    def _passes_mobility(self, job: JobRecord) -> bool:
        tags = self._parse_tags(job.get("tags", ""))
        if self.policy.require_target_country_for_non_remote:
            if not (tags & REMOTE_TAGS) and "target-country" not in tags:
                return False
        if not self.policy.require_mobility_match:
            return True
        allowed_tags = self._allowed_mobility_tags()
        if not allowed_tags:
            return False
        return bool(tags & allowed_tags)

    def _allowed_mobility_tags(self) -> set[str]:
        allowed = {tag.lower() for tag in self.policy.accepted_mobility_tags}
        if not self.policy.allow_remote:
            allowed.discard("remote")
        if not self.policy.allow_work_anywhere:
            allowed.discard("work-anywhere")
        if not self.policy.allow_emea_remote:
            allowed.discard("emea-remote")
        return allowed

    def _is_target_country(self, location: str) -> bool:
        location_lower = (location or "").lower()
        return any(
            keyword.lower() in location_lower for keyword in self.policy.target_country_keywords
        )

    @staticmethod
    def _parse_tags(raw_tags: str) -> set[str]:
        tags = {
            piece.strip().lower()
            for piece in (raw_tags or "").split(",")
            if piece.strip()
        }
        if "check posting" in tags:
            tags.remove("check posting")
        return tags

    @staticmethod
    def _join_tags(tags: set[str]) -> str:
        if not tags:
            return "check posting"
        ordered = [tag for tag in ORDERED_TAGS if tag in tags]
        extras = sorted(tag for tag in tags if tag not in ORDERED_TAGS)
        return ", ".join(ordered + extras)
