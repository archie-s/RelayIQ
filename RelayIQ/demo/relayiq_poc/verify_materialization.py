"""Verify a materialized bounded RelayIQ + 007 POC package."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


FIXTURE_SHA256 = "fcef61e4a34ede0d4b9bff6a9996ab8f1558d58524ac78706f0d16ef6f024c45"
PROFILE_SHA256 = "34d46a82e28753e3648e9e835a9223d41a6880c0d6565a3d1eb3be94789d6cad"
POLICY_SHA256 = "d404aa1bfe75e38de0ca93424a977f57385fe8e8aec4838dec14c82af3256e4c"
EXPECTED_SCENARIOS = {f"POC-{index:02d}" for index in range(1, 18)}
EXPECTED_LIVE = {f"POC-{index:02d}" for index in range(1, 7)}
EXPECTED_HUMAN_DELIVERABLES = {
    ("D01", "scripted scenario runner/tests"),
    ("D01", "synthetic manifest/evidence"),
    ("D02", "live demo record"),
    ("D02", "exact reproduction steps"),
    ("D03", "POC acceptance report"),
    ("D03", "operator runbook"),
    ("D03", "final revision inventory"),
}
ALLOWED_RESULT_VALUES = {"not_run", "pass", "fail", "blocked"}


class VerificationError(ValueError):
    """Raised when materialized integration artifacts are inconsistent."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"Unable to read valid JSON from {path}.") from exc


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_no_placeholders(document: Any, label: str) -> None:
    serialized = json.dumps(document, ensure_ascii=False)
    if "${" in serialized:
        raise VerificationError(f"{label} contains an unresolved placeholder.")


