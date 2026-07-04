from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import ProfileConfig
from ..types import JobRecord
from ..salary_policy import SalaryDecision


DEFAULT_COVER_LETTER_TEMPLATE = (
    "Hello {company} hiring team,\n\n"
    "I am applying for the {title} role. My background in identity, endpoint, "
    "and modern workplace engineering aligns with your requirements.\n\n"
    "Location preference: {location}\n"
    "Notice period: {notice_weeks} week(s)\n"
    "Target compensation: {target_salary}\n\n"
    "Best regards,\n"
    "{candidate_name}"
)


@dataclass(frozen=True)
class TemplateContext:
    values: dict[str, Any]


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def build_template_context(
    *,
    profile: ProfileConfig,
    job: JobRecord,
    notice_weeks: int,
    salary_decision: SalaryDecision | None,
) -> TemplateContext:
    target_salary = "market rate"
    if salary_decision is not None:
        target_salary = (
            f"{salary_decision.currency} {salary_decision.target_amount:,.0f} "
            f"({salary_decision.reason_code})"
        )
    return TemplateContext(
        values={
            "candidate_name": profile.full_name,
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "notice_weeks": notice_weeks,
            "target_salary": target_salary,
        }
    )


def render_template(template: str, context: TemplateContext) -> str:
    return template.format_map(_SafeDict(context.values))
