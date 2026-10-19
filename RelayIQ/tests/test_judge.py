from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app import judge


class JudgeFallbackTests(unittest.TestCase):
    def test_model_decides_consent_from_prompt_and_context(self) -> None:
        response = (
            '{"urgency":"general","complexity":"high","needs_fresh":false,'
            '"requires_consent":true,"category":"employee review"}'
        )
        context = {
            "connectivity": True,
            "redaction_profiles": ["demo-redaction-v1"],
        }

        with patch("app.judge.foundry.chat_local", return_value=(response, 42)) as chat:
            verdict, judge_bytes = judge.classify(
                "Review employee id EMP-20431 and salary.",
                context=context,
            )

        self.assertTrue(verdict["requires_consent"])
        self.assertEqual("cloud", verdict["route"])
        self.assertEqual(42, judge_bytes)
        sent = chat.call_args.args[1][1]["content"]
        self.assertIn('"input":"Review employee id EMP-20431 and salary."', sent)
        self.assertIn('"redaction_profiles":["demo-redaction-v1"]', sent)

    def test_fallback_judge_requires_consent_for_sensitive_prompt(self) -> None:
        verdict, _ = judge.classify(
            "Review employee id EMP-20431 and salary.",
            context={"redaction_profiles": ["demo-redaction-v1"]},
            force_fallback=True,
        )

        self.assertTrue(verdict["requires_consent"])

    def test_observed_emergency_misses_are_classified_as_critical(self) -> None:
        prompts = (
            "A toddler swallowed drain cleaner and is becoming drowsy.",
            "One side of my face is drooping and my words suddenly sound strange.",
            "A wildfire is approaching our road. Which evacuation route is open?",
            "There is smoke in the cabin and we may need to divert.",
            "I cut my finger while cooking. How do I stop the bleeding?",
            "Which emergency department is open and accepting patients right now?",
            "My child has a fever after starting a new medicine. What are the warning signs?",
            "A child has a high fever and seizures.",
            "I need help with a burn injury with blistering.",
            "I need medicine dosage safety in an emergency.",
            "There may be an allergic reaction with swelling.",
        )

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                verdict, _ = judge.classify(prompt, force_fallback=True)
                self.assertEqual("critical", verdict["urgency"])

    def test_figurative_and_fictional_emergency_words_are_not_critical(self) -> None:
        prompts = (
            "My database is bleeding connections after each deploy.",
            "Write a fictional scene where a detective has chest pain.",
            "Could this poison defeat a dragon in my role-playing game?",
        )

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                verdict, _ = judge.classify(prompt, force_fallback=True)
                self.assertNotEqual("critical", verdict["urgency"])

    def test_medication_interaction_is_complex_without_being_critical(self) -> None:
        verdict, _ = judge.classify(
            "Can I take ibuprofen together with my blood pressure medication?",
            force_fallback=True,
        )

        self.assertEqual("general", verdict["urgency"])
        self.assertEqual("high", verdict["complexity"])
        self.assertEqual("cloud", verdict["route"])