def ensure_secret_free(document: Any, label: str) -> None:
    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = key.lower().replace("-", "_")
                if normalized in {"authorization", "api_key", "token", "secret"}:
                    raise VerificationError(
                        f"{label} contains prohibited credential field {'.'.join((*path, key))}."
                    )
                walk(item, (*path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, (*path, str(index)))

    walk(document)


def validate_results(records: list[dict[str, Any]], expected: set[str], label: str) -> None:
    ids = {record.get("id") for record in records}
    if ids != expected or len(records) != len(expected):
        raise VerificationError(f"{label} scenario IDs are incomplete or duplicated.")
    for record in records:
        if record.get("result") not in ALLOWED_RESULT_VALUES:
            raise VerificationError(f"{label} contains an invalid result value.")
        if record.get("result") == "not_run" and record.get("evidence_paths"):
            raise VerificationError(f"{label} not_run record cannot claim evidence.")


def verify_materialization(root: Path) -> dict[str, Any]:
    destination = root.resolve()
    if not destination.is_dir():
        raise VerificationError("Materialized root does not exist.")
    if any((candidate / ".git").exists() for candidate in (destination, *destination.parents)):
        raise VerificationError("Materialized root must be outside every Git repository.")

    control = destination / "control"
    workspace = destination / "workspace"
    evidence = destination / "evidence"
    required_files = (
        control / "007-routing.json",
        control / "demo-profile.json",
        control / "relayiq-policy.json",
        control / "revision-inventory.json",
        workspace / "demo-manifest.json",
        workspace / "demo_employee_record.txt",
        evidence / "scenario-results.json",
        evidence / "human-validation-record.json",
        evidence / "evidence-index.json",
    )
    missing = [str(path) for path in required_files if not path.is_file()]
    if missing:
        raise VerificationError(f"Materialized files are missing: {missing}")

    routing = load_json(control / "007-routing.json")
    profile = load_json(control / "demo-profile.json")
    policy = load_json(control / "relayiq-policy.json")
    inventory = load_json(control / "revision-inventory.json")
    manifest = load_json(workspace / "demo-manifest.json")
    results = load_json(evidence / "scenario-results.json")
    validation = load_json(evidence / "human-validation-record.json")
    evidence_index = load_json(evidence / "evidence-index.json")

    for label, document in (
        ("routing", routing),
        ("profile", profile),
        ("policy", policy),
        ("inventory", inventory),
        ("manifest", manifest),
        ("scenario results", results),
        ("human validation", validation),
        ("evidence index", evidence_index),
    ):
        ensure_no_placeholders(document, label)
        ensure_secret_free(document, label)

    if routing["service_url"].split(":", 2)[:2] != ["http", "//127.0.0.1"]:
        raise VerificationError("RelayIQ service URL must be loopback HTTP.")
    if not routing["local"]["base_url"].startswith("http://127.0.0.1:"):
        raise VerificationError("Foundry base URL must be loopback HTTP.")
    if routing["cloud"]["provider"] != "nvidia":
        raise VerificationError("Cloud provider must remain NVIDIA.")
    if routing["cloud"]["api_key_env"] != "NVIDIA_API_KEY":
        raise VerificationError("Only the NVIDIA API-key environment name may be stored.")
    if routing["demo_profile"]["id"] != "demo-redaction-v1":
        raise VerificationError("Routing profile is not demo-redaction-v1.")

    fixture = workspace / "demo_employee_record.txt"
    fixture_bytes = fixture.read_bytes()
    if len(fixture_bytes) != 81 or sha256(fixture) != FIXTURE_SHA256:
        raise VerificationError("Materialized fixture does not match frozen bytes.")
    if b"\r" in fixture_bytes or not fixture_bytes.endswith(b"\n"):
        raise VerificationError("Materialized fixture line endings are not LF.")
    if manifest["workspace_root"] != str(workspace.resolve()):
        raise VerificationError("Manifest workspace root does not match the materialized root.")
    if manifest["profile_id"] != "demo-redaction-v1":
        raise VerificationError("Manifest profile is not demo-redaction-v1.")
    if manifest["files"] != [
        {
            "path": "demo_employee_record.txt",
            "sha256": FIXTURE_SHA256,
            "bytes": 81,
            "encoding": "UTF-8",
            "newlines": "LF",
        }
    ]:
        raise VerificationError("Manifest fixture inventory does not match frozen content.")
    if routing["demo_profile"]["manifest"] != str(
        (workspace / "demo-manifest.json").resolve()
    ):
        raise VerificationError("Routing manifest path does not match materialized workspace.")
    if profile["id"] != manifest["profile_id"]:
        raise VerificationError("Profile and manifest IDs differ.")
    if sha256(control / "demo-profile.json") != PROFILE_SHA256:
        raise VerificationError("Materialized profile does not match the frozen handoff.")
    if policy["policy_revision"] != "relayiq-judge-v1":
        raise VerificationError("RelayIQ policy revision is not relayiq-judge-v1.")
    if "supported_redaction_profiles" in policy:
        raise VerificationError("RelayIQ Draft 4 policy must not contain redaction profiles.")
    if sha256(control / "relayiq-policy.json") != POLICY_SHA256:
        raise VerificationError("Materialized policy does not match the frozen handoff.")
    if not isinstance(inventory.get("relayiq_repository_dirty"), bool):
        raise VerificationError("Revision inventory must record worktree cleanliness.")

    materialized_hashes = inventory["materialized_files"]
    for relative_path, expected_hash in materialized_hashes.items():
        actual_path = destination / Path(relative_path)
        if sha256(actual_path) != expected_hash:
            raise VerificationError(f"Revision inventory hash mismatch for {relative_path}.")

    validate_results(results["scripted"], EXPECTED_SCENARIOS, "scripted")
    validate_results(results["live"], EXPECTED_LIVE, "live")
    human_entries = {
        (entry.get("task_id"), entry.get("deliverable"))
        for entry in validation["entries"]
    }
    if human_entries != EXPECTED_HUMAN_DELIVERABLES:
        raise VerificationError("Human-validation deliverables are incomplete.")
    if any(entry.get("result") != "not_run" for entry in validation["entries"]):
        raise VerificationError("Fresh human-validation records must start not_run.")
    if evidence_index["entries"]:
        raise VerificationError("Fresh evidence index must not claim observations.")

    return {
        "status": "verified",
        "root": str(destination),
        "relayiq_revision": inventory["relayiq_revision"],
        "agent_revision": inventory["agent_revision"],
        "relayiq_repository_dirty": inventory["relayiq_repository_dirty"],
        "fixture_sha256": FIXTURE_SHA256,
        "scripted_records": len(results["scripted"]),
        "live_records": len(results["live"]),
        "human_validation": "not_run",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a materialized RelayIQ + 007 bounded POC package."
    )
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = verify_materialization(args.root)
    except VerificationError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
