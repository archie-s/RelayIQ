import { buildLocalContinuation } from "./advisor.ts";
import type { PlacementDecision } from "./client.ts";
import type { ConsentOutcome, DecisionAudit } from "./state.ts";

export interface PlacementFlowDependencies<TModel> {
	resolveLocal(suggestions: readonly string[]): TModel | undefined;
	resolveCloud(suggestions: readonly string[]): TModel | undefined;
	modelId(model: TModel): string;
	setLocal(model: TModel): Promise<boolean>;
	confirmCloud(decision: PlacementDecision, modelId: string): Promise<boolean | undefined>;
	invokeAdvisor(model: TModel, permittedPayload: string): Promise<string>;
	now(): number;
}

export interface PlacementFlowResult {
	action: "continue" | "transform" | "handled";
	text?: string;
	audit: DecisionAudit;
	message: string;
}

function audit(
	decision: PlacementDecision,
	dependencies: PlacementFlowDependencies<unknown>,
	consent: ConsentOutcome,
	selectedModel?: string,
): DecisionAudit {
	return {
		kind: "decision",
		decisionId: decision.decision_id,
		placement: decision.placement,
		policy: decision.policy,
		reasonCodes: [...decision.reason_codes],
		selectedModel,
		consent,
		timestamp: dependencies.now(),
	};
}

export async function executePlacementFlow<TModel>(
	originalInput: string,
	decision: PlacementDecision,
	dependencies: PlacementFlowDependencies<TModel>,
): Promise<PlacementFlowResult> {
	if (decision.placement === "local") {
		const local = dependencies.resolveLocal(decision.suggested_models);
		if (local && !(await dependencies.setLocal(local))) {
			return {
				action: "handled",
				audit: audit(decision, dependencies as PlacementFlowDependencies<unknown>, "not_required"),
				message: "RelayIQ selected a local model, but Pi could not activate it.",
			};
		}
		const selectedModel = local ? dependencies.modelId(local) : undefined;
		return {
			action: "continue",
			audit: audit(
				decision,
				dependencies as PlacementFlowDependencies<unknown>,
				"not_required",
				selectedModel,
			),
			message: decision.explanation,
		};
	}

	if (decision.placement === "deferred") {
		return {
			action: "continue",
			audit: audit(decision, dependencies as PlacementFlowDependencies<unknown>, "not_required"),
			message: decision.explanation,
		};
	}

	const cloud = dependencies.resolveCloud(decision.suggested_models);
	if (!cloud) {
		return {
			action: "continue",
			audit: audit(
				decision,
				dependencies as PlacementFlowDependencies<unknown>,
				decision.requires_consent ? "unavailable" : "not_required",
			),
			message: "RelayIQ selected cloud, but no advertised cloud model is available. Continuing locally.",
		};
	}
	const selectedModel = dependencies.modelId(cloud);
	let consent: ConsentOutcome = "not_required";
	if (decision.requires_consent) {
		const approved = await dependencies.confirmCloud(decision, selectedModel);
		consent = approved === undefined ? "unavailable" : approved ? "approved" : "denied";
		if (!approved) {
			return {
				action: "continue",
				audit: audit(
					decision,
					dependencies as PlacementFlowDependencies<unknown>,
					consent,
					selectedModel,
				),
				message:
					approved === undefined
						? "Cloud consent requires an interactive UI. Continuing locally."
						: "Cloud consent was not granted. Continuing locally.",
			};
		}
	}
	const advice = await dependencies.invokeAdvisor(cloud, decision.permitted_payload);
	return {
		action: "transform",
		text: buildLocalContinuation(originalInput, advice, decision.decision_id),
		audit: audit(
			decision,
			dependencies as PlacementFlowDependencies<unknown>,
			consent,
			selectedModel,
		),
		message: decision.explanation,
	};
}
