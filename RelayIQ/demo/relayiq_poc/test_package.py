from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import materialize as materialize_module
from app.policy import PolicyConfigurationError
from materialize import (
    FIXTURE_SHA256,
    MaterializationError,
    materialize,
)
from verify_materialization import VerificationError, verify_materialization


class RelayIqPocPackageTests(unittest.TestCase):
    def test_materializes_complete_secret_free_package(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "relayiq-007-poc"
            with patch(
                "materialize.repository_state",
                return_value=("test-relayiq-revision", False),
            ):
                materialize(
                    root,
                    local_model_id="phi-4-mini",
                    cloud_model_id="nvidia/nemotron-3.5-lightning-30b-a3b",
                    relayiq_port=18891,
                    foundry_port=15272,
                    agent_revision="agent-test-revision",
                )
            summary = verify_materialization(root)

            self.assertEqual("verified", summary["status"])
            self.assertEqual(FIXTURE_SHA256, summary["fixture_sha256"])
            self.assertEqual(17, summary["scripted_records"])
            self.assertEqual(6, summary["live_records"])
            self.assertEqual("not_run", summary["human_validation"])
            self.assertFalse(summary["relayiq_repository_dirty"])

            routing = json.loads(
                (root / "control" / "007-routing.json").read_text(encoding="utf-8")
            )
            serialized = json.dumps(routing)
            self.assertNotIn("${", serialized)
            self.assertNotIn("api_key\"", serialized)
            self.assertEqual("NVIDIA_API_KEY", routing["cloud"]["api_key_env"])
            self.assertEqual(
                "http://127.0.0.1:18891", routing["service_url"]
            )
            inventory = json.loads(
                (root / "control" / "revision-inventory.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn(
                "config/relayiq-policy.json", inventory["relayiq_source_sha256"]
            )
            self.assertNotIn(
                "templates/relayiq-policy.json",
                inventory["relayiq_source_sha256"],
            )

    def test_rejects_existing_destination_and_same_ports(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            existing = Path(parent) / "existing"
            existing.mkdir()
            with self.assertRaises(MaterializationError):
                materialize(
                    existing,
                    local_model_id="local",
                    cloud_model_id="cloud",
                    relayiq_port=18891,
                    foundry_port=15272,
                    agent_revision=None,
                    allow_dirty_repository=True,
                )

            fresh = Path(parent) / "fresh"
            with self.assertRaises(MaterializationError):
                materialize(
                    fresh,
                    local_model_id="local",
                    cloud_model_id="cloud",
                    relayiq_port=18891,
                    foundry_port=18891,
                    agent_revision=None,
                    allow_dirty_repository=True,
                )

    def test_rejects_destination_inside_repository(self) -> None:
        repository_destination = (
            Path(__file__).resolve().parent / "must-not-materialize"
        )
        with self.assertRaises(MaterializationError):
            materialize(
                repository_destination,
                local_model_id="local",
                cloud_model_id="cloud",
                relayiq_port=18891,
                foundry_port=15272,
                agent_revision=None,
                allow_dirty_repository=True,
            )
        self.assertFalse(repository_destination.exists())

    def test_rejects_dirty_repository_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            with (
                patch(
                    "materialize.repository_state",
                    return_value=("test-revision", True),
                ),
                self.assertRaises(MaterializationError),
            ):
                materialize(
                    Path(parent) / "relayiq-007-poc",
                    local_model_id="local",
                    cloud_model_id="cloud",
                    relayiq_port=18891,
                    foundry_port=15272,
                    agent_revision=None,
                )

    def test_rejects_frozen_source_hash_drift(self) -> None:
        path = "demo/relayiq_poc/templates/demo-profile.json"
        expected = materialize_module.RELAYIQ_SOURCE_SHA256[path]
        with patch.dict(
            materialize_module.RELAYIQ_SOURCE_SHA256,
            {path: "0" * 64},
        ):
            with self.assertRaisesRegex(
                MaterializationError, "does not match the frozen handoff"
            ):
                materialize_module.validate_sources()
        self.assertEqual(materialize_module.RELAYIQ_SOURCE_SHA256[path], expected)

    def test_reports_malformed_policy_as_materialization_error(self) -> None:
        with (
            patch(
                "app.policy.load_placement_policy",
                side_effect=PolicyConfigurationError("invalid test policy"),
            ),
            self.assertRaisesRegex(
                MaterializationError, "policy configuration is invalid"
            ),
        ):
            materialize_module.validate_sources()

    def test_verifier_detects_fixture_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "relayiq-007-poc"
            materialize(
                root,
                local_model_id="local",
                cloud_model_id="cloud",
                relayiq_port=18891,
                foundry_port=15272,
                agent_revision=None,
                allow_dirty_repository=True,
            )
            fixture = root / "workspace" / "demo_employee_record.txt"
            fixture.write_text("changed\n", encoding="utf-8")
            with self.assertRaises(VerificationError):
                verify_materialization(root)


if __name__ == "__main__":
    unittest.main()
