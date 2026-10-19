"""Materialize the bounded RelayIQ + 007 POC into a fresh non-Git directory."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]
FIXTURE_SOURCE = PACKAGE_ROOT / "fixtures" / "demo_employee_record.txt.b64"
PROFILE_SOURCE = PACKAGE_ROOT / "templates" / "demo-profile.json"
ROUTING_SOURCE = PACKAGE_ROOT / "templates" / "007-routing.json"
POLICY_SOURCE = REPOSITORY_ROOT / "config" / "relayiq-policy.json"

FIXTURE_SHA256 = "fcef61e4a34ede0d4b9bff6a9996ab8f1558d58524ac78706f0d16ef6f024c45"
HANDOFF_SOURCE_SHA256 = {
    "demo-profile.json": "34d46a82e28753e3648e9e835a9223d41a6880c0d6565a3d1eb3be94789d6cad",
    "demo-scenarios.json": "57332866bc632fd0fa28c0210bdef082f0d89b73cf78f9e654fe1d5dcddbfa58",
    "expanded-scenarios.json": "ccdc68ab1f432702c61c9491b9e38f378fd91ee9bf44386b03d6aeffc9cc0fa3",
    "fixtures/demo_employee_record.txt": FIXTURE_SHA256,
    "templates/007-routing.json": "87fd72fefc214e34edc09b53e307bde89ef573537999c91c64bb1d3165553433",
    "templates/relayiq-policy.json": "a64d48618e8882ac90ff1af329f1037978d1f8a0cc5128b18414bd22f78f34d1",
}
RELAYIQ_SOURCE_SHA256 = {
    "demo/relayiq_poc/templates/demo-profile.json": (
        HANDOFF_SOURCE_SHA256["demo-profile.json"]
    ),
    "demo/relayiq_poc/templates/007-routing.json": (
        HANDOFF_SOURCE_SHA256["templates/007-routing.json"]
    ),
    "config/relayiq-policy.json": "d404aa1bfe75e38de0ca93424a977f57385fe8e8aec4838dec14c82af3256e4c",
}
PERMITTED_ROOT_MESSAGES = (
    "Demonstrate the configured demo block.",
    (
        "For Alex Example (alex.example@example.com), read "
        "demo_employee_record.txt using read_file, then report only the balance."
    ),
    "What is 12 + 15? Reply with only the number.",
)
SCENARIOS = (
    ("POC-01", "Local preference selects the local answer model", "live_and_scripted", True),
    ("POC-02", "Cloud handles the whole tool turn and redacts evolving context", "live_and_scripted", True),
    ("POC-03", "Configured policy block overrides local failure mode", "live_and_scripted", True),
    ("POC-04", "Offline cloud preference yields a direct local recommendation", "live_and_scripted", True),
    ("POC-05", "Declining cloud consent stops execution", "live_and_scripted", True),
    ("POC-06", "User explicitly chooses a permitted local alternative", "live_and_scripted", True),
    ("POC-07", "RelayIQ unreachable uses the explicit harness failure policy", "scripted_required_live_optional", False),
    ("POC-08", "Changed fixture blocks further cloud requests", "scripted_required", False),
    ("POC-09", "Post-dispatch provider failure does not switch or replay", "scripted_required", False),
    ("POC-10", "Zero budget selects local without pretending connectivity failed", "scripted_required_live_optional", False),
    ("POC-11", "An empty supplied inventory yields fallback/stop", "api_only_scripted", False),
    ("POC-12", "Pending approval rejects configuration changes and stale replies", "scripted_required", False),
    ("POC-13", "A child path inherits the selected guarded client", "core_regression_scripted", False),
    ("POC-14", "Without the flag, legacy behavior is preserved", "core_regression_scripted", False),
    ("POC-15", "A retired response profile field is rejected", "invalid_peer_scripted", False),
    ("POC-16", "Missing cloud key does not prevent a local turn", "scripted_required", False),
    ("POC-17", "Cloud preparation fails before inference; permitted local fallback runs", "scripted_required", False),
)
HUMAN_DELIVERABLES = (
    ("D01", "scripted scenario runner/tests"),
    ("D01", "synthetic manifest/evidence"),
    ("D02", "live demo record"),
    ("D02", "exact reproduction steps"),
    ("D03", "POC acceptance report"),
    ("D03", "operator runbook"),
    ("D03", "final revision inventory"),
)


class MaterializationError(ValueError):
    """Raised when the requested bounded demo package would be unsafe."""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_json(path: Path, document: Any) -> None:
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def replace_placeholders(document: Any, replacements: dict[str, str]) -> Any:
    if isinstance(document, dict):
        return {
            key: replace_placeholders(value, replacements)
            for key, value in document.items()
        }
    if isinstance(document, list):
        return [replace_placeholders(value, replacements) for value in document]
    if isinstance(document, str):
        for placeholder, replacement in replacements.items():
            document = document.replace(placeholder, replacement)
    return document


def repository_state(repository: Path) -> tuple[str, bool]:
    revision_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    status_result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return revision_result.stdout.strip(), bool(status_result.stdout.strip())


def validate_text_value(name: str, value: str) -> str:
    if not value or not value.strip():
        raise MaterializationError(f"{name} must be a non-blank value.")
    if any(character in value for character in ("\0", "\r", "\n")):
        raise MaterializationError(f"{name} must be a single-line value.")
    if "${" in value:
        raise MaterializationError(f"{name} cannot contain an unresolved placeholder.")
    return value


def validate_port(name: str, value: int) -> int:
    if not 1 <= value <= 65535:
        raise MaterializationError(f"{name} must be between 1 and 65535.")
    return value


def path_has_git_ancestor(path: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in (path, *path.parents))


def validate_destination(output_root: Path) -> Path:
    if not output_root.is_absolute():
        raise MaterializationError("--output-root must be an absolute path.")
    resolved = output_root.resolve()
    if resolved.exists():
        raise MaterializationError("--output-root must not already exist.")
    if path_has_git_ancestor(resolved.parent):
        raise MaterializationError("--output-root must be outside every Git repository.")
    return resolved


def validate_sources() -> None:
    try:
        fixture = base64.b64decode(
            FIXTURE_SOURCE.read_text(encoding="ascii").strip(),
            validate=True,
        )
    except (UnicodeError, ValueError) as exc:
        raise MaterializationError("Packaged fixture encoding is invalid.") from exc
    if len(fixture) != 81 or sha256_bytes(fixture) != FIXTURE_SHA256:
        raise MaterializationError("Packaged fixture bytes do not match the frozen fixture.")
    if b"\r" in fixture or not fixture.endswith(b"\n"):
        raise MaterializationError("Packaged fixture must use UTF-8 LF line endings.")

    source_files = {
        "demo/relayiq_poc/templates/demo-profile.json": PROFILE_SOURCE,
        "demo/relayiq_poc/templates/007-routing.json": ROUTING_SOURCE,
        "config/relayiq-policy.json": POLICY_SOURCE,
    }
    for relative_path, source_path in source_files.items():
        actual = sha256_bytes(source_path.read_bytes())
        expected = RELAYIQ_SOURCE_SHA256[relative_path]
        if actual != expected:
            raise MaterializationError(
                f"Packaged source hash does not match the frozen handoff: {relative_path}."
            )

    profile = json.loads(PROFILE_SOURCE.read_text(encoding="utf-8"))
    if profile["id"] != "demo-redaction-v1":
        raise MaterializationError("Packaged profile ID is not demo-redaction-v1.")
    if profile["status"] != "synthetic-demo-only":
        raise MaterializationError("Packaged profile must remain synthetic-demo-only.")

    sys.path.insert(0, str(REPOSITORY_ROOT))
    try:
        from app.policy import PolicyConfigurationError, load_placement_policy

        try:
            snapshot = load_placement_policy(POLICY_SOURCE)
        except PolicyConfigurationError as exc:
            raise MaterializationError(
                "Committed RelayIQ policy configuration is invalid."
            ) from exc
    finally:
        sys.path.pop(0)
    if snapshot.policy_revision != "relayiq-judge-v1":
        raise MaterializationError("Committed RelayIQ policy revision is unexpected.")


def scenario_record(
    scenario_id: str,
    title: str,
    mode: str,
    mandatory_live: bool,
    *,
    execution_mode: str,
) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "title": title,
        "catalog_mode": mode,
        "execution_mode": execution_mode,
        "mandatory_live": mandatory_live,
        "result": "not_run",
        "decision_id": None,
        "selected_model": None,
        "counters": None,
        "predicate_results": [],
        "evidence_paths": [],
        "blocking_issue": None,
    }


def materialize(
    output_root: Path,
    *,
    local_model_id: str,
    cloud_model_id: str,
    relayiq_port: int,
    foundry_port: int,
    agent_revision: str | None,
    allow_dirty_repository: bool = False,
) -> Path:
    validate_sources()
    destination = validate_destination(output_root)
    local_model = validate_text_value("LOCAL_MODEL_ID", local_model_id)
    cloud_model = validate_text_value("CLOUD_MODEL_ID", cloud_model_id)
    relay_port = validate_port("RELAYIQ_PORT", relayiq_port)
    foundry_service_port = validate_port("FOUNDRY_PORT", foundry_port)
    if relay_port == foundry_service_port:
        raise MaterializationError("RelayIQ and Foundry ports must differ.")
    if agent_revision is not None:
        validate_text_value("agent_revision", agent_revision)
    relayiq_revision, relayiq_dirty = repository_state(REPOSITORY_ROOT)
    if relayiq_dirty and not allow_dirty_repository:
        raise MaterializationError(
            "RelayIQ worktree must be clean before recording its revision."
        )

    control = destination / "control"
    workspace = destination / "workspace"
    evidence = destination / "evidence"
    control.mkdir(parents=True)
    workspace.mkdir()
    evidence.mkdir()

    fixture_target = workspace / "demo_employee_record.txt"
    fixture_bytes = base64.b64decode(
        FIXTURE_SOURCE.read_text(encoding="ascii").strip(),
        validate=True,
    )
    fixture_target.write_bytes(fixture_bytes)
    workspace_root = str(workspace.resolve())

    manifest = {
        "manifest_version": "1.0",
        "status": "materialized_not_executed",
        "profile_id": "demo-redaction-v1",
        "profile_claim": (
            "Exact registered fixtures and bounded source handling; not general "
            "PII detection or pii-v1 conformance."
        ),
        "workspace_root": workspace_root,
        "permitted_root_messages": list(PERMITTED_ROOT_MESSAGES),
        "files": [
            {
                "path": "demo_employee_record.txt",
                "sha256": sha256_bytes(fixture_bytes),
                "bytes": len(fixture_bytes),
                "encoding": "UTF-8",
                "newlines": "LF",
            }
        ],
        "guards": json.loads(PROFILE_SOURCE.read_text(encoding="utf-8"))["guards"],
    }
    write_json(workspace / "demo-manifest.json", manifest)

    replacements = {
        "${RELAYIQ_PORT}": str(relay_port),
        "${LOCAL_MODEL_ID}": local_model,
        "${FOUNDRY_PORT}": str(foundry_service_port),
        "${CLOUD_MODEL_ID}": cloud_model,
        "${DEMO_ROOT}": workspace_root,
    }
    routing_template = json.loads(ROUTING_SOURCE.read_text(encoding="utf-8"))
    routing = replace_placeholders(routing_template, replacements)
    write_json(control / "007-routing.json", routing)
    shutil.copyfile(PROFILE_SOURCE, control / "demo-profile.json")
    shutil.copyfile(POLICY_SOURCE, control / "relayiq-policy.json")

    revision_inventory = {
        "status": "materialized_not_executed",
        "relayiq_repository": "mmorozova_microsoft/RelayIQ",
        "relayiq_revision": relayiq_revision,
        "relayiq_repository_dirty": relayiq_dirty,
        "relayiq_worktree": str(REPOSITORY_ROOT),
        "agent_repository": "xehertze_microsoft/Mini-general-agent",
        "agent_revision": agent_revision,
        "handoff_plan_version": "1.1",
        "handoff_source_sha256": HANDOFF_SOURCE_SHA256,
        "relayiq_source_sha256": RELAYIQ_SOURCE_SHA256,
        "materialized_files": {
            "control/007-routing.json": sha256_bytes(
                (control / "007-routing.json").read_bytes()
            ),
            "control/demo-profile.json": sha256_bytes(
                (control / "demo-profile.json").read_bytes()
            ),
            "control/relayiq-policy.json": sha256_bytes(
                (control / "relayiq-policy.json").read_bytes()
            ),
            "workspace/demo-manifest.json": sha256_bytes(
                (workspace / "demo-manifest.json").read_bytes()
            ),
            "workspace/demo_employee_record.txt": sha256_bytes(fixture_bytes),
        },
    }
    write_json(control / "revision-inventory.json", revision_inventory)

    scenario_results = {
        "schema_version": "1.0",
        "scope": (
            "Bounded synthetic POC integration evidence. Expected templates are "
            "not observations."
        ),
        "relayiq_revision": revision_inventory["relayiq_revision"],
        "agent_revision": agent_revision,
        "scripted": [
            scenario_record(*scenario, execution_mode="scripted")
            for scenario in SCENARIOS
        ],
        "live": [
            scenario_record(*scenario, execution_mode="live")
            for scenario in SCENARIOS
            if scenario[3]
        ],
    }
    write_json(evidence / "scenario-results.json", scenario_results)

    human_validation = {
        "plan_version": "1.1",
        "status": "materialized_not_executed",
        "result_values": ["not_run", "pass", "fail", "blocked"],
        "entries": [
            {
                "task_id": task_id,
                "deliverable": deliverable,
                "reviewer": None,
                "reviewed_at": None,
                "result_revision": None,
                "mode": None,
                "commands_or_actions": None,
                "observed_result": None,
                "evidence_paths": [],
                "result": "not_run",
                "blocking_issue": None,
            }
            for task_id, deliverable in HUMAN_DELIVERABLES
        ],
    }
    write_json(evidence / "human-validation-record.json", human_validation)
    write_json(
        evidence / "evidence-index.json",
        {
            "schema_version": "1.0",
            "status": "materialized_not_executed",
            "scope": "Synthetic fixtures and metadata only.",
            "entries": [],
            "prohibited_content": [
                "authorization headers",
                "API key values",
                "arbitrary user data",
                "general environment dumps",
                "unsanitized provider-bound messages",
            ],
        },
    )

    from verify_materialization import verify_materialization

    verify_materialization(destination)
    return destination


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a secret-free RelayIQ + 007 demo package in a new non-Git "
            "directory. This command does not start services or invoke models."
        )
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--local-model-id", required=True)
    parser.add_argument("--cloud-model-id", required=True)
    parser.add_argument("--relayiq-port", type=int, required=True)
    parser.add_argument("--foundry-port", type=int, required=True)
    parser.add_argument(
        "--agent-revision",
        help="Optional committed 007 revision to bind into the evidence inventory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        destination = materialize(
            args.output_root,
            local_model_id=args.local_model_id,
            cloud_model_id=args.cloud_model_id,
            relayiq_port=args.relayiq_port,
            foundry_port=args.foundry_port,
            agent_revision=args.agent_revision,
        )
    except (
        MaterializationError,
        OSError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"materialization failed: {exc}", file=sys.stderr)
        return 1
    print(f"materialized and verified: {destination}")
    print(f"routing config: {destination / 'control' / '007-routing.json'}")
    print(f"demo workspace: {destination / 'workspace'}")
    print(f"evidence records: {destination / 'evidence'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
