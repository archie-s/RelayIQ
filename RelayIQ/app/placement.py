"""Reusable request placement decisions for AI clients."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from time import perf_counter
from typing import Any, Mapping
from uuid import UUID, uuid4

from . import judge, policy, privacy
from .classification import JudgeAssessment


class Placement(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    CLOUD_WITH_CONSENT = "cloud_with_consent"
    DEFERRED = "deferred"


class Fallback(str, Enum):
    LOCAL = "local"


class ReasonCode(str, Enum):
    LOCAL_CAPABILITY_SUFFICIENT = "LOCAL_CAPABILITY_SUFFICIENT"
    CLOUD_CAPABILITY_REQUIRED = "CLOUD_CAPABILITY_REQUIRED"
    NO_ELIGIBLE_MODEL = "NO_ELIGIBLE_MODEL"
    SENSITIVE_DATA_DETECTED = "SENSITIVE_DATA_DETECTED"
    CONNECTIVITY_UNAVAILABLE = "CONNECTIVITY_UNAVAILABLE"
    CLOUD_BUDGET_EXCEEDED = "CLOUD_BUDGET_EXCEEDED"
    CLASSIFIER_DEGRADED = "CLASSIFIER_DEGRADED"


class TargetClass(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    CLOUD_WITH_CONSENT = "cloud_with_consent"
    FALLBACK = "fallback"


class FallbackAction(str, Enum):
    LOCAL = "local"
    STOP = "stop"


class V2ReasonCode(str, Enum):
    LOCAL_CAPABILITY_SUFFICIENT = "LOCAL_CAPABILITY_SUFFICIENT"
    CLOUD_CAPABILITY_REQUIRED = "CLOUD_CAPABILITY_REQUIRED"
    SENSITIVE_DATA_DETECTED = "SENSITIVE_DATA_DETECTED"
    CONNECTIVITY_UNAVAILABLE = "CONNECTIVITY_UNAVAILABLE"
    CLOUD_BUDGET_EXCEEDED = "CLOUD_BUDGET_EXCEEDED"
    CLASSIFIER_DEGRADED = "CLASSIFIER_DEGRADED"
    PREFERENCE_HONORED = "PREFERENCE_HONORED"
    PREFERENCE_OVERRIDDEN = "PREFERENCE_OVERRIDDEN"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    NO_ELIGIBLE_MODEL = "NO_ELIGIBLE_MODEL"


class PlacementServiceUnavailableError(RuntimeError):
    """Raised when the placement service cannot produce any decision."""


@dataclass(frozen=True)
class PlacementContext:
    connectivity: bool = True
    cloud_budget_kb: float | None = None
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    available_local: tuple[str, ...] = field(default_factory=tuple)
    available_cloud: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PlacementDecision:
    decision_id: UUID
    placement: Placement
    policy: str
    permitted_payload: str
    requires_consent: bool
    fallback: Fallback
    suggested_models: tuple[str, ...]
    reason_codes: tuple[ReasonCode, ...]
    explanation: str
    # Private diagnostics retained for the scheduler and evaluation tools.
    factors: tuple[str, ...]
    verdict: dict[str, Any]
    privacy: dict[str, Any]
    timings_ms: dict[str, float]
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "placement": self.placement,
            "policy": self.policy,
            "permitted_payload": self.permitted_payload,
            "requires_consent": self.requires_consent,
            "fallback": self.fallback,
            "suggested_models": list(self.suggested_models),
            "reason_codes": list(self.reason_codes),
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class PlacementContextV2:
    connectivity: bool = True
    cloud_budget_kb: float | None = None
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PlacementDecisionV2:
    decision_id: UUID
    target_class: TargetClass
    policy_revision: str
    consent_required: bool
    assessment: JudgeAssessment | None
    fallback_action: FallbackAction
    reason_codes: tuple[V2ReasonCode, ...]
    explanation: str | None
    suggested_models: Mapping[str, tuple[str, ...]] | None
    timings_ms: dict[str, float]

    def to_public_dict(self) -> dict[str, Any]:
        response: dict[str, Any] = {
            "decision_id": self.decision_id,
            "target_class": self.target_class,
            "policy_revision": self.policy_revision,
            "requirements": {
                "consent_required": self.consent_required,
            },
            "fallback_action": self.fallback_action,
            "reason_codes": list(self.reason_codes),
        }
        if self.explanation is not None:
            response["explanation"] = self.explanation
        if self.suggested_models is not None:
            response["suggested_models"] = {
                lane: list(models) for lane, models in self.suggested_models.items()
            }
        if self.assessment is not None:
            response.update(self.assessment.model_dump(exclude_unset=True))
        return response


def _unique_models(models: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(models))


def _explain(
    *,
    route: str,
    placement: Placement,
    sensitive: bool,
    connectivity_unavailable: bool,
    budget_exceeded: bool,
    no_eligible_model: bool,
    classifier_degraded: bool,
) -> str:
    if route == "local":
        parts = ["Local capability is sufficient for this request."]
    elif placement == Placement.LOCAL:
        parts = ["Cloud capability is required, but connectivity is unavailable; use the local fallback."]
    elif placement == Placement.DEFERRED:
        parts = [
            "Cloud capability is required, but the estimated request exceeds the available cloud budget."
        ]
    elif placement == Placement.CLOUD_WITH_CONSENT:
        parts = [
            "Cloud capability is required and sensitive data was detected. "
            "Use only the sanitized payload after caller consent."
        ]
    else:
        parts = ["Cloud capability is required and currently available."]

    if sensitive and placement != Placement.CLOUD_WITH_CONSENT:
        parts.append("Sensitive data was detected and sanitized locally.")
    if budget_exceeded and placement != Placement.DEFERRED:
        parts.append("The estimated request also exceeds the available cloud budget.")
    if connectivity_unavailable and placement != Placement.LOCAL:
        parts.append("Connectivity is also unavailable.")
    if no_eligible_model:
        lane = "local" if placement == Placement.LOCAL else "cloud"
        parts.append(f"No eligible {lane} model was advertised.")
    if classifier_degraded:
        parts.append("Deterministic fallback classification was used.")
    return " ".join(parts)


class PlacementEngine:
    """Classify and place a request without executing the selected model."""

    def decide(
        self,
        request: str,
        context: PlacementContext | None = None,
        *,
        force_fallback: bool = False,
    ) -> PlacementDecision:
        context = context or PlacementContext()
        started = perf_counter()

        judge_started = perf_counter()
        verdict, _ = judge.classify(request, force_fallback=force_fallback)
        judge_ms = (perf_counter() - judge_started) * 1000

        policy_started = perf_counter()
        allocation = policy.allocate(verdict)
        verdict["route"] = allocation["route"]
        policy_ms = (perf_counter() - policy_started) * 1000

        privacy_started = perf_counter()
        privacy_result = privacy.inspect_prompt(request, verdict)
        privacy_ms = (perf_counter() - privacy_started) * 1000

        route = allocation["route"]
        estimated_cloud_kb = (len(privacy_result["redacted_prompt"].encode("utf-8")) + 1200) / 1024
        connectivity_unavailable = route == "cloud" and not context.connectivity
        budget_exceeded = (
            route == "cloud"
            and context.cloud_budget_kb is not None
            and estimated_cloud_kb > context.cloud_budget_kb
        )
        sensitive = bool(privacy_result["sensitive"])
        classifier_degraded = verdict.get("judge_mode") == "fallback"

        if route == "local":
            placement = Placement.LOCAL
        elif connectivity_unavailable:
            placement = Placement.LOCAL
        elif budget_exceeded:
            placement = Placement.DEFERRED
        elif sensitive:
            placement = Placement.CLOUD_WITH_CONSENT
        else:
            placement = Placement.CLOUD

        requires_consent = placement == Placement.CLOUD_WITH_CONSENT
        candidate_models = (
            context.available_local
            if placement == Placement.LOCAL
            else context.available_cloud
        )
        suggested_models = _unique_models(candidate_models)
        no_eligible_model = not suggested_models

        reason_codes: list[ReasonCode] = [
            ReasonCode.LOCAL_CAPABILITY_SUFFICIENT
            if route == "local"
            else ReasonCode.CLOUD_CAPABILITY_REQUIRED
        ]
        if no_eligible_model:
            reason_codes.append(ReasonCode.NO_ELIGIBLE_MODEL)
        if sensitive:
            reason_codes.append(ReasonCode.SENSITIVE_DATA_DETECTED)
        if connectivity_unavailable:
            reason_codes.append(ReasonCode.CONNECTIVITY_UNAVAILABLE)
        if budget_exceeded:
            reason_codes.append(ReasonCode.CLOUD_BUDGET_EXCEEDED)
        if classifier_degraded:
            reason_codes.append(ReasonCode.CLASSIFIER_DEGRADED)

        explanation = _explain(
            route=route,
            placement=placement,
            sensitive=sensitive,
            connectivity_unavailable=connectivity_unavailable,
            budget_exceeded=budget_exceeded,
            no_eligible_model=no_eligible_model,
            classifier_degraded=classifier_degraded,
        )

        total_ms = (perf_counter() - started) * 1000
        factors = (
            f"urgency:{verdict['urgency']}",
            f"reasoning:{verdict['complexity']}",
            f"fresh_knowledge:{str(verdict['needs_fresh']).lower()}",
            f"sensitive_data:{str(privacy_result['sensitive']).lower()}",
            f"connectivity:{str(context.connectivity).lower()}",
            f"cloud_budget_kb:{context.cloud_budget_kb if context.cloud_budget_kb is not None else 'unbounded'}",
            f"estimated_cloud_kb:{estimated_cloud_kb:.3f}",
        )
        return PlacementDecision(
            decision_id=uuid4(),
            placement=placement,
            policy=allocation["policy"],
            permitted_payload=privacy_result["redacted_prompt"],
            requires_consent=requires_consent,
            fallback=Fallback.LOCAL,
            suggested_models=suggested_models,
            reason_codes=tuple(reason_codes),
            explanation=explanation,
            factors=factors,
            verdict=verdict,
            privacy=privacy_result,
            timings_ms={
                "judge": round(judge_ms, 3),
                "policy": round(policy_ms, 3),
                "privacy": round(privacy_ms, 3),
                "total": round(total_ms, 3),
            },
            degraded=(
                classifier_degraded
                or connectivity_unavailable
                or placement == Placement.DEFERRED
            ),
        )

    def decide_v2(
        self,
        request: str,
        context: PlacementContextV2 | None = None,
        *,
        preferred_target_class: TargetClass | None = None,
        model_mapping: Mapping[str, tuple[str, ...]] | None = None,
        policy_snapshot: policy.PlacementPolicySnapshot | None = None,
        force_fallback: bool = False,
    ) -> PlacementDecisionV2:
        """Return a Draft 4 recommendation without invoking an answer model or tool."""
        if policy_snapshot is None:
            raise policy.PolicyDecisionUnavailableError("Placement policy is unavailable.")
        context = context or PlacementContextV2()
        started = perf_counter()
        mapping_supplied = model_mapping is not None
        local_models = tuple(model_mapping.get("local", ())) if model_mapping is not None else ()
        cloud_models = tuple(model_mapping.get("cloud", ())) if model_mapping is not None else ()
        empty_suggestions: dict[str, tuple[str, ...]] | None = (
            {"local": (), "cloud": ()} if mapping_supplied else None
        )

        judge_started = perf_counter()
        verdict, _ = judge.classify(
            request,
            context={
                "connectivity": context.connectivity,
                "cloud_budget_kb": context.cloud_budget_kb,
                "required_capabilities": list(context.required_capabilities),
            },
            force_fallback=force_fallback,
            include_classification=True,
        )
        judge_ms = (perf_counter() - judge_started) * 1000
        assessment = None
        if verdict.get("judge_mode") != "fallback":
            assessment_fields: dict[str, Any] = {"classification": verdict.get("classification")}
            if "sensitive_data_types" in verdict:
                assessment_fields["sensitive_data_types"] = verdict["sensitive_data_types"]
            assessment = JudgeAssessment.model_validate(assessment_fields)
        assessment_reasons: tuple[V2ReasonCode, ...] = ()
        if assessment is None:
            assessment_reasons = (V2ReasonCode.CLASSIFIER_DEGRADED,)
        elif assessment.classification.sensitive_data:
            assessment_reasons = (V2ReasonCode.SENSITIVE_DATA_DETECTED,)

        policy_started = perf_counter()
        evaluation = policy.evaluate_v2(request, verdict, policy_snapshot)
        policy_ms = (perf_counter() - policy_started) * 1000

        timings = {
            "judge": round(judge_ms, 3),
            "policy": round(policy_ms, 3),
        }

        if evaluation.blocked or not evaluation.permitted_targets:
            timings["total"] = round((perf_counter() - started) * 1000, 3)
            return PlacementDecisionV2(
                decision_id=uuid4(),
                target_class=TargetClass.FALLBACK,
                policy_revision=policy_snapshot.policy_revision,
                consent_required=False,
                assessment=assessment,
                fallback_action=FallbackAction.STOP,
                reason_codes=(V2ReasonCode.POLICY_BLOCKED,) + assessment_reasons,
                explanation="The loaded policy prohibits model use for this input.",
                suggested_models=empty_suggestions,
                timings_ms=timings,
            )

        local_inventory_eligible = not mapping_supplied or bool(local_models)
        cloud_inventory_eligible = not mapping_supplied or bool(cloud_models)
        local_permitted = "local" in evaluation.permitted_targets
        cloud_permitted = "cloud" in evaluation.permitted_targets

        input_kb = len(request.encode("utf-8")) / 1024
        connectivity_blocked = not context.connectivity
        budget_blocked = (
            context.cloud_budget_kb is not None
            and input_kb > context.cloud_budget_kb
        )
        local_eligible = local_permitted and local_inventory_eligible
        cloud_eligible = (
            cloud_permitted
            and cloud_inventory_eligible
            and not connectivity_blocked
            and not budget_blocked
        )

        desired = (
            preferred_target_class.value
            if preferred_target_class is not None
            else evaluation.route
        )
        eligibility = {"local": local_eligible, "cloud": cloud_eligible}
        if eligibility[desired]:
            selected_lane = TargetClass(desired)
        else:
            alternative = "cloud" if desired == "local" else "local"
            selected_lane = (
                TargetClass(alternative)
                if eligibility[alternative]
                else TargetClass.FALLBACK
            )
        selected = (
            TargetClass.CLOUD_WITH_CONSENT
            if selected_lane == TargetClass.CLOUD
            and bool(verdict.get("requires_consent"))
            else selected_lane
        )

        reasons: list[V2ReasonCode] = []
        cloud_constraints_relevant = desired == "cloud" or (
            not local_eligible and cloud_permitted and not cloud_eligible
        )
        if cloud_constraints_relevant:
            if connectivity_blocked:
                reasons.append(V2ReasonCode.CONNECTIVITY_UNAVAILABLE)
            if budget_blocked:
                reasons.append(V2ReasonCode.CLOUD_BUDGET_EXCEEDED)

        desired_inventory_eligible = (
            local_inventory_eligible if desired == "local" else cloud_inventory_eligible
        )
        if mapping_supplied and not desired_inventory_eligible:
            reasons.append(V2ReasonCode.NO_ELIGIBLE_MODEL)

        if preferred_target_class is not None:
            reasons.append(
                V2ReasonCode.PREFERENCE_HONORED
                if selected_lane.value == desired
                else V2ReasonCode.PREFERENCE_OVERRIDDEN
            )
        elif selected_lane == TargetClass.LOCAL:
            reasons.append(V2ReasonCode.LOCAL_CAPABILITY_SUFFICIENT)
        elif selected_lane == TargetClass.CLOUD:
            reasons.append(V2ReasonCode.CLOUD_CAPABILITY_REQUIRED)

        if selected_lane == TargetClass.FALLBACK:
            if mapping_supplied and V2ReasonCode.NO_ELIGIBLE_MODEL not in reasons:
                reasons.append(V2ReasonCode.NO_ELIGIBLE_MODEL)

        reasons.extend(assessment_reasons)

        can_fallback_local = (
            evaluation.local_failure_permitted
            and local_eligible
            and selected_lane != TargetClass.FALLBACK
        )
        fallback_action = (
            FallbackAction.LOCAL if can_fallback_local else FallbackAction.STOP
        )

        consent_required = selected == TargetClass.CLOUD_WITH_CONSENT
        suggested_models = empty_suggestions
        if mapping_supplied and selected_lane != TargetClass.FALLBACK:
            suggested_models = {"local": local_models, "cloud": cloud_models}

        explanation = self._explain_v2(
            selected=selected,
            desired=desired,
            reasons=reasons,
        )
        timings["total"] = round((perf_counter() - started) * 1000, 3)
        return PlacementDecisionV2(
            decision_id=uuid4(),
            target_class=selected,
            policy_revision=policy_snapshot.policy_revision,
            consent_required=consent_required,
            assessment=assessment,
            fallback_action=fallback_action,
            reason_codes=tuple(dict.fromkeys(reasons)),
            explanation=explanation,
            suggested_models=suggested_models,
            timings_ms=timings,
        )

    @staticmethod
    def _explain_v2(
        *,
        selected: TargetClass,
        desired: str,
        reasons: list[V2ReasonCode],
    ) -> str:
        if selected == TargetClass.FALLBACK:
            return "No permitted and eligible model class can be recommended; stop."
        selected_lane = (
            "cloud"
            if selected in {TargetClass.CLOUD, TargetClass.CLOUD_WITH_CONSENT}
            else selected.value
        )
        if selected_lane == desired:
            if selected == TargetClass.CLOUD_WITH_CONSENT:
                return (
                    "The cloud class is permitted and eligible; "
                    "the judge requires caller consent."
                )
            return f"The {selected.value} class is permitted and eligible."
        constraint = next(
            (
                reason.value
                for reason in reasons
                if reason
                in {
                    V2ReasonCode.CONNECTIVITY_UNAVAILABLE,
                    V2ReasonCode.CLOUD_BUDGET_EXCEEDED,
                    V2ReasonCode.NO_ELIGIBLE_MODEL,
                }
            ),
            "policy constraints",
        )
        return (
            f"The {desired} class is unavailable under {constraint}; "
            f"the eligible {selected_lane} class is recommended."
        )