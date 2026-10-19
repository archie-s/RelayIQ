from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from app import judge
from app.main import app, scheduler
from app.placement import PlacementServiceUnavailableError


SUCCESS_FIELDS = {
    "decision_id",
    "placement",
    "policy",
    "permitted_payload",
    "requires_consent",
    "fallback",
    "suggested_models",
    "reason_codes",
    "explanation",
}
ERROR_FIELDS = {"error", "message", "decision_id"}


class PlacementApiTests(unittest.TestCase):
    def _post(self, payload: dict):
        with TestClient(app) as client:
            return client.post("/v1/placement/decide", json=payload)

    def test_exact_success_shape_uuid_and_no_answer_model_execution(self) -> None:
        verdict = judge.enrich("general", "high", False, "analysis")
        with (
            patch("app.placement.judge.classify", return_value=(verdict, 0)),
            patch("app.foundry.chat_local", side_effect=AssertionError("local answer called")),
            patch("app.foundry.chat_cloud", side_effect=AssertionError("cloud answer called")),
            TestClient(app) as client,
        ):
            response = client.post(
                "/v1/placement/decide",
                json={
                    "input": "Compare two architecture options.",
                    "context": {
                        "connectivity": True,
                        "cloud_budget_kb": None,
                        "required_capabilities": ["extended_reasoning"],
                        "available_local": ["local-A"],
                        "available_cloud": ["cloud-B", "cloud-A", "cloud-B"],
                    },
                },
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual(SUCCESS_FIELDS, set(payload))
        self.assertEqual(str(UUID(payload["decision_id"])), payload["decision_id"])
        self.assertEqual("cloud", payload["placement"])
        self.assertEqual(["cloud-B", "cloud-A"], payload["suggested_models"])
        self.assertFalse(payload["requires_consent"])
        self.assertEqual("local", payload["fallback"])

    def test_explicit_false_connectivity_is_not_overridden(self) -> None:
        verdict = judge.enrich("general", "high", False, "analysis")
        with (
            patch("app.placement.judge.classify", return_value=(verdict, 0)),
            patch.object(scheduler, "connectivity", True),
        ):
            response = self._post({
                "input": "Analyze this.",
                "context": {
                    "connectivity": False,
                    "cloud_budget_kb": None,
                    "available_local": ["local-A"],
                    "available_cloud": ["cloud-A"],
                },
            })

        payload = response.json()
        self.assertEqual("local", payload["placement"])
        self.assertEqual(["local-A"], payload["suggested_models"])
        self.assertIn("CONNECTIVITY_UNAVAILABLE", payload["reason_codes"])

    def test_omitted_connectivity_uses_service_observed_value(self) -> None:
        verdict = judge.enrich("general", "high", False, "analysis")
        with (
            patch("app.placement.judge.classify", return_value=(verdict, 0)),
            patch.object(scheduler, "connectivity", False),
        ):
            response = self._post({
                "input": "Analyze this.",
                "context": {
                    "cloud_budget_kb": None,
                    "available_local": ["local-A"],
                    "available_cloud": ["cloud-A"],
                },
            })

        self.assertEqual("local", response.json()["placement"])
        self.assertIn("CONNECTIVITY_UNAVAILABLE", response.json()["reason_codes"])

    def test_omitted_context_defaults_to_service_observations(self) -> None:
        verdict = judge.enrich("general", "high", False, "analysis")
        with (
            patch("app.placement.judge.classify", return_value=(verdict, 0)),
            patch.object(scheduler, "connectivity", False),
        ):
            response = self._post({"input": "Analyze this."})

        self.assertEqual(200, response.status_code)
        self.assertEqual("local", response.json()["placement"])
        self.assertEqual([], response.json()["suggested_models"])

    def test_omitted_budget_uses_service_remaining_budget(self) -> None:
        verdict = judge.enrich("general", "high", False, "analysis")
        with (
            patch("app.placement.judge.classify", return_value=(verdict, 0)),
            patch.object(scheduler, "budget_kb", 5.0),
            patch.object(scheduler, "cloud_kb_used", 5.0),
        ):
            response = self._post({
                "input": "Analyze this.",
                "context": {
                    "connectivity": True,
                    "available_cloud": ["cloud-A"],
                },
            })

        self.assertEqual("deferred", response.json()["placement"])
        self.assertIn("CLOUD_BUDGET_EXCEEDED", response.json()["reason_codes"])

    def test_explicit_null_budget_is_unbounded_for_request(self) -> None:
        verdict = judge.enrich("general", "high", False, "analysis")
        with (
            patch("app.placement.judge.classify", return_value=(verdict, 0)),
            patch.object(scheduler, "budget_kb", 0.0),
            patch.object(scheduler, "cloud_kb_used", 0.0),
        ):
            response = self._post({
                "input": "Analyze this.",
                "context": {
                    "connectivity": True,
                    "cloud_budget_kb": None,
                    "available_cloud": ["cloud-A"],
                },
            })

        self.assertEqual("cloud", response.json()["placement"])
        self.assertNotIn("CLOUD_BUDGET_EXCEEDED", response.json()["reason_codes"])

    def test_sensitive_cloud_response_has_consent_invariant(self) -> None:
        verdict = judge.enrich("general", "high", False, "employee review")
        with patch("app.placement.judge.classify", return_value=(verdict, 0)):
            response = self._post({
                "input": "Review employee id EMP-20431 and salary.",
                "context": {
                    "connectivity": True,
                    "cloud_budget_kb": None,
                    "available_cloud": ["cloud-A"],
                },
            })

        payload = response.json()
        self.assertEqual("cloud_with_consent", payload["placement"])
        self.assertTrue(payload["requires_consent"])
        self.assertNotIn("EMP-20431", payload["permitted_payload"])
        self.assertNotIn("EMP-20431", payload["explanation"])

    def test_input_at_maximum_length_is_accepted(self) -> None:
        verdict = judge.enrich("general", "low", False, "analysis")
        with patch("app.placement.judge.classify", return_value=(verdict, 0)):
            response = self._post({"input": "x" * 100_000})

        self.assertEqual(200, response.status_code)

    def test_invalid_requests_return_stable_400_envelope(self) -> None:
        cases = [
            ({}, "missing input"),
            ({"input": ""}, "empty input"),
            ({"input": 42}, "input type"),
            ({"input": "ok", "unknown": True}, "top-level unknown"),
            ({"input": "ok", "context": None}, "null context"),
            ({"input": "ok", "context": {"connectivity": None}}, "null connectivity"),
            ({"input": "ok", "context": {"connectivity": "false"}}, "connectivity type"),
            ({"input": "ok", "context": {"cloud_budget_kb": -1}}, "negative budget"),
            ({"input": "ok", "context": {"available_local": "model-A"}}, "model array"),
            ({"input": "ok", "context": {"available_cloud": [1]}}, "model identifier"),
            ({"input": "x" * 100_001}, "oversized input"),
            ({"input": "ok", "context": {"latency_target_ms": 10}}, "retired latency"),
            ({"input": "ok", "context": {"data_classification": "private"}}, "retired classification"),
            ({"input": "ok", "context": {"user_policy": {}}}, "retired policy"),
        ]

        for payload, label in cases:
            with self.subTest(label=label):
                response = self._post(payload)
                self.assertEqual(400, response.status_code)
                self.assertEqual(ERROR_FIELDS, set(response.json()))
                self.assertEqual("INVALID_REQUEST", response.json()["error"])
                self.assertIsNone(response.json()["decision_id"])
                self.assertTrue(response.json()["message"])

    def test_malformed_json_returns_stable_400_envelope(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/v1/placement/decide",
                content='{"input":',
                headers={"content-type": "application/json"},
            )

        self.assertEqual(400, response.status_code)
        self.assertEqual({
            "error": "INVALID_REQUEST",
            "message": "Malformed JSON request body.",
            "decision_id": None,
        }, response.json())

    def test_non_finite_budget_returns_stable_400_envelope(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/v1/placement/decide",
                content='{"input":"ok","context":{"cloud_budget_kb":Infinity}}',
                headers={"content-type": "application/json"},
            )

        self.assertEqual(400, response.status_code)
        self.assertEqual("INVALID_REQUEST", response.json()["error"])
        self.assertIsNone(response.json()["decision_id"])

    def test_stable_internal_and_unavailable_error_envelopes(self) -> None:
        for error, status, code in (
            (RuntimeError("boom"), 500, "INTERNAL_ERROR"),
            (PlacementServiceUnavailableError("offline"), 503, "SERVICE_UNAVAILABLE"),
        ):
            with self.subTest(status=status):
                with (
                    patch("app.main.placement_engine.decide", side_effect=error),
                    patch("app.main._logger"),
                    TestClient(app, raise_server_exceptions=False) as client,
                ):
                    response = client.post("/v1/placement/decide", json={"input": "ok"})

                self.assertEqual(status, response.status_code)
                self.assertEqual(ERROR_FIELDS, set(response.json()))
                self.assertEqual(code, response.json()["error"])
                self.assertIsNone(response.json()["decision_id"])

    def test_openapi_exposes_only_v1_contract_and_no_422(self) -> None:
        schema = app.openapi()
        operation = schema["paths"]["/v1/placement/decide"]["post"]
        success_schema = schema["components"]["schemas"]["PlacementResponse"]
        context_schema = schema["components"]["schemas"]["PlacementContextRequest"]
        request_schema = schema["components"]["schemas"]["PlacementRequest"]

        self.assertEqual(SUCCESS_FIELDS, set(success_schema["required"]))
        self.assertEqual(SUCCESS_FIELDS, set(success_schema["properties"]))
        self.assertNotIn("422", operation["responses"])
        self.assertIn("400", operation["responses"])
        self.assertEqual(
            "#/components/schemas/InvalidRequestResponse",
            operation["responses"]["400"]["content"]["application/json"]["schema"]["$ref"],
        )
        self.assertEqual(
            "#/components/schemas/InternalErrorResponse",
            operation["responses"]["500"]["content"]["application/json"]["schema"]["$ref"],
        )
        self.assertEqual(
            "#/components/schemas/ServiceUnavailableResponse",
            operation["responses"]["503"]["content"]["application/json"]["schema"]["$ref"],
        )
        self.assertFalse(context_schema["additionalProperties"])
        self.assertFalse(request_schema["additionalProperties"])
        self.assertEqual({"type": "boolean", "title": "Connectivity"}, context_schema[
            "properties"
        ]["connectivity"])
        self.assertEqual(
            {
                "connectivity",
                "cloud_budget_kb",
                "required_capabilities",
                "available_local",
                "available_cloud",
            },
            set(context_schema["properties"]),
        )


if __name__ == "__main__":
    unittest.main()
