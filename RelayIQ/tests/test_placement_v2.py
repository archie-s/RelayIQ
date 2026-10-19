from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import foundry, judge, policy
from app.api import POLICY_CONFIG_ENV, PlacementResponseV2, create_app
from app.classification import JudgeAssessment, JudgeClassification
from app.placement import PlacementServiceUnavailableError

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "relayiq-policy.json"
ERROR_FIELDS = {"error", "message", "decision_id"}
REQUIRED_SUCCESS_FIELDS = {
    "decision_id",
    "target_class",
    "policy_revision",
    "requirements",
    "fallback_action",
    "reason_codes",
}


def local_verdict() -> dict:
    return {
        **judge.enrich("general", "low", False, "arithmetic"),
        "judge_mode": "model",
        "classification": {
            "routine": True,
            "fresh_knowledge": False,
            "reasoning_heavy": False,
            "critical": False,
            "sensitive_data": False,
        },
    }


def cloud_verdict(
    *,
    requires_consent: bool = False,
    sensitive_data_types: list[str] | None = None,
) -> dict:
    verdict = {
        **judge.enrich(
            "general",
            "high",
            False,
            "analysis",
            requires_consent=requires_consent,
        ),
        "judge_mode": "model",
        "classification": {
            "routine": False,
            "fresh_knowledge": False,
            "reasoning_heavy": True,
            "critical": False,
            "sensitive_data": sensitive_data_types is not None,
        },
    }
    if sensitive_data_types is not None:
        verdict["sensitive_data_types"] = sensitive_data_types
    return verdict


class PlacementV2ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = create_app(POLICY_PATH)

    def _post(self, payload: dict, verdict: dict | None = None):
        classifier_fixture = verdict or local_verdict()
        with (
            patch(
                "app.placement.judge.classify",
                return_value=(classifier_fixture, 0),
            ),
            TestClient(self.api) as client,
        ):
            return client.post("/v2/placement/decide", json=payload)

    def test_poc_scenario_outcomes(self) -> None:
        local_mapping = {
            "local": ["phi-4-mini"],
            "cloud": ["nvidia/nemotron-3.5-lightning-30b-a3b"],
        }
        cloud_context = {}
        cases = (
            (
                "POC-01",
                {
                    "input": "What is 12 + 15? Reply with only the number.",
                    "preferred_target_class": "local",
                    "model_mapping": local_mapping,
                    "context": cloud_context,
                },
                local_verdict(),
                "local",
                "local",
                ["phi-4-mini"],
                {"PREFERENCE_HONORED"},
            ),
            (
                "POC-02",
                {
                    "input": (
                        "For Alex Example (alex.example@example.com), read "
                        "demo_employee_record.txt using read_file, then report only the balance."
                    ),
                    "preferred_target_class": "cloud",
                    "model_mapping": local_mapping,
                    "context": cloud_context,
                },
                cloud_verdict(
                    requires_consent=True,
                    sensitive_data_types=["email_address", "employee_id"],
                ),
                "cloud_with_consent",
                "local",
                ["nvidia/nemotron-3.5-lightning-30b-a3b"],
                {"PREFERENCE_HONORED", "SENSITIVE_DATA_DETECTED"},
            ),
            (
                "POC-03",
                {
                    "input": "Demonstrate the configured demo block.",
                    "model_mapping": local_mapping,
                    "context": cloud_context,
                },
                local_verdict(),
                "fallback",
                "stop",
                [],
                {"POLICY_BLOCKED"},
            ),
            (
                "POC-04",
                {
                    "input": "What is 12 + 15? Reply with only the number.",
                    "preferred_target_class": "cloud",
                    "model_mapping": local_mapping,
                    "context": {
                        "connectivity": False,
                    },
                },
                local_verdict(),
                "local",
                "local",
                ["phi-4-mini"],
                {"CONNECTIVITY_UNAVAILABLE", "PREFERENCE_OVERRIDDEN"},
            ),
            (
                "POC-10",
                {
                    "input": "What is 12 + 15? Reply with only the number.",
                    "preferred_target_class": "cloud",
                    "model_mapping": local_mapping,
                    "context": {
                        "cloud_budget_kb": 0,
                    },
                },
                local_verdict(),
                "local",
                "local",
                ["phi-4-mini"],
                {"CLOUD_BUDGET_EXCEEDED", "PREFERENCE_OVERRIDDEN"},
            ),
            (
                "POC-11",
                {
                    "input": "What is 12 + 15? Reply with only the number.",
                    "model_mapping": {},
                    "context": cloud_context,
                },
                local_verdict(),
                "fallback",
                "stop",
                [],
                {"NO_ELIGIBLE_MODEL"},
            ),
        )

        for (
            scenario,
            request,
            verdict,
            target,
            fallback,
            models,
            reasons,
        ) in cases:
            with self.subTest(scenario=scenario):
                response = self._post(request, verdict)
                self.assertEqual(200, response.status_code)
                payload = response.json()
                self.assertTrue(REQUIRED_SUCCESS_FIELDS.issubset(payload))
                self.assertEqual("relayiq-judge-v1", payload["policy_revision"])
                self.assertEqual(target, payload["target_class"])
                self.assertEqual(fallback, payload["fallback_action"])
                self.assertEqual(
                    {"local": [], "cloud": []} if target == "fallback" else local_mapping,
                    payload["suggested_models"],
                )
                if target != "fallback":
                    lane = "local" if target == "local" else "cloud"
                    self.assertEqual(models, payload["suggested_models"][lane])
                self.assertTrue(reasons.issubset(payload["reason_codes"]))
                if target in {"cloud", "cloud_with_consent"}:
                    self.assertEqual(
                        {
                            "consent_required": target == "cloud_with_consent",
                        },
                        payload["requirements"],
                    )
                else:
                    self.assertEqual(
                        {
                            "consent_required": False,
                        },
                        payload["requirements"],
                    )

    def test_omitted_mapping_omits_suggested_models(self) -> None:
        for verdict, target in (
            (local_verdict(), "local"),
            (cloud_verdict(), "cloud"),
            (cloud_verdict(requires_consent=True), "cloud_with_consent"),
        ):
            for degraded in (False, True):
                with self.subTest(target=target, degraded=degraded):
                    fixture = deepcopy(verdict)
                    if degraded:
                        fixture["judge_mode"] = "fallback"
                        fixture.pop("classification")
                    response = self._post({"input": "Explain a loop."}, fixture)
                    self.assertEqual(200, response.status_code)
                    payload = response.json()
                    self.assertEqual(target, payload["target_class"])
                    self.assertNotIn("suggested_models", payload)
                    self.assertEqual(degraded, "CLASSIFIER_DEGRADED" in payload["reason_codes"])
                    self.assertEqual(not degraded, "classification" in payload)

    def test_suggestions_preserve_both_lanes_independent_of_recommendation(self) -> None:
        mapping = {
            "local": ["shared-model", "local-B", "local-A"],
            "cloud": ["cloud-B", "shared-model", "cloud-A"],
        }
        for preferred, connectivity, consent, degraded in (
            ("local", True, False, False),
            ("local", False, False, False),
            ("cloud", True, False, False),
            ("cloud", True, True, False),
            ("cloud", False, False, False),
            ("local", True, False, True),
        ):
            with self.subTest(
                preferred=preferred, connectivity=connectivity, consent=consent, degraded=degraded,
            ):
                verdict = cloud_verdict(requires_consent=consent)
                if degraded:
                    verdict.pop("classification")
                    verdict["judge_mode"] = "fallback"
                response = self._post(
                    {
                        "input": "Analyze this.",
                        "preferred_target_class": preferred,
                        "model_mapping": mapping,
                        "context": {"connectivity": connectivity},
                    },
                    verdict,
                )
                self.assertEqual(200, response.status_code)
                payload = response.json()
                target = "local" if preferred == "local" or not connectivity else (
                    "cloud_with_consent" if consent else "cloud"
                )
                self.assertEqual(target, payload["target_class"])
                self.assertEqual(mapping, payload["suggested_models"])

    def test_sensitivity_is_independent_of_consent_and_target(self) -> None:
        for preferred in ("local", "cloud"):
            for consent in (False, True):
                for categories in (None, ["email_address", "novel_sensitive_category"]):
                    with self.subTest(preferred=preferred, consent=consent, categories=categories):
                        verdict = cloud_verdict(
                            requires_consent=consent, sensitive_data_types=categories
                        )
                        response = self._post(
                            {"input": "Review the supplied record.", "preferred_target_class": preferred},
                            verdict,
                        )
                        self.assertEqual(200, response.status_code)
                        payload = response.json()
                        target = "cloud_with_consent" if preferred == "cloud" and consent else preferred
                        self.assertEqual(target, payload["target_class"])
                        self.assertEqual(verdict["classification"], payload["classification"])
                        self.assertEqual(
                            categories is not None,
                            "SENSITIVE_DATA_DETECTED" in payload["reason_codes"],
                        )
                        if categories is None:
                            self.assertNotIn("sensitive_data_types", payload)
                        else:
                            self.assertEqual(categories, payload["sensitive_data_types"])

    def test_policy_blocks_preserve_assessment_and_mapping_presence(self) -> None:
        degraded = {**local_verdict(), "judge_mode": "fallback"}
        degraded.pop("classification")
        for mapped in (False, True):
            for verdict in (
                local_verdict(),
                cloud_verdict(sensitive_data_types=["employee_id"]),
                degraded,
            ):
                with self.subTest(mapped=mapped, verdict=verdict):
                    request = {"input": "Demonstrate the configured demo block."}
                    if mapped:
                        request["model_mapping"] = {"local": ["local"], "cloud": ["cloud"]}
                    response = self._post(request, verdict)
                    self.assertEqual(200, response.status_code)
                    payload = response.json()
                    self.assertEqual("fallback", payload["target_class"])
                    self.assertEqual("stop", payload["fallback_action"])
                    self.assertIn("POLICY_BLOCKED", payload["reason_codes"])
                    self.assertEqual(mapped, "suggested_models" in payload)
                    if mapped:
                        self.assertEqual({"local": [], "cloud": []}, payload["suggested_models"])
                    if verdict["judge_mode"] == "fallback":
                        self.assertNotIn("classification", payload)
                        self.assertNotIn("sensitive_data_types", payload)
                        self.assertNotIn("SENSITIVE_DATA_DETECTED", payload["reason_codes"])
                        self.assertIn("CLASSIFIER_DEGRADED", payload["reason_codes"])
                    else:
                        self.assertEqual(verdict["classification"], payload["classification"])
                        self.assertNotIn("CLASSIFIER_DEGRADED", payload["reason_codes"])
                        self.assertEqual(
                            verdict["classification"]["sensitive_data"],
                            "SENSITIVE_DATA_DETECTED" in payload["reason_codes"],
                        )
                        if "sensitive_data_types" in verdict:
                            self.assertEqual(verdict["sensitive_data_types"], payload["sensitive_data_types"])

    def test_real_judge_engine_api_healthy_assessments(self) -> None:
        for sensitive in (False, True):
            for consent in (False, True):
                with self.subTest(sensitive=sensitive, consent=consent):
                    categories = ["email_address"] if sensitive else None
                    verdict = cloud_verdict(
                        requires_consent=consent, sensitive_data_types=categories
                    )
                    model_output = {
                        key: value for key, value in verdict.items()
                        if key not in {"judge_mode", "route", "tier", "priority"}
                    }
                    with (
                        patch.object(foundry, "chat_local", return_value=(json.dumps(model_output), 42)) as chat,
                        patch.object(foundry, "chat_cloud", side_effect=AssertionError("answer call")) as cloud,
                        TestClient(self.api) as client,
                    ):
                        response = client.post(
                            "/v2/placement/decide",
                            json={"input": "Review alex.example@example.com and EMP-20431."},
                        )
                    self.assertEqual(200, response.status_code)
                    payload = response.json()
                    self.assertEqual("cloud_with_consent" if consent else "cloud", payload["target_class"])
                    self.assertEqual(verdict["classification"], payload["classification"])
                    self.assertNotIn("CLASSIFIER_DEGRADED", payload["reason_codes"])
                    self.assertEqual(sensitive, "SENSITIVE_DATA_DETECTED" in payload["reason_codes"])
                    self.assertEqual(categories, payload.get("sensitive_data_types"))
                    self.assertNotIn("suggested_models", payload)
                    self.assertNotIn("alex.example@example.com", response.text)
                    self.assertNotIn("EMP-20431", response.text)
                    chat.assert_called_once()
                    self.assertEqual(160, chat.call_args.kwargs["max_tokens"])
                    cloud.assert_not_called()

    def test_real_judge_engine_api_degrades_malformed_and_legacy_output(self) -> None:
        for output in (
            "RAW_SECRET_CANARY {not-json",
            json.dumps(judge.enrich("general", "low", False, "RAW_SECRET_CANARY")),
            json.dumps({**cloud_verdict(), "sensitive_data_types": ["RAW_SECRET_CANARY"]}),
            json.dumps(cloud_verdict(sensitive_data_types=["employee_id"])),
            json.dumps(cloud_verdict(sensitive_data_types=["social_security_number"])),
            json.dumps(cloud_verdict(sensitive_data_types=["email_address", "employee_id"])),
        ):
            with self.subTest(output=output):
                with (
                    patch.object(foundry, "chat_local", return_value=(output, 9)) as chat,
                    patch.object(foundry, "chat_cloud", side_effect=AssertionError("answer call")) as cloud,
                    self.assertLogs("app.judge", level="WARNING") as captured,
                    TestClient(self.api) as client,
                ):
                    response = client.post(
                        "/v2/placement/decide",
                        json={
                            "input": "Review employee id EMP-20431 and salary.",
                            "preferred_target_class": "cloud",
                        },
                    )
                self.assertEqual(200, response.status_code)
                payload = response.json()
                self.assertEqual("cloud_with_consent", payload["target_class"])
                self.assertIn("CLASSIFIER_DEGRADED", payload["reason_codes"])
                self.assertNotIn("classification", payload)
                self.assertNotIn("sensitive_data_types", payload)
                self.assertNotIn("SENSITIVE_DATA_DETECTED", payload["reason_codes"])
                self.assertNotIn("RAW_SECRET_CANARY", response.text)
                self.assertNotIn("RAW_SECRET_CANARY", repr([record.__dict__ for record in captured.records]))
                self.assertNotIn("EMP-20431", repr([record.__dict__ for record in captured.records]))
                chat.assert_called_once()
                cloud.assert_not_called()

    def test_compact_judge_signals_preserve_email_scope_and_independent_consent(self) -> None:
        for email in (False, True):
            for consent in (False, True):
                signals = {
                    "routine": False, "fresh_knowledge": False,
                    "reasoning_heavy": True, "critical": False,
                    "email_detected": email, "requires_consent": consent,
                }
                with (
                    self.subTest(email=email, consent=consent),
                    patch.object(foundry, "chat_local", return_value=(json.dumps(signals), 8)) as chat,
                    patch.object(foundry, "chat_cloud", side_effect=AssertionError("answer call")) as cloud,
                    TestClient(self.api) as client,
                ):
                    response = client.post(
                        "/v2/placement/decide", json={"input": "Review the supplied record."},
                    )
                self.assertEqual(200, response.status_code)
                payload = response.json()
                self.assertEqual("cloud_with_consent" if consent else "cloud", payload["target_class"])
                self.assertEqual(email, payload["classification"]["sensitive_data"])
                self.assertEqual(email, "SENSITIVE_DATA_DETECTED" in payload["reason_codes"])
                self.assertNotIn("CLASSIFIER_DEGRADED", payload["reason_codes"])
                self.assertNotIn("email_detected", payload)
                if email:
                    self.assertEqual(["email_address"], payload["sensitive_data_types"])
                else:
                    self.assertNotIn("sensitive_data_types", payload)
                self.assertEqual(160, chat.call_args.kwargs["max_tokens"])
                self.assertEqual(judge.config.JUDGE_TIMEOUT_SECONDS, chat.call_args.kwargs["timeout_seconds"])
                cloud.assert_not_called()

    def test_healthy_v2_does_not_inspect_or_redact_prompt(self) -> None:
        with patch(
            "app.placement.privacy.inspect_prompt",
            side_effect=AssertionError("v2 must not inspect or redact"),
        ) as inspect:
            response = self._post(
                {"input": "Employee id EMP-20431, alex.example@example.com."},
                cloud_verdict(),
            )
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()["classification"]["sensitive_data"])
        self.assertNotIn("SENSITIVE_DATA_DETECTED", response.json()["reason_codes"])
        inspect.assert_not_called()

    def test_judge_decides_between_cloud_outcomes_from_request_context(self) -> None:
        request = {
            "input": "Review employee id EMP-20431 and salary.",
            "preferred_target_class": "cloud",
            "model_mapping": {"local": ["local"], "cloud": ["cloud"]},
            "context": {
                "connectivity": True,
                "cloud_budget_kb": None,
                "required_capabilities": ["extended_reasoning"],
            },
        }
        expected_context = {
            "connectivity": True,
            "cloud_budget_kb": None,
            "required_capabilities": ["extended_reasoning"],
        }

        for requires_consent, target in (
            (False, "cloud"),
            (True, "cloud_with_consent"),
        ):
            with self.subTest(target=target):
                verdict = cloud_verdict(requires_consent=requires_consent)
                with (
                    patch(
                        "app.placement.judge.classify",
                        return_value=(verdict, 0),
                    ) as classifier_fixture,
                    TestClient(self.api) as client,
                ):
                    response = client.post("/v2/placement/decide", json=request)

                self.assertEqual(200, response.status_code)
                payload = response.json()
                self.assertEqual(target, payload["target_class"])
                self.assertEqual(
                    requires_consent,
                    payload["requirements"]["consent_required"],
                )
                classifier_fixture.assert_called_once_with(
                    request["input"],
                    context=expected_context,
                    force_fallback=False,
                    include_classification=True,
                )

    def test_mapping_order_and_missing_lane_eligibility_are_preserved(self) -> None:
        response = self._post(
            {
                "input": "Analyze this.",
                "preferred_target_class": "cloud",
                "model_mapping": {"local": ["local-B", "local-A"]},
            },
            cloud_verdict(),
        )
        payload = response.json()
        self.assertEqual("local", payload["target_class"])
        self.assertEqual(
            {"local": ["local-B", "local-A"], "cloud": []}, payload["suggested_models"],
        )
        self.assertIn("NO_ELIGIBLE_MODEL", payload["reason_codes"])
        self.assertIn("PREFERENCE_OVERRIDDEN", payload["reason_codes"])

    def test_empty_local_lane_can_select_cloud_alternative(self) -> None:
        response = self._post(
            {
                "input": "Explain a loop.",
                "preferred_target_class": "local",
                "model_mapping": {"local": [], "cloud": ["cloud-B", "cloud-A"]},
            }
        )
        payload = response.json()
        self.assertEqual("cloud", payload["target_class"])
        self.assertEqual(
            {"local": [], "cloud": ["cloud-B", "cloud-A"]}, payload["suggested_models"],
        )
        self.assertEqual("stop", payload["fallback_action"])

    def test_original_utf8_bytes_define_budget_boundary(self) -> None:
        base = {
            "input": "é",
            "preferred_target_class": "cloud",
            "model_mapping": {"local": ["local"], "cloud": ["cloud"]},
            "context": {},
        }
        equality = json.loads(json.dumps(base))
        equality["context"]["cloud_budget_kb"] = 2 / 1024
        exceeded = json.loads(json.dumps(base))
        exceeded["context"]["cloud_budget_kb"] = (2 / 1024) - 0.000001

        cloud_response = self._post(equality)
        local_response = self._post(exceeded)

        self.assertEqual("cloud", cloud_response.json()["target_class"])
        self.assertNotIn(
            "CLOUD_BUDGET_EXCEEDED", cloud_response.json()["reason_codes"]
        )
        self.assertEqual("local", local_response.json()["target_class"])
        self.assertIn(
            "CLOUD_BUDGET_EXCEEDED", local_response.json()["reason_codes"]
        )

    def test_required_capabilities_remain_a_hint(self) -> None:
        request = {
            "input": "Explain a loop.",
            "model_mapping": {"local": ["local"], "cloud": ["cloud"]},
            "context": {
                "required_capabilities": ["unknown-capability"],
            },
        }
        response = self._post(request, local_verdict())
        self.assertEqual("local", response.json()["target_class"])

    def test_policy_target_restriction_does_not_emit_contradictory_reason(self) -> None:
        document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        document["permitted_targets"] = ["local"]
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(json.dumps(document), encoding="utf-8")
            api = create_app(policy_path)

        with (
            patch("app.placement.judge.classify", return_value=(cloud_verdict(), 0)),
            TestClient(api) as client,
        ):
            response = client.post(
                "/v2/placement/decide",
                json={
                    "input": "Analyze this.",
                    "model_mapping": {"local": ["local"], "cloud": ["cloud"]},
                },
            )
        payload = response.json()
        self.assertEqual("local", payload["target_class"])
        self.assertIn("LOCAL_CAPABILITY_SUFFICIENT", payload["reason_codes"])
        self.assertNotIn("CLOUD_CAPABILITY_REQUIRED", payload["reason_codes"])
        self.assertEqual({"local": ["local"], "cloud": ["cloud"]}, payload["suggested_models"])

    def test_cloud_only_policy_without_eligible_cloud_returns_fallback_stop(self) -> None:
        document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        document["permitted_targets"] = ["cloud"]
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(json.dumps(document), encoding="utf-8")
            api = create_app(policy_path)

        with (
            patch("app.placement.judge.classify", return_value=(local_verdict(), 0)),
            TestClient(api, raise_server_exceptions=False) as client,
        ):
            response = client.post(
                "/v2/placement/decide",
                json={"input": "Explain a loop.", "context": {"connectivity": False}},
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("fallback", payload["target_class"])
        self.assertEqual("relayiq-judge-v1", payload["policy_revision"])
        self.assertEqual("stop", payload["fallback_action"])
        self.assertEqual(
            {
                "consent_required": False,
            },
            payload["requirements"],
        )
        self.assertIn("CONNECTIVITY_UNAVAILABLE", payload["reason_codes"])
        self.assertNotIn("suggested_models", payload)

    def test_profile_free_cloud_is_eligible(self) -> None:
        response = self._post(
            {
                "input": "Analyze this.",
                "preferred_target_class": "cloud",
                "model_mapping": {"local": ["local"], "cloud": ["cloud"]},
            },
            cloud_verdict(),
        )
        payload = response.json()
        self.assertEqual("cloud", payload["target_class"])
        self.assertEqual({"consent_required": False}, payload["requirements"])

    def test_local_failure_permission_and_candidate_are_both_required(self) -> None:
        document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        document["local_failure_permitted"] = False
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(json.dumps(document), encoding="utf-8")
            api = create_app(policy_path)

        with (
            patch("app.placement.judge.classify", return_value=(cloud_verdict(), 0)),
            TestClient(api) as client,
        ):
            response = client.post(
                "/v2/placement/decide",
                json={
                    "input": "Analyze this.",
                    "model_mapping": {"local": ["local"], "cloud": ["cloud"]},
                },
            )
        self.assertEqual("cloud", response.json()["target_class"])
        self.assertEqual("stop", response.json()["fallback_action"])

    def test_policy_evaluation_failure_returns_500_without_a_decision(self) -> None:
        degraded = {**local_verdict(), "judge_mode": "fallback"}
        degraded.pop("classification")
        for mapping in (None, {}, {"local": ["local"], "cloud": ["cloud"]}):
            for verdict in (local_verdict(), cloud_verdict(sensitive_data_types=["employee_id"]), degraded):
                for error in (
                    policy.PolicyDecisionUnavailableError("POLICY_EXCEPTION_CANARY"),
                    RuntimeError("POLICY_EXCEPTION_CANARY"),
                ):
                    with self.subTest(mapping=mapping, verdict=verdict, error=type(error).__name__):
                        request = {"input": "PROMPT_CANARY"}
                        if mapping is not None:
                            request["model_mapping"] = mapping
                        with (
                            patch("app.placement.policy.evaluate_v2", side_effect=error),
                            self.assertLogs("app.api", level="ERROR") as logs,
                        ):
                            response = self._post(request, verdict)
                        self.assertEqual(500, response.status_code)
                        self.assertEqual(
                            {
                                "error": "INTERNAL_ERROR",
                                "message": "The placement service could not complete the request.",
                                "decision_id": None,
                            },
                            response.json(),
                        )
                        for canary in ("POLICY_EXCEPTION_CANARY", "PROMPT_CANARY"):
                            self.assertNotIn(canary, response.text)
                            self.assertNotIn(canary, repr([record.__dict__ for record in logs.records]))

    def test_missing_policy_returns_500_without_calling_judge(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            api = create_app()
        for mapping in (None, {}, {"local": ["local"], "cloud": ["cloud"]}):
            with self.subTest(mapping=mapping):
                request = {"input": "Ping"}
                if mapping is not None:
                    request["model_mapping"] = mapping
                with (
                    patch("app.placement.judge.classify") as classifier,
                    self.assertLogs("app.api", level="ERROR"),
                    TestClient(api) as client,
                ):
                    response = client.post("/v2/placement/decide", json=request)
                self.assertEqual(500, response.status_code)
                self.assertEqual(ERROR_FIELDS, set(response.json()))
                self.assertEqual("INTERNAL_ERROR", response.json()["error"])
                self.assertIsNone(response.json()["decision_id"])
                classifier.assert_not_called()

    def test_policy_with_no_permitted_targets_is_an_explicit_block(self) -> None:
        document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        document["permitted_targets"] = []
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            api = create_app(path)
        for mapping in (None, {}, {"local": ["local"], "cloud": ["cloud"]}):
            with self.subTest(mapping=mapping):
                request = {"input": "Ping"}
                if mapping is not None:
                    request["model_mapping"] = mapping
                with (
                    patch("app.placement.judge.classify", return_value=(local_verdict(), 0)),
                    TestClient(api) as client,
                ):
                    response = client.post("/v2/placement/decide", json=request)
                self.assertEqual(200, response.status_code)
                payload = response.json()
                self.assertEqual("fallback", payload["target_class"])
                self.assertEqual("stop", payload["fallback_action"])
                self.assertEqual(["POLICY_BLOCKED"], payload["reason_codes"])
                self.assertEqual("relayiq-judge-v1", payload["policy_revision"])
                if mapping is not None:
                    self.assertEqual({"local": [], "cloud": []}, payload["suggested_models"])
                else:
                    self.assertNotIn("suggested_models", payload)

    def test_invalid_requests_return_stable_400_envelope(self) -> None:
        cases = (
            {},
            {"input": "   "},
            {"input": "Ping", "preferred_target_class": "auto"},
            {"input": "Ping", "preferred_target_class": None},
            {"input": "Ping", "model_mapping": None},
            {"input": "Ping", "model_mapping": {"local": ["same", "same"]}},
            {"input": "Ping", "model_mapping": {"fallback": ["model"]}},
            {"input": "Ping", "model_mapping": {"local": [" "]}},
            {"input": "Ping", "context": {"connectivity": "false"}},
            {"input": "Ping", "context": {"cloud_budget_kb": -1}},
            {"input": "Ping", "context": {"required_capabilities": [""]}},
            {"input": "Ping", "context": {"redaction_profiles": []}},
            {"input": "Ping", "context": {"redaction_profiles": ["demo-redaction-v1"]}},
            {"input": "Ping", "context": {"redaction_profiles": None}},
            {"input": "Ping", "classification": local_verdict()["classification"]},
            {"input": "Ping", "allowed_targets": ["local"]},
        )
        for request in cases:
            with self.subTest(request=request):
                response = self._post(request)
                self.assertEqual(400, response.status_code)
                self.assertEqual(ERROR_FIELDS, set(response.json()))
                self.assertEqual("INVALID_REQUEST", response.json()["error"])
                self.assertIsNone(response.json()["decision_id"])

    def test_malformed_json_and_nonfinite_budget_return_400(self) -> None:
        with TestClient(self.api) as client:
            malformed = client.post(
                "/v2/placement/decide",
                content='{"input":',
                headers={"content-type": "application/json"},
            )
            nonfinite = client.post(
                "/v2/placement/decide",
                content='{"input":"Ping","context":{"cloud_budget_kb":Infinity}}',
                headers={"content-type": "application/json"},
            )
        self.assertEqual(400, malformed.status_code)
        self.assertEqual(400, nonfinite.status_code)

    def test_error_envelopes_do_not_log_prompt_or_exception_canaries(self) -> None:
        canaries = ("Alex Example", "alex.example", "EMP-DEMO-0042", "SECRET-KEY")
        for error, status, code in (
            (RuntimeError(" ".join(canaries)), 500, "INTERNAL_ERROR"),
            (
                PlacementServiceUnavailableError(" ".join(canaries)),
                503,
                "SERVICE_UNAVAILABLE",
            ),
        ):
            with self.subTest(code=code):
                with (
                    patch.object(
                        self.api.state.placement_engine,
                        "decide_v2",
                        side_effect=error,
                    ),
                    self.assertLogs("app.api", level="INFO") as captured,
                    TestClient(self.api, raise_server_exceptions=False) as client,
                ):
                    response = client.post(
                        "/v2/placement/decide",
                        json={"input": " ".join(canaries)},
                    )
                logs = "\n".join(captured.output)
                self.assertEqual(status, response.status_code)
                self.assertEqual(code, response.json()["error"])
                self.assertEqual(ERROR_FIELDS, set(response.json()))
                for canary in canaries:
                    self.assertNotIn(canary, logs)

    def test_decision_endpoint_does_not_invoke_answer_models(self) -> None:
        with (
            patch(
                "app.placement.judge.classify",
                return_value=(local_verdict(), 0),
            ) as classifier_fixture,
            patch.object(
                foundry,
                "chat_local",
                side_effect=AssertionError("answer-model local call"),
            ) as local_answer,
            patch.object(
                foundry,
                "chat_cloud",
                side_effect=AssertionError("answer-model cloud call"),
            ) as cloud_answer,
            TestClient(self.api) as client,
        ):
            response = client.post(
                "/v2/placement/decide",
                json={
                    "input": "What is 12 + 15?",
                    "model_mapping": {"local": ["local"], "cloud": ["cloud"]},
                },
            )
        self.assertEqual(200, response.status_code)
        classifier_fixture.assert_called_once()
        local_answer.assert_not_called()
        cloud_answer.assert_not_called()

    def test_openapi_matches_draft4_surface_and_omits_422(self) -> None:
        schema = self.api.openapi()
        operation = schema["paths"]["/v2/placement/decide"]["post"]
        request_schema = schema["components"]["schemas"]["PlacementRequestV2"]
        response_schema = schema["components"]["schemas"]["PlacementResponseV2"]

        self.assertEqual({"input"}, set(request_schema["required"]))
        self.assertEqual(
            {"input", "preferred_target_class", "model_mapping", "context"},
            set(request_schema["properties"]),
        )
        self.assertNotIn(
            {"type": "null"},
            request_schema["properties"]["preferred_target_class"].get("anyOf", []),
        )
        self.assertNotIn(
            {"type": "null"},
            request_schema["properties"]["model_mapping"].get("anyOf", []),
        )
        self.assertEqual(REQUIRED_SUCCESS_FIELDS, set(response_schema["required"]))
        self.assertEqual(
            REQUIRED_SUCCESS_FIELDS
            | {"suggested_models", "explanation", "classification", "sensitive_data_types"},
            set(response_schema["properties"]),
        )
        self.assertEqual(5, len(response_schema["allOf"]))
        self.assertEqual("string", response_schema["properties"]["policy_revision"]["type"])
        self.assertNotIn("anyOf", response_schema["properties"]["policy_revision"])
        components = schema["components"]["schemas"]
        classification = components["JudgeClassification"]
        self.assertFalse(classification["additionalProperties"])
        self.assertEqual(
            {"routine", "fresh_knowledge", "reasoning_heavy", "critical", "sensitive_data"},
            set(classification["required"]),
        )
        self.assertEqual(set(classification["required"]), set(classification["properties"]))
        for flag in classification["properties"].values():
            self.assertEqual("boolean", flag["type"])
        self.assertEqual(
            {"consent_required"}, set(components["PlacementRequirementsResponse"]["properties"])
        )
        self.assertEqual(
            {"connectivity", "cloud_budget_kb", "required_capabilities"},
            set(components["PlacementContextRequestV2"]["properties"]),
        )
        for name in (
            "PlacementResponseV2", "PlacementRequestV2", "PlacementContextRequestV2",
            "PlacementRequirementsResponse", "ModelMappingRequest", "SuggestedModelsResponse",
        ):
            self.assertFalse(components[name]["additionalProperties"])
        suggestions = components["SuggestedModelsResponse"]
        self.assertEqual({"local", "cloud"}, set(suggestions["required"]))
        self.assertEqual({"local", "cloud"}, set(suggestions["properties"]))
        for lane in suggestions["properties"].values():
            self.assertEqual("array", lane["type"])
            self.assertTrue(lane["uniqueItems"])
            self.assertEqual("string", lane["items"]["type"])
        categories = response_schema["properties"]["sensitive_data_types"]
        self.assertIn("relayiq-judge-v1", categories["description"])
        self.assertIn(
            "detects email addresses only",
            response_schema["properties"]["classification"]["description"],
        )
        self.assertEqual("array", categories["type"])
        self.assertEqual(1, categories["minItems"])
        self.assertTrue(categories["uniqueItems"])
        self.assertEqual(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", categories["items"]["pattern"])
        self.assertNotIn("enum", categories["items"])
        self.assertNotIn("REDACTION_UNSUPPORTED", components["V2ReasonCode"]["enum"])
        self.assertNotIn("DECISION_UNAVAILABLE", components["V2ReasonCode"]["enum"])
        self.assertEqual(
            {"local", "cloud", "cloud_with_consent", "fallback"},
            set(components["TargetClass"]["enum"]),
        )
        self.assertEqual({"local", "stop"}, set(components["FallbackAction"]["enum"]))
        self.assertEqual(
            ["local", "cloud"], request_schema["properties"]["preferred_target_class"]["enum"]
        )
        for name in ("classification", "sensitive_data_types", "suggested_models", "explanation"):
            self.assertNotIn({"type": "null"}, response_schema["properties"][name].get("anyOf", []))
        self.assertIn(
            "cloud_with_consent",
            schema["components"]["schemas"]["TargetClass"]["enum"],
        )
        for retired in (
            "contract_version",
            "placement",
            "policy",
            "permitted_payload",
            "requires_consent",
            "context_policy",
        ):
            self.assertNotIn(retired, response_schema["properties"])
        self.assertNotIn("422", operation["responses"])
        self.assertIn("400", operation["responses"])
        self.assertIn("500", operation["responses"])
        self.assertIn("503", operation["responses"])


class PlacementV2ResponseContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.healthy = {
            "decision_id": "4e0aa77b-5c5b-4a04-8a47-d22d9d15ce51",
            "target_class": "local",
            "policy_revision": "relayiq-judge-v1",
            "requirements": {"consent_required": False},
            "fallback_action": "local",
            "reason_codes": ["LOCAL_CAPABILITY_SUFFICIENT"],
            "classification": local_verdict()["classification"],
        }
        self.sensitive = deepcopy(self.healthy)
        self.sensitive["classification"]["sensitive_data"] = True
        self.sensitive["sensitive_data_types"] = ["email_address", "novel_sensitive_category"]
        self.sensitive["reason_codes"].append("SENSITIVE_DATA_DETECTED")
        self.degraded = deepcopy(self.healthy)
        self.degraded.pop("classification")
        self.degraded["reason_codes"].append("CLASSIFIER_DEGRADED")

    def valid_responses(self):
        for assessment in (self.healthy, self.sensitive, self.degraded):
            for target in ("local", "cloud", "cloud_with_consent", "fallback"):
                for mapped in (False, True):
                    payload = deepcopy(assessment)
                    payload["target_class"] = target
                    payload["requirements"]["consent_required"] = target == "cloud_with_consent"
                    if mapped:
                        payload["suggested_models"] = (
                            {"local": [], "cloud": []} if target == "fallback"
                            else {"local": ["local-B", "shared"], "cloud": ["shared", "cloud-A"]}
                        )
                    yield f"{target}/{mapped}/{assessment['reason_codes']}", payload
                    if mapped and target != "fallback":
                        empty_alternative = deepcopy(payload)
                        other_lane = "cloud" if target == "local" else "local"
                        empty_alternative["suggested_models"][other_lane] = []
                        yield f"{target}/empty-{other_lane}", empty_alternative
        for assessment in (self.healthy, self.sensitive, self.degraded):
            payload = deepcopy(assessment)
            payload.update(target_class="fallback", fallback_action="stop")
            payload["reason_codes"].append("POLICY_BLOCKED")
            yield "POLICY_BLOCKED", payload

    def invalid_responses(self):
        for name in ("classification", "sensitive_data_types", "suggested_models", "explanation"):
            yield f"explicit null {name}", {**deepcopy(self.healthy), name: None}
        for flag in self.healthy["classification"]:
            payload = deepcopy(self.healthy)
            del payload["classification"][flag]
            yield f"missing flag {flag}", payload
            for value in (0, 1, "true", "false", None, [], {}):
                payload = deepcopy(self.healthy)
                payload["classification"][flag] = value
                yield f"nonboolean {flag}={value!r}", payload
        for categories in (
            [], ["email_address", "email_address"], ["Email_Address"], ["email address"],
            ["email-address"], ["email__address"], ["_email"], ["email_"], ["9email"],
            ["alex.example@example.com"], ["EMP-20431"], [""], [None], [42], "email_address",
        ):
            yield f"bad categories {categories!r}", {**deepcopy(self.sensitive), "sensitive_data_types": categories}
        missing = deepcopy(self.healthy)
        missing.pop("classification")
        yield "healthy missing classification", missing
        yield "empty classification", {**deepcopy(self.healthy), "classification": {}}
        payload = deepcopy(self.healthy)
        payload["classification"]["extra"] = False
        yield "unknown flag", payload
        yield "unknown response property", {**deepcopy(self.healthy), "extra": False}
        yield "retired redaction requirement", {
            **deepcopy(self.healthy), "requirements": {"consent_required": False, "redaction_profile": "old"},
        }
        yield "coerced consent", {**deepcopy(self.healthy), "requirements": {"consent_required": 0}}
        yield "retired reason", {**deepcopy(self.healthy), "reason_codes": ["REDACTION_UNSUPPORTED"]}
        yield "unknown target", {**deepcopy(self.healthy), "target_class": "deferred"}
        yield "unknown fallback", {**deepcopy(self.healthy), "fallback_action": "cloud"}
        yield "missing categories", {key: value for key, value in deepcopy(self.sensitive).items() if key != "sensitive_data_types"}
        yield "missing sensitive reason", {**deepcopy(self.sensitive), "reason_codes": ["LOCAL_CAPABILITY_SUFFICIENT"]}
        yield "spurious categories", {**deepcopy(self.healthy), "sensitive_data_types": ["email_address"]}
        yield "spurious sensitive reason", {**deepcopy(self.healthy), "reason_codes": ["SENSITIVE_DATA_DETECTED"]}
        payload = deepcopy(self.sensitive)
        payload["classification"]["sensitive_data"] = False
        yield "false sensitivity with categories and reason", payload
        payload = deepcopy(self.healthy)
        payload["classification"]["sensitive_data"] = True
        yield "true sensitivity without categories or reason", payload
        for assessment in (self.healthy, self.sensitive):
            payload = deepcopy(assessment)
            payload["reason_codes"].append("CLASSIFIER_DEGRADED")
            yield "degraded with assessment", payload
        yield "degraded with types", {**deepcopy(self.degraded), "sensitive_data_types": ["employee_id"]}
        yield "degraded with sensitive reason", {**deepcopy(self.degraded), "reason_codes": ["CLASSIFIER_DEGRADED", "SENSITIVE_DATA_DETECTED"]}
        yield "duplicate reasons", {**deepcopy(self.healthy), "reason_codes": ["CLASSIFIER_DEGRADED"] * 2}
        yield "empty reasons", {**deepcopy(self.healthy), "reason_codes": []}
        for suggestions in (
            [], ["legacy-flat-model"], {},
            {"local": ["local"]}, {"cloud": ["cloud"]},
            {"local": ["local"], "cloud": [], "extra": []},
            {"local": ["same", "same"], "cloud": []},
            {"local": ["local"], "cloud": ["same", "same"]},
            {"local": [" "], "cloud": []}, {"local": ["local"], "cloud": [" "]},
            {"local": "local", "cloud": []}, {"local": ["local"], "cloud": None},
            {"local": [], "cloud": ["cloud"]},
            {"local": [], "cloud": []},
        ):
            yield f"invalid model inventory {suggestions!r}", {
                **deepcopy(self.healthy), "suggested_models": suggestions,
            }
        for lane in ("local", "cloud"):
            inventory = {"local": [], "cloud": []}
            inventory[lane] = ["model"]
            yield f"fallback with {lane} models", {
                **deepcopy(self.healthy), "target_class": "fallback", "suggested_models": inventory,
            }
        for target in ("cloud", "cloud_with_consent"):
            yield f"{target} missing selected lane", {
                **deepcopy(self.healthy), "target_class": target,
                "requirements": {"consent_required": target == "cloud_with_consent"},
                "suggested_models": {"local": ["local"], "cloud": []},
            }
        yield "cloud consent missing", {**deepcopy(self.healthy), "target_class": "cloud_with_consent"}
        yield "local with consent", {**deepcopy(self.healthy), "requirements": {"consent_required": True}}
        yield "unknown policy without fallback", {**deepcopy(self.healthy), "policy_revision": None}
        yield "unknown policy with local fallback", {**deepcopy(self.healthy), "policy_revision": None, "target_class": "fallback"}
        yield "unknown policy with stop", {
            **deepcopy(self.healthy), "policy_revision": None,
            "target_class": "fallback", "fallback_action": "stop",
        }
        yield "blocked without fallback", {**deepcopy(self.healthy), "reason_codes": ["POLICY_BLOCKED"]}
        yield "blocked with local fallback", {**deepcopy(self.healthy), "target_class": "fallback", "reason_codes": ["POLICY_BLOCKED"]}
        yield "unavailable without fallback", {**deepcopy(self.healthy), "reason_codes": ["DECISION_UNAVAILABLE"]}
        yield "retired unavailable fallback", {
            **deepcopy(self.healthy), "target_class": "fallback", "fallback_action": "stop",
            "reason_codes": ["DECISION_UNAVAILABLE"],
        }

    def test_runtime_response_validation_accepts_valid_contract(self) -> None:
        for name, payload in self.valid_responses():
            with self.subTest(name=name):
                self.assertEqual(
                    payload,
                    PlacementResponseV2.model_validate_json(json.dumps(payload)).model_dump(
                        mode="json", exclude_unset=True
                    ),
                )

    def test_runtime_response_validation_rejects_invalid_contract(self) -> None:
        for name, payload in self.invalid_responses():
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    PlacementResponseV2.model_validate_json(json.dumps(payload))
                with self.assertRaises(ValidationError):
                    PlacementResponseV2.model_validate(payload)

    @unittest.skipIf(Draft202012Validator is None, "optional jsonschema package is not installed")
    def test_advertised_schema_agrees_with_response_validation(self) -> None:
        schema = create_app(POLICY_PATH).openapi()
        document = {
            "$ref": "#/components/schemas/PlacementResponseV2",
            "components": schema["components"],
        }
        Draft202012Validator.check_schema(document)
        validator = Draft202012Validator(document)
        for name, payload in self.valid_responses():
            with self.subTest(valid=name):
                self.assertEqual([], list(validator.iter_errors(payload)))
        for name, payload in self.invalid_responses():
            with self.subTest(invalid=name):
                self.assertTrue(list(validator.iter_errors(payload)))

    def test_shared_assessment_is_strict_and_frozen(self) -> None:
        classification = JudgeClassification.model_validate(self.healthy["classification"])
        with self.assertRaises(ValidationError):
            classification.routine = False
        assessment = JudgeAssessment(classification=classification)
        with self.assertRaises(ValidationError):
            assessment.classification = classification
        with self.assertRaises(ValidationError):
            JudgeAssessment.model_validate({"classification": classification, "unknown": True})
        for value in (None, [], ["email_address"]):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                JudgeAssessment(classification=classification, sensitive_data_types=value)
        with self.assertRaises(ValidationError):
            JudgeAssessment.model_validate({})


class PlacementV2StartupAndPolicyTests(unittest.TestCase):
    def test_policy_snapshot_matches_bounded_poc_template(self) -> None:
        snapshot = policy.load_placement_policy(POLICY_PATH)
        self.assertEqual("relayiq-judge-v1", snapshot.policy_revision)
        self.assertEqual(frozenset({"local", "cloud"}), snapshot.permitted_targets)
        self.assertTrue(snapshot.local_failure_permitted)
        self.assertFalse(hasattr(snapshot, "supported_redaction_profiles"))
        self.assertEqual(
            frozenset({"Demonstrate the configured demo block."}),
            snapshot.deny_inputs,
        )

    def test_invalid_policy_fails_visibly(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            invalid = Path(directory) / "invalid-policy.json"
            invalid.write_text(
                '{"policy_revision":"bad","permitted_targets":["cloud"]}',
                encoding="utf-8",
            )
            with self.assertRaises(policy.PolicyConfigurationError):
                create_app(invalid)

    def test_retired_redaction_profiles_rejected_in_otherwise_valid_r2_policy(self) -> None:
        document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        self.assertEqual("relayiq-judge-v1", document["policy_revision"])
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(
                "relayiq-judge-v1", policy.load_placement_policy(policy_path).policy_revision
            )
            for profiles in ([], ["demo-redaction-v1"], None):
                with self.subTest(profiles=profiles):
                    policy_path.write_text(
                        json.dumps({**document, "supported_redaction_profiles": profiles}),
                        encoding="utf-8",
                    )
                    with self.assertRaises(policy.PolicyConfigurationError):
                        policy.load_placement_policy(policy_path)
                    with self.assertRaises(policy.PolicyConfigurationError):
                        create_app(policy_path)

    def test_standalone_import_does_not_import_scheduler(self) -> None:
        environment = os.environ.copy()
        environment[POLICY_CONFIG_ENV] = str(POLICY_PATH)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import app.api; "
                    "assert 'app.scheduler' not in sys.modules"
                ),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_v2_fixed_defaults_do_not_read_demo_scheduler_state(self) -> None:
        api = create_app(POLICY_PATH)
        captured = []

        original = api.state.placement_engine.decide_v2

        def capture_without_recursion(request, context, **kwargs):
            captured.append(context)
            return original(request, context, **kwargs)

        with (
            patch("app.placement.judge.classify", return_value=(local_verdict(), 0)),
            patch.object(
                api.state.placement_engine,
                "decide_v2",
                side_effect=capture_without_recursion,
            ),
            TestClient(api) as client,
        ):
            response = client.post("/v2/placement/decide", json={"input": "Ping"})

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(captured))
        self.assertTrue(captured[0].connectivity)
        self.assertIsNone(captured[0].cloud_budget_kb)
        self.assertEqual((), captured[0].required_capabilities)
        self.assertFalse(hasattr(captured[0], "redaction_profiles"))


if __name__ == "__main__":
    unittest.main()