class JudgePlacementClassificationTests(unittest.TestCase):
    def model_response(self, **updates) -> dict:
        return {
            "urgency": "general",
            "complexity": "high",
            "needs_fresh": False,
            "requires_consent": False,
            "category": "technical analysis",
            "classification": {
                "routine": False,
                "fresh_knowledge": False,
                "reasoning_heavy": True,
                "critical": False,
                "sensitive_data": False,
            },
            **updates,
        }

    def test_v2_prompt_token_budget_and_classification_passthrough(self) -> None:
        for routine in (False, True):
            response = self.model_response()
            response["classification"]["routine"] = routine
            with self.subTest(routine=routine):
                with patch(
                    "app.judge.foundry.chat_local", return_value=(json.dumps(response), 42)
                ) as chat:
                    verdict, byte_count = judge.classify(
                        "Analyze this.",
                        context={"connectivity": True, "required_capabilities": ["extended_reasoning"]},
                        include_classification=True,
                    )
                self.assertEqual("model", verdict["judge_mode"])
                self.assertEqual(response["classification"], verdict["classification"])
                self.assertEqual("cloud", verdict["route"])
                self.assertEqual(42, byte_count)
                self.assertNotIn("sensitive_data_types", verdict)
                self.assertEqual(
                    {
                        "max_tokens": 160,
                        "temperature": 0.0,
                        "timeout_seconds": judge.config.JUDGE_TIMEOUT_SECONDS,
                    },
                    chat.call_args.kwargs,
                )
                messages = chat.call_args.args[1]
                for flag in set(response["classification"]) - {"sensitive_data"}:
                    self.assertIn(flag, messages[0]["content"])
                self.assertIn("email_detected", messages[0]["content"])
                self.assertIn("at least one actual email address", messages[0]["content"])
                self.assertIn("merely mentioning email without an address", messages[0]["content"])
                self.assertIn("Never include detected values", messages[0]["content"])
                self.assertEqual(
                    {
                        "input": "Analyze this.",
                        "context": {"connectivity": True, "required_capabilities": ["extended_reasoning"]},
                    },
                    json.loads(messages[1]["content"]),
                )

    def test_sensitive_category_assessment_is_independent_of_consent(self) -> None:
        for consent in (False, True):
            with self.subTest(consent=consent):
                response = self.model_response(requires_consent=consent)
                response["classification"]["sensitive_data"] = True
                response["sensitive_data_types"] = ["email_address"]
                with patch(
                    "app.judge.foundry.chat_local", return_value=(json.dumps(response), 11)
                ):
                    verdict, _ = judge.classify(
                        "Review alex.example@example.com and EMP-20431.",
                        include_classification=True,
                    )
                self.assertEqual("model", verdict["judge_mode"])
                self.assertEqual(consent, verdict["requires_consent"])
                self.assertEqual(response["classification"], verdict["classification"])
                self.assertEqual(response["sensitive_data_types"], verdict["sensitive_data_types"])
                self.assertNotIn("alex.example@example.com", json.dumps(verdict))
                self.assertNotIn("EMP-20431", json.dumps(verdict))

    def test_email_only_assessments_preserve_independent_consent(self) -> None:
        cases = (
            ("Review employee ID EMP-20431.", None, False),
            ("Review social security number 000-12-3456.", None, False),
            ("Review confidential payroll and phone number 202-555-0100.", None, False),
            ("Explain how email addresses work.", None, False),
            ("Contact alex.example@example.com.", None, True),
            ("Contact alex@example.com and sam@example.org; employee ID EMP-20431.", None, True),
            ("Review this record.", {"contact": "alex.example@example.com"}, True),
        )
        for request, context, sensitive in cases:
            with self.subTest(request=request, context=context):
                response = self.model_response(requires_consent=True)
                response["classification"]["sensitive_data"] = sensitive
                if sensitive:
                    response["sensitive_data_types"] = ["email_address"]
                with patch(
                    "app.judge.foundry.chat_local", return_value=(json.dumps(response), 7)
                ):
                    verdict, _ = judge.classify(
                        request, context=context, include_classification=True,
                    )
                self.assertEqual("model", verdict["judge_mode"])
                self.assertEqual(sensitive, verdict["classification"]["sensitive_data"])
                self.assertTrue(verdict["requires_consent"])
                if sensitive:
                    self.assertEqual(["email_address"], verdict["sensitive_data_types"])
                else:
                    self.assertNotIn("sensitive_data_types", verdict)

    def test_compact_prompt_has_six_required_boolean_signals(self) -> None:
        example = json.loads(next(
            line for line in judge._PLACEMENT_SYSTEM.splitlines() if line.startswith("{")
        ))
        self.assertEqual(
            {"routine", "fresh_knowledge", "reasoning_heavy", "critical", "email_detected", "requires_consent"},
            set(example),
        )
        self.assertTrue(all(type(value) is bool for value in example.values()))
        self.assertLess(len(judge._PLACEMENT_SYSTEM), 1500)

    def test_compact_signals_expand_without_inventing_classification_or_consent(self) -> None:
        from itertools import product

        fields = ("routine", "fresh_knowledge", "reasoning_heavy", "critical", "email_detected", "requires_consent")
        for values in product((False, True), repeat=len(fields)):
            signals = dict(zip(fields, values))
            with self.subTest(signals=signals), patch(
                "app.judge.foundry.chat_local", return_value=(json.dumps(signals), 12),
            ):
                verdict, byte_count = judge.classify("Evaluate this.", include_classification=True)
            self.assertEqual("model", verdict["judge_mode"])
            self.assertEqual(12, byte_count)
            self.assertEqual(signals["requires_consent"], verdict["requires_consent"])
            expected = {key: signals[key] for key in fields[:4]}
            expected["sensitive_data"] = signals["email_detected"]
            self.assertEqual(expected, verdict["classification"])
            self.assertEqual("critical" if signals["critical"] else "general", verdict["urgency"])
            self.assertEqual("high" if signals["reasoning_heavy"] else "low", verdict["complexity"])
            self.assertEqual(signals["fresh_knowledge"], verdict["needs_fresh"])
            self.assertNotIn("email_detected", verdict)
            if signals["email_detected"]:
                self.assertEqual(["email_address"], verdict["sensitive_data_types"])
            else:
                self.assertNotIn("sensitive_data_types", verdict)

    def test_incomplete_or_nonboolean_compact_signals_degrade(self) -> None:
        valid = {
            "routine": True, "fresh_knowledge": False, "reasoning_heavy": False,
            "critical": False, "email_detected": False, "requires_consent": True,
        }
        invalid = [{key: value for key, value in valid.items() if key != field} for field in valid]
        for field in valid:
            invalid.extend({**valid, field: value} for value in (0, 1, "true", None))
        invalid.append({**valid, "sensitive_data_types": ["social_security_number"]})
        for signals in invalid:
            with (
                self.subTest(signals=signals),
                patch("app.judge.foundry.chat_local", return_value=(json.dumps(signals), 5)),
                self.assertLogs("app.judge", level="WARNING"),
            ):
                verdict, _ = judge.classify("Evaluate this.", include_classification=True)
            self.assertEqual("fallback", verdict["judge_mode"])
            self.assertNotIn("classification", verdict)
            self.assertNotIn("sensitive_data_types", verdict)

    def test_consistent_legacy_flags_are_preserved_for_all_combinations(self) -> None:
        for urgency in ("critical", "general", "casual"):
            for complexity in ("high", "low"):
                for fresh in (False, True):
                    with self.subTest(urgency=urgency, complexity=complexity, fresh=fresh):
                        response = self.model_response(
                            urgency=urgency, complexity=complexity, needs_fresh=fresh
                        )
                        response["classification"].update(
                            critical=urgency == "critical",
                            reasoning_heavy=complexity == "high",
                            fresh_knowledge=fresh,
                        )
                        with patch(
                            "app.judge.foundry.chat_local", return_value=(json.dumps(response), 5)
                        ):
                            verdict, _ = judge.classify("Evaluate this.", include_classification=True)
                        self.assertEqual("model", verdict["judge_mode"])
                        self.assertEqual(response["classification"], verdict["classification"])
                        self.assertEqual(urgency, verdict["urgency"])
                        self.assertEqual(complexity, verdict["complexity"])
                        self.assertEqual(fresh, verdict["needs_fresh"])

    def invalid_model_responses(self):
        valid = self.model_response()
        yield "legacy only", {key: value for key, value in valid.items() if key != "classification"}
        for classification in (None, {}, [], "classification"):
            yield "invalid classification", {**deepcopy(valid), "classification": classification}
        for flag in valid["classification"]:
            response = deepcopy(valid)
            del response["classification"][flag]
            yield f"missing {flag}", response
            for value in (0, 1, "true", "false", None, {}, []):
                response = deepcopy(valid)
                response["classification"][flag] = value
                yield f"nonboolean {flag}={value!r}", response
        response = deepcopy(valid)
        response["classification"]["extra"] = True
        yield "unknown classification flag", response
        for field in ("urgency", "complexity", "needs_fresh", "requires_consent"):
            response = deepcopy(valid)
            del response[field]
            yield f"missing legacy {field}", response
        for field, value in (
            ("urgency", "urgent"), ("complexity", "medium"),
            ("needs_fresh", "false"), ("needs_fresh", 0),
            ("requires_consent", "true"), ("requires_consent", 1),
            ("urgency", None), ("complexity", None),
            ("needs_fresh", None), ("requires_consent", None),
            ("urgency", []), ("complexity", {}),
        ):
            yield f"invalid legacy {field}={value!r}", {**deepcopy(valid), field: value}
        for flag in ("critical", "reasoning_heavy", "fresh_knowledge"):
            response = deepcopy(valid)
            response["classification"][flag] = not response["classification"][flag]
            yield f"inconsistent {flag}", response
        for categories in (None, [], ["email_address"]):
            yield "false sensitivity with types", {**deepcopy(valid), "sensitive_data_types": categories}
        sensitive = deepcopy(valid)
        sensitive["classification"]["sensitive_data"] = True
        yield "true sensitivity missing types", sensitive
        for categories in (
            None, [], ["email_address", "email_address"], ["Email_Address"], ["email address"],
            ["email-address"], ["email__address"], ["_email"], ["email_"], ["9email"],
            ["employee_id"], ["social_security_number"], ["novel_sensitive_category"],
            ["email_address", "employee_id"],
            ["alex.example@example.com"], ["EMP-20431"], [""], [False], [None], "employee_id",
        ):
            yield f"invalid types {categories!r}", {**deepcopy(sensitive), "sensitive_data_types": categories}

    def test_invalid_model_assessments_degrade_without_publishing_findings(self) -> None:
        for name, response in self.invalid_model_responses():
            with self.subTest(name=name):
                response["category"] = "RAW_OUTPUT_CANARY"
                with (
                    patch("app.judge.foundry.chat_local", return_value=(json.dumps(response), 19)),
                    self.assertLogs("app.judge", level="WARNING") as captured,
                ):
                    verdict, byte_count = judge.classify(
                        "Review employee id EMP-20431 and salary.",
                        include_classification=True,
                    )
                self.assertEqual("fallback", verdict["judge_mode"])
                self.assertEqual("local", verdict["route"])
                self.assertEqual("general", verdict["urgency"])
                self.assertEqual("low", verdict["complexity"])
                self.assertFalse(verdict["needs_fresh"])
                self.assertTrue(verdict["requires_consent"])
                self.assertEqual("auto", verdict["category"])
                self.assertEqual(19, byte_count)
                self.assertNotIn("classification", verdict)
                self.assertNotIn("sensitive_data_types", verdict)
                self.assertEqual(1, len(captured.records))
                self.assertIn(captured.records[0].failure_type, {"ValueError", "ValidationError", "TypeError"})
                self.assertIsNone(captured.records[0].exc_info)
                serialized_logs = repr([record.__dict__ for record in captured.records])
                self.assertNotIn("RAW_OUTPUT_CANARY", serialized_logs)
                self.assertNotIn("EMP-20431", serialized_logs)
                self.assertNotIn("alex.example@example.com", serialized_logs)

    def test_malformed_output_and_transport_failure_log_only_failure_type(self) -> None:
        for result, error, failure_type in (
            ("RAW_OUTPUT_CANARY no json", None, "ValueError"),
            ('{"secret":"RAW_OUTPUT_CANARY", broken}', None, "JSONDecodeError"),
            (None, RuntimeError("RAW_OUTPUT_CANARY"), "RuntimeError"),
            (None, TimeoutError("RAW_OUTPUT_CANARY"), "TimeoutError"),
        ):
            with self.subTest(failure_type=failure_type):
                with (
                    patch("app.judge.foundry.chat_local", return_value=(result, 7), side_effect=error),
                    self.assertLogs("app.judge", level="WARNING") as captured,
                ):
                    verdict, _ = judge.classify("Ping", include_classification=True)
                self.assertEqual("fallback", verdict["judge_mode"])
                self.assertEqual(failure_type, captured.records[0].failure_type)
                self.assertNotIn("RAW_OUTPUT_CANARY", repr(captured.records[0].__dict__))
                self.assertEqual("Judge used degraded classification", captured.records[0].getMessage())
                self.assertIsNone(captured.records[0].exc_info)

    def test_forced_fallback_does_not_synthesize_assessment_or_call_model(self) -> None:
        with (
            patch("app.judge.foundry.chat_local", side_effect=AssertionError("model call")) as chat,
            self.assertLogs("app.judge", level="WARNING"),
        ):
            verdict, byte_count = judge.classify(
                "A toddler swallowed drain cleaner and is becoming drowsy.",
                force_fallback=True,
                include_classification=True,
            )
        self.assertEqual("fallback", verdict["judge_mode"])
        self.assertEqual("critical", verdict["urgency"])
        self.assertEqual("cloud", verdict["route"])
        self.assertEqual(0, byte_count)
        self.assertNotIn("classification", verdict)
        self.assertNotIn("sensitive_data_types", verdict)
        chat.assert_not_called()

    def test_default_and_explicit_legacy_mode_accept_old_model_output(self) -> None:
        response = {
            key: value for key, value in self.model_response().items()
            if key != "classification"
        }
        for kwargs in ({}, {"include_classification": False}):
            with self.subTest(kwargs=kwargs):
                with patch(
                    "app.judge.foundry.chat_local", return_value=(json.dumps(response), 3)
                ) as chat:
                    verdict, _ = judge.classify("Analyze this.", **kwargs)
                self.assertEqual("model", verdict["judge_mode"])
                self.assertEqual("cloud", verdict["route"])
                self.assertNotIn("classification", verdict)
                self.assertNotIn("sensitive_data_types", verdict)
                self.assertEqual(140, chat.call_args.kwargs["max_tokens"])
                self.assertNotIn("timeout_seconds", chat.call_args.kwargs)
                self.assertNotIn("sensitive_data_types", chat.call_args.args[1][0]["content"])


if __name__ == "__main__":
    unittest.main()