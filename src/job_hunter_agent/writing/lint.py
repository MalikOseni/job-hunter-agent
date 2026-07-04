from __future__ import annotations
from dataclasses import dataclass

from ..config import WritingRulesConfig


class WritingLintError(ValueError):
    """Raised when generated writing fails lint checks."""


@dataclass(frozen=True)
class LintIssue:
    code: str
    message: str
    matched_text: str


@dataclass(frozen=True)
class LintResult:
    valid: bool
    issues: list[LintIssue]


def lint_text(text: str, rules: WritingRulesConfig) -> LintResult:
    issues: list[LintIssue] = []
    value = text or ""
    lowered = value.lower()

    for punctuation in rules.banned_punctuation:
        if punctuation and punctuation in value:
            issues.append(
                LintIssue(
                    code="forbidden_punctuation",
                    message=f"Forbidden punctuation detected: {punctuation}",
                    matched_text=punctuation,
                )
            )

    if rules.ban_em_dash and "—" in value:
        issues.append(
            LintIssue(
                code="em_dash_not_allowed",
                message="Em dash punctuation is not allowed by writing policy.",
                matched_text="—",
            )
        )

    if rules.ban_ai_tells:
        for phrase in rules.banned_phrases:
            phrase_clean = phrase.strip().lower()
            if phrase_clean and phrase_clean in lowered:
                issues.append(
                    LintIssue(
                        code="banned_phrase",
                        message=f"Banned phrase detected: {phrase}",
                        matched_text=phrase,
                    )
                )

    stripped = value.strip().lower()
    for opener in rules.banned_openers:
        opener_clean = opener.strip().lower()
        if opener_clean and stripped.startswith(opener_clean):
            issues.append(
                LintIssue(
                    code="banned_opener",
                    message=f"Banned opener detected: {opener}",
                    matched_text=opener,
                )
            )

    deduped = _dedupe_issues(issues)
    return LintResult(valid=not deduped, issues=deduped)


def assert_text_compliant(text: str, rules: WritingRulesConfig) -> None:
    result = lint_text(text, rules)
    if result.valid:
        return
    summary = "; ".join(f"{issue.code}:{issue.matched_text}" for issue in result.issues)
    raise WritingLintError(summary)


def _dedupe_issues(issues: list[LintIssue]) -> list[LintIssue]:
    seen: set[tuple[str, str]] = set()
    deduped: list[LintIssue] = []
    for issue in issues:
        key = (issue.code, issue.matched_text.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped
