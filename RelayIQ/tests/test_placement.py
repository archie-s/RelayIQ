from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import UUID

from app import judge
from app.placement import PlacementContext, PlacementEngine


class PlacementEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PlacementEngine()

    def _decide(
        self,
        verdict: dict,
        text: str = "Analyze this problem.",
        context: PlacementContext | None = None,
    ):
        with patch("app.placement.judge.classify", return_value=(verdict, 0)):
            return self.engine.decide(text, context)

    def test_local_decision_uses_only_ordered_unique_local_models(self) -> None:
        verdict = judge.enrich("general", "low", False, "education")
        decision = self._decide(
            verdict,
            context=PlacementContext(
                available_local=("local-B", "local-A", "local-B"),
                available_cloud=("cloud-A",),
            ),
        )

        self.assertEqual("local", decision.placement)
        self.assertEqual(("local-B", "local-A"), decision.suggested_models)
        self.assertEqual(
            ("LOCAL_CAPABILITY_SUFFICIENT",),
            tuple(code.value for code in decision.reason_codes),
        )
        self.assertFalse(decision.requires_consent)
        self.assertEqual("local", decision.fallback)

    def test_cloud_decision_uses_only_ordered_unique_cloud_models(self) -> None:
        verdict = judge.enrich("general", "high", False, "analysis")
        decision = self._decide(
            verdict,
            context=PlacementContext(
                available_local=("local-A",),
                available_cloud=("Cloud-X", "cloud-x", "Cloud-X"),
            ),
        )

        self.assertEqual("cloud", decision.placement)
        self.assertEqual(("Cloud-X", "cloud-x"), decision.suggested_models)
        self.assertEqual(("CLOUD_CAPABILITY_REQUIRED",), tuple(
            code.value for code in decision.reason_codes
        ))
        self.assertFalse(decision.requires_consent)
        self.assertEqual("local", decision.fallback)

    def test_sensitive_cloud_decision_requires_consent_and_redacts_payload(self) -> None:
        verdict = judge.enrich("general", "high", False, "employee review")
        decision = self._decide(
            verdict,
            text="Review employee id EMP-20431 and salary.",
            context=PlacementContext(available_cloud=("cloud-A",)),
        )

        self.assertEqual("cloud_with_consent", decision.placement)
        self.assertTrue(decision.requires_consent)
        self.assertNotIn("EMP-20431", decision.permitted_payload)
        self.assertNotIn("EMP-20431", decision.explanation)
        self.assertEqual(
            ("CLOUD_CAPABILITY_REQUIRED", "SENSITIVE_DATA_DETECTED"),
            tuple(code.value for code in decision.reason_codes),
        )

    def test_connectivity_and_budget_constraints_are_resolved_before_consent(self) -> None:
        verdict = judge.enrich("general", "high", False, "employee review")
        decision = self._decide(
            verdict,
            text="Review employee id EMP-20431 and salary.",
            context=PlacementContext(
                connectivity=False,
                cloud_budget_kb=0,
                available_local=("local-A",),
                available_cloud=("cloud-A",),
            ),
        )

        self.assertEqual("local", decision.placement)
        self.assertFalse(decision.requires_consent)
        self.assertEqual(("local-A",), decision.suggested_models)
        self.assertEqual(
            (
                "CLOUD_CAPABILITY_REQUIRED",
                "SENSITIVE_DATA_DETECTED",
                "CONNECTIVITY_UNAVAILABLE",
                "CLOUD_BUDGET_EXCEEDED",
            ),
            tuple(code.value for code in decision.reason_codes),
        )

    def test_cloud_preference_is_deferred_when_budget_is_insufficient(self) -> None:
        verdict = judge.enrich("general", "high", False, "analysis")
        decision = self._decide(
            verdict,
            context=PlacementContext(
                cloud_budget_kb=0,
                available_local=("local-A",),
                available_cloud=("cloud-A", "cloud-A", "cloud-B"),
            ),
        )

        self.assertEqual("deferred", decision.placement)
        self.assertFalse(decision.requires_consent)
        self.assertEqual(("cloud-A", "cloud-B"), decision.suggested_models)
        self.assertIn("CLOUD_BUDGET_EXCEEDED", decision.reason_codes)

    def test_empty_relevant_model_list_adds_no_eligible_model(self) -> None:
        verdict = judge.enrich("general", "high", False, "analysis")
        decision = self._decide(
            verdict,
            context=PlacementContext(available_local=("local-A",)),
        )

        self.assertEqual((), decision.suggested_models)
        self.assertEqual(
            ("CLOUD_CAPABILITY_REQUIRED", "NO_ELIGIBLE_MODEL"),
            tuple(code.value for code in decision.reason_codes),
        )

    def test_classifier_fallback_is_reported_without_exposing_private_diagnostics(self) -> None:
        with patch("app.foundry.chat_local", side_effect=RuntimeError("local unavailable")):
            decision = self.engine.decide(
                "Explain quadratic equations.",
                PlacementContext(available_local=("local-A",)),
            )

        self.assertEqual("local", decision.placement)
        self.assertIn("CLASSIFIER_DEGRADED", decision.reason_codes)
        self.assertTrue(decision.degraded)
        self.assertIn("total", decision.timings_ms)
        self.assertNotIn("timings_ms", decision.to_public_dict())
        self.assertNotIn("verdict", decision.to_public_dict())

    def test_decision_ids_are_unique_uuid_values(self) -> None:
        verdict = judge.enrich("general", "low", False, "education")
        first = self._decide(verdict, context=PlacementContext(available_local=("local-A",)))
        second = self._decide(verdict, context=PlacementContext(available_local=("local-A",)))

        self.assertNotEqual(first.decision_id, second.decision_id)
        self.assertEqual(first.decision_id, UUID(str(first.decision_id)))
        self.assertEqual(second.decision_id, UUID(str(second.decision_id)))


if __name__ == "__main__":
    unittest.main()
