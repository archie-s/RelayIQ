from __future__ import annotations

import unittest

from app import judge
from app.privacy import inspect_prompt


class PrivacyGateTests(unittest.TestCase):
    def test_sensitive_emergency_requires_review_and_redacts(self) -> None:
        verdict = judge.enrich("critical", "high", False, "medical")

        result = inspect_prompt(
            "Emergency for employee id EMP-42: email me at person@example.com, SSN 123-45-6789",
            verdict,
        )

        self.assertTrue(result["emergency_override"])
        self.assertTrue(result["requires_human_review"])
        self.assertNotIn("person@example.com", result["redacted_prompt"])
        self.assertNotIn("123-45-6789", result["redacted_prompt"])
        self.assertNotIn("employee id", result["redacted_prompt"].lower())
        self.assertNotIn("EMP-42", result["redacted_prompt"])

    def test_sensitive_non_emergency_requires_review(self) -> None:
        verdict = judge.enrich("general", "high", False, "enterprise")

        result = inspect_prompt("Review payroll for account number 99881", verdict)

        self.assertTrue(result["sensitive"])
        self.assertTrue(result["requires_human_review"])
        self.assertIn("[REDACTED_ACCOUNT_NUMBER]", result["redacted_prompt"])
        self.assertNotIn("99881", result["redacted_prompt"])

    def test_non_sensitive_prompt_does_not_require_review(self) -> None:
        verdict = judge.enrich("general", "high", False, "education")

        result = inspect_prompt("Compare three ways to teach fractions", verdict)

        self.assertFalse(result["sensitive"])
        self.assertFalse(result["requires_human_review"])


if __name__ == "__main__":
    unittest.main()
