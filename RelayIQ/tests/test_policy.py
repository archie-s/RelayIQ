from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import config, foundry, judge
from app.policy import allocate, decision_record
from app.privacy import inspect_prompt


class PolicyDecisionTests(unittest.TestCase):
    def test_fresh_cloud_request_requires_web_search(self) -> None:
        create = Mock(return_value=SimpleNamespace(output_text="Current answer"))
        client = SimpleNamespace(responses=SimpleNamespace(create=create))

        with (
            patch.object(config, "cloud_is_real", return_value=True),
            patch.object(foundry, "_azure_client", return_value=client),
        ):
            foundry.chat_cloud(
                [{"role": "user", "content": "Who won the 2026 FIFA World Cup?"}],
                use_web_search=True,
            )

        request = create.call_args.kwargs
        self.assertEqual([{"type": "web_search"}], request["tools"])
        self.assertEqual("required", request["tool_choice"])
        self.assertIn("current date", request["instructions"])
        self.assertIn("latest authoritative source", request["instructions"])

    def test_deep_reasoning_cloud_request_does_not_enable_web_search(self) -> None:
        create = Mock(return_value=SimpleNamespace(output_text="Reasoned answer"))
        client = SimpleNamespace(responses=SimpleNamespace(create=create))

        with (
            patch.object(config, "cloud_is_real", return_value=True),
            patch.object(foundry, "_azure_client", return_value=client),
        ):
            foundry.chat_cloud(
                [{"role": "user", "content": "Compare three rollout plans."}],
            )

        request = create.call_args.kwargs
        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)

    def test_responses_endpoint_is_normalized_to_v1_base_url(self) -> None:
        with patch.object(
            config,
            "AZURE_OPENAI_ENDPOINT",
            "https://example.services.ai.azure.com/openai/v1/responses",
        ):
            self.assertEqual(
                "https://example.services.ai.azure.com/openai/v1/",
                config.azure_openai_base_url(),
            )

    def test_policy_engine_is_authoritative_for_placement(self) -> None:
        stale_verdict = {
            "urgency": "general",
            "complexity": "low",
            "needs_fresh": True,
            "route": "local",
        }

        placement = allocate(stale_verdict)

        self.assertEqual("cloud", placement["route"])
        self.assertEqual("Fresh Knowledge v1", placement["policy"])

    def test_local_first_explains_on_device_placement(self) -> None:
        verdict = judge.enrich("general", "low", False, "education")
        privacy = inspect_prompt("Explain fractions", verdict)

        decision = decision_record(verdict, privacy, "answered", True)

        self.assertEqual("Local First v1", decision["policy"])
        self.assertEqual("Local", decision["placement"])
        self.assertIn("on device", decision["action"])

    def test_fresh_request_explains_cloud_placement(self) -> None:
        verdict = judge.enrich("general", "low", True, "travel")
        privacy = inspect_prompt("Which trains run today?", verdict)

        decision = decision_record(verdict, privacy, "queued", True)

        self.assertEqual("Fresh Knowledge v1", decision["policy"])
        self.assertEqual("Cloud", decision["placement"])
        self.assertIn("current information", decision["reason"])
        self.assertIn("Web search: required", decision["factors"])

    def test_sensitive_request_explains_sanitized_forwarding_without_pii(self) -> None:
        prompt = "Review account number 99881 for person@example.com"
        verdict = judge.enrich("general", "high", False, "finance")
        privacy = inspect_prompt(prompt, verdict)

        decision = decision_record(verdict, privacy, "done", True, True)
        rendered = " ".join(str(value) for value in decision.values())

        self.assertEqual("Privacy Guard v2", decision["policy"])
        self.assertEqual("Hybrid", decision["placement"])
        self.assertIn("removed detected identifiers", decision["action"])
        self.assertNotIn("99881", rendered)
        self.assertNotIn("person@example.com", rendered)

    def test_offline_cloud_request_explains_resilient_fallback(self) -> None:
        verdict = judge.enrich("general", "high", False, "planning")
        privacy = inspect_prompt("Design an irrigation plan", verdict)

        decision = decision_record(verdict, privacy, "answered-offline", False)

        self.assertEqual("Resilient Fallback v1", decision["policy"])
        self.assertEqual("Local now, cloud later", decision["placement"])


if __name__ == "__main__":
    unittest.main()
