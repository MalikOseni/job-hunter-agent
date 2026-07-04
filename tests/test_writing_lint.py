from __future__ import annotations

import unittest

from job_hunter_agent.config import WritingRulesConfig
from job_hunter_agent.writing.lint import WritingLintError, assert_text_compliant, lint_text


def _rules() -> WritingRulesConfig:
    return WritingRulesConfig(
        ban_em_dash=True,
        ban_ai_tells=True,
        banned_punctuation=("—",),
        banned_phrases=("as an ai", "ai-generated"),
        banned_openers=("dear hiring manager",),
    )


class WritingLintTests(unittest.TestCase):
    def test_lint_detects_banned_phrase_and_punctuation(self) -> None:
        result = lint_text(
            "Dear Hiring Manager, as an AI — I am applying for this role.",
            _rules(),
        )
        self.assertFalse(result.valid)
        codes = {issue.code for issue in result.issues}
        self.assertIn("banned_phrase", codes)
        self.assertIn("em_dash_not_allowed", codes)
        self.assertIn("banned_opener", codes)

    def test_assert_text_compliant_raises(self) -> None:
        with self.assertRaises(WritingLintError):
            assert_text_compliant("This is AI-generated content.", _rules())

    def test_clean_text_passes(self) -> None:
        result = lint_text(
            "Hello team, I am applying for the role and can start in two weeks.",
            _rules(),
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.issues, [])


if __name__ == "__main__":
    unittest.main()
