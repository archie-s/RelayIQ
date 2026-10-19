"""Policy decisions for intelligence placement and human-readable explanations."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import config


class PolicyConfigurationError(ValueError):
    """Raised when trusted placement policy configuration is invalid."""


class PolicyDecisionUnavailableError(RuntimeError):
    """Raised when policy evaluation cannot produce a recommendation."""


@dataclass(frozen=True)
class PlacementPolicySnapshot:
    policy_revision: str
    permitted_targets: frozenset[str]
    local_failure_permitted: bool
    deny_inputs: frozenset[str]


@dataclass(frozen=True)
class PlacementPolicyEvaluation:
    route: str
    policy_name: str
    permitted_targets: frozenset[str]
    local_failure_permitted: bool
    blocked: bool


def _string_list(
    value: object,
    *,
    field_name: str,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PolicyConfigurationError(f"{field_name} must be an array.")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise PolicyConfigurationError(f"{field_name} must contain non-blank strings.")
    if len(set(value)) != len(value):
        raise PolicyConfigurationError(f"{field_name} must not contain duplicates.")
    if allowed is not None and not set(value).issubset(allowed):
        raise PolicyConfigurationError(f"{field_name} contains an unsupported value.")
    return tuple(value)


def load_placement_policy(path: str | Path) -> PlacementPolicySnapshot:
    """Load and validate a trusted operator-controlled policy snapshot."""
    config_path = Path(path)
    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyConfigurationError(
            f"Unable to load placement policy configuration at {config_path}."
        ) from exc

    if not isinstance(document, dict):
        raise PolicyConfigurationError("Placement policy configuration must be an object.")

    expected_fields = {
        "policy_revision",
        "permitted_targets",
        "local_failure_permitted",
        "deny_inputs",
    }
    if set(document) != expected_fields:
        raise PolicyConfigurationError(
            "Placement policy configuration fields do not match the supported schema."
        )

    revision = document["policy_revision"]
    if not isinstance(revision, str) or not revision.strip():
        raise PolicyConfigurationError("policy_revision must be a non-blank string.")

    local_failure_permitted = document["local_failure_permitted"]
    if not isinstance(local_failure_permitted, bool):
        raise PolicyConfigurationError("local_failure_permitted must be a boolean.")

    permitted_targets = _string_list(
        document["permitted_targets"],
        field_name="permitted_targets",
        allowed=frozenset({"local", "cloud"}),
    )
    deny_inputs = _string_list(document["deny_inputs"], field_name="deny_inputs")

    return PlacementPolicySnapshot(
        policy_revision=revision,
        permitted_targets=frozenset(permitted_targets),
        local_failure_permitted=local_failure_permitted,
        deny_inputs=frozenset(deny_inputs),
    )


def evaluate_v2(
    request: str,
    verdict: dict,
    snapshot: PlacementPolicySnapshot,
) -> PlacementPolicyEvaluation:
    """Evaluate classifier output against one immutable policy snapshot."""
    allocation = allocate(verdict)
    return PlacementPolicyEvaluation(
        route=allocation["route"],
        policy_name=allocation["policy"],
        permitted_targets=snapshot.permitted_targets,
        local_failure_permitted=snapshot.local_failure_permitted,
        blocked=request in snapshot.deny_inputs,
    )


def cloud_model_name(is_real_cloud: bool) -> str:
    if is_real_cloud or config.cloud_is_real():
        return config.AZURE_OPENAI_DEPLOYMENT
    return f"{config.CLOUD_SIM_MODEL} (simulated cloud)"


def allocate(verdict: dict) -> dict:
    """Apply placement policies to Judge signals."""
    if verdict["needs_fresh"]:
        return {"route": "cloud", "policy": "Fresh Knowledge v1"}
    if verdict["complexity"] == "high":
        return {"route": "cloud", "policy": "Deep Reasoning v1"}
    return {"route": "local", "policy": "Local First v1"}


def decision_record(
    verdict: dict,
    privacy_result: dict,
    status: str,
    connectivity: bool,
    is_real_cloud: bool = False,
) -> dict:
    """Describe why a request was placed locally, in cloud, or in both."""
    sensitive = privacy_result["sensitive"]
    requires_review = privacy_result["requires_human_review"]
    emergency = privacy_result["emergency_override"]
    needs_fresh = verdict["needs_fresh"]
    is_complex = verdict["complexity"] == "high"

    if sensitive and requires_review:
        policy_name = "Privacy Guard v2"
        placement = "Hybrid" if emergency or status in ("blocked-local", "answered") else "Pending approval"
        if status == "blocked-local":
            headline = f"Request kept on {config.LOCAL_MODEL}."
            action = "Cloud forwarding was declined. The original request remained local."
        elif status in ("queued", "deferred", "running-cloud", "done"):
            placement = "Hybrid"
            headline = f"Sanitized request sent to {cloud_model_name(is_real_cloud)}."
            action = "A local model removed detected identifiers before the approved cloud request."
        elif emergency:
            headline = f"Immediate guidance generated by {config.LOCAL_MODEL}."
            action = "The sanitized cloud request is paused until a person approves it."
        else:
            headline = "Cloud forwarding paused for human approval."
            action = "Review the locally sanitized payload before it leaves this device."
        reason = (
            "Personal or sensitive information was detected. The requested reasoning or fresh "
            "information exceeds the local-only policy, so Use AI in the Right Place sanitized the cloud payload locally."
        )
    elif allocate(verdict)["route"] == "local":
        policy_name = "Local First v1"
        placement = "Local"
        headline = f"Request answered by {config.LOCAL_MODEL}."
        reason = "The request does not require fresh information or extended reasoning."
        action = "Process entirely on device and avoid a cloud call."
    elif not connectivity or status in ("answered-offline",):
        policy_name = "Resilient Fallback v1"
        placement = "Local now, cloud later"
        headline = f"Cloud unavailable. Best-effort guidance generated by {config.LOCAL_MODEL}."
        reason = "The policy prefers cloud capability, but connectivity is unavailable."
        action = "Answer locally now and retain the request for prioritized refinement after reconnection."
    elif status == "cloud-error":
        policy_name = "Resilient Fallback v1"
        placement = "Cloud unavailable"
        headline = f"Request to {cloud_model_name(is_real_cloud)} could not complete."
        reason = "The cloud lane returned an authentication, deployment, or service error."
        action = "Keep the scheduler available and retry after the cloud configuration is restored."
    elif needs_fresh:
        policy_name = "Fresh Knowledge v1"
        placement = "Cloud"
        headline = f"Request sent to {cloud_model_name(is_real_cloud)}."
        reason = "A correct answer depends on current information that the local model may not contain."
        action = "Use the cloud lane when connectivity and the data budget permit."
    else:
        policy_name = "Deep Reasoning v1"
        placement = "Cloud"
        headline = f"Request sent to {cloud_model_name(is_real_cloud)}."
        reason = "The Judge identified extended reasoning that exceeds the local-first capability policy."
        action = "Use the cloud lane when connectivity and the data budget permit."

    factors = [
        f"Urgency: {verdict['urgency']}",
        f"Reasoning: {verdict['complexity']}",
        f"Fresh knowledge: {'required' if needs_fresh else 'not required'}",
        f"Web search: {'required' if needs_fresh else 'not required'}",
        f"Sensitive data: {'detected and sanitized' if sensitive else 'not detected'}",
        f"Network: {'available' if connectivity else 'unavailable'}",
    ]

    return {
        "placement": placement,
        "headline": headline,
        "reason": reason,
        "action": action,
        "policy": policy_name,
        "factors": factors,
    }
