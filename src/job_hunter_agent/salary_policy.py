from __future__ import annotations

import re
from dataclasses import dataclass

from .config import PolicyConfig, ProfileConfig
from .models import canonical_job_key
from .types import JobRecord

_SALARY_RANGE_RE = re.compile(
    r"(?P<currency>[£$€])\s*(?P<low>\d{2,3})(?:k|,?000)\s*(?:-|to)\s*"
    r"(?P<high>\d{2,3})(?:k|,?000)",
    re.IGNORECASE,
)

_ROLE_BENCHMARKS_GBP: dict[str, tuple[float, float, float]] = {
    "principal_engineer": (90000.0, 110000.0, 130000.0),
    "manager": (85000.0, 100000.0, 120000.0),
    "senior_engineer": (70000.0, 85000.0, 100000.0),
    "identity_security": (75000.0, 90000.0, 110000.0),
    "support": (45000.0, 55000.0, 65000.0),
    "default": (60000.0, 75000.0, 90000.0),
}


@dataclass(frozen=True)
class SalaryDecision:
    external_key: str
    strategy: str
    reason_code: str
    currency: str
    target_amount: float
    benchmark_low: float | None
    benchmark_median: float | None
    benchmark_high: float | None
    role_family: str
    market_region: str


def build_salary_decisions(
    jobs: list[JobRecord],
    *,
    policy: PolicyConfig,
    profile: ProfileConfig,
) -> dict[str, SalaryDecision]:
    decisions: dict[str, SalaryDecision] = {}
    for job in jobs:
        decision = decide_salary(job, policy=policy, profile=profile)
        decisions[decision.external_key] = decision
    return decisions


def decide_salary(
    job: JobRecord,
    *,
    policy: PolicyConfig,
    profile: ProfileConfig,
) -> SalaryDecision:
    external_key = canonical_job_key(job)
    combined_text = " ".join(
        [
            job.get("title", "") or "",
            job.get("location", "") or "",
            job.get("tags", "") or "",
            job.get("skills", "") or "",
            job.get("source", "") or "",
        ]
    )
    parsed_band = _extract_salary_band(combined_text)
    role_family = infer_role_family(job.get("title", ""))
    market_region = infer_market_region(job.get("location", ""), profile)

    if parsed_band is not None and policy.salary_default_offer_strategy == "top_of_range_if_provided":
        currency, low, high = parsed_band
        return SalaryDecision(
            external_key=external_key,
            strategy=policy.salary_default_offer_strategy,
            reason_code="salary_band_top_selected",
            currency=currency,
            target_amount=high,
            benchmark_low=low,
            benchmark_median=(low + high) / 2.0,
            benchmark_high=high,
            role_family=role_family,
            market_region=market_region,
        )

    benchmark_low, benchmark_median, benchmark_high = estimate_market_rate_stub(
        role_family=role_family,
        currency=policy.salary_currency,
    )
    uplift_multiplier = 1.0 + max(policy.salary_market_rate_uplift_percent, 0.0) / 100.0
    target_amount = round(benchmark_high * uplift_multiplier, 2)
    return SalaryDecision(
        external_key=external_key,
        strategy=policy.salary_fallback_strategy,
        reason_code="market_rate_estimate_stub",
        currency=policy.salary_currency,
        target_amount=target_amount,
        benchmark_low=benchmark_low,
        benchmark_median=benchmark_median,
        benchmark_high=benchmark_high,
        role_family=role_family,
        market_region=market_region,
    )


def infer_role_family(title: str) -> str:
    lowered = (title or "").lower()
    if any(token in lowered for token in ("principal", "staff", "architect")):
        return "principal_engineer"
    if any(token in lowered for token in ("manager", "head", "director", "lead")):
        return "manager"
    if any(token in lowered for token in ("identity", "iam", "security")):
        return "identity_security"
    if any(token in lowered for token in ("support", "helpdesk", "technician")):
        return "support"
    if "senior" in lowered:
        return "senior_engineer"
    return "default"


def infer_market_region(location: str, profile: ProfileConfig) -> str:
    lowered = (location or "").lower()
    if "remote" in lowered:
        return "remote"
    if "united kingdom" in lowered or "uk" in lowered or "london" in lowered:
        return "united_kingdom"
    if "canada" in lowered:
        return "canada"
    if "australia" in lowered:
        return "australia"
    return (profile.location or "global").replace(" ", "_").lower()


def estimate_market_rate_stub(
    *,
    role_family: str,
    currency: str,
) -> tuple[float, float, float]:
    if currency != "GBP":
        low, median, high = _ROLE_BENCHMARKS_GBP.get(
            role_family,
            _ROLE_BENCHMARKS_GBP["default"],
        )
        return low, median, high
    return _ROLE_BENCHMARKS_GBP.get(role_family, _ROLE_BENCHMARKS_GBP["default"])


def summarize_salary_decisions(decisions: dict[str, SalaryDecision]) -> str:
    if not decisions:
        return "none"
    reasons: dict[str, int] = {}
    for decision in decisions.values():
        reasons[decision.reason_code] = reasons.get(decision.reason_code, 0) + 1
    parts = [f"{reason}:{count}" for reason, count in sorted(reasons.items())]
    return ", ".join(parts)


def _extract_salary_band(text: str) -> tuple[str, float, float] | None:
    match = _SALARY_RANGE_RE.search(text or "")
    if not match:
        return None
    symbol = match.group("currency")
    currency = {"£": "GBP", "$": "USD", "€": "EUR"}.get(symbol, "GBP")
    low = _thousands(match.group("low"))
    high = _thousands(match.group("high"))
    if high < low:
        low, high = high, low
    return currency, low, high


def _thousands(value: str) -> float:
    numeric = float(value)
    if numeric < 1000:
        return numeric * 1000.0
    return numeric
