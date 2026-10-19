import assert from "node:assert/strict";
import test from "node:test";
import type { PlacementDecision } from "../extensions/relayiQ/client.ts";
import { executePlacementFlow, type PlacementFlowDependencies } from "../extensions/relayiQ/flow.ts";

const base: PlacementDecision = {
	decision_id: "4e899b33-13bb-4b77-9e41-54738be58458",
	placement: "local",
	policy: "Local First v1",
	permitted_payload: "original",
	requires_consent: false,
	fallback: "local",
	suggested_models: ["local/model"],
	reason_codes: ["LOCAL_CAPABILITY_SUFFICIENT"],
	explanation: "Local capability is sufficient.",
};

function dependencies(overrides: Partial<PlacementFlowDependencies<string>> = {}) {
	return {
		resolveLocal: () => "local/model",
		resolveCloud: () => "cloud/model",
		modelId: (model: string) => model,
		setLocal: async () => true,
		confirmCloud: async () => true,
		invokeAdvisor: async () => "cloud advice",
		now: () => 123,
		...overrides,
	};
}

test("local and deferred placements never invoke cloud advice", async () => {
	let cloudCalls = 0;
	const deps = dependencies({ invokeAdvisor: async () => (cloudCalls++, "advice") });
	const local = await executePlacementFlow("original", base, deps);
	const deferred = await executePlacementFlow(
		"original",
		{
			...base,
			placement: "deferred",
			policy: "Deep Reasoning v1",
			suggested_models: ["cloud/model"],
			reason_codes: ["CLOUD_CAPABILITY_REQUIRED", "CLOUD_BUDGET_EXCEEDED"],
		},
		deps,
	);
	assert.equal(local.action, "continue");
	assert.equal(deferred.action, "continue");
	assert.equal(cloudCalls, 0);
});

test("cloud placement returns isolated advice to the local continuation", async () => {
	const result = await executePlacementFlow(
		"original private local prompt",
		{
			...base,
			placement: "cloud",
			policy: "Deep Reasoning v1",
			permitted_payload: "sanitized payload",
			suggested_models: ["cloud/model"],
			reason_codes: ["CLOUD_CAPABILITY_REQUIRED"],
		},
		dependencies({
			invokeAdvisor: async (_model, payload) => {
				assert.equal(payload, "sanitized payload");
				return "cloud advice";
			},
		}),
	);
	assert.equal(result.action, "transform");
	assert.match(result.text ?? "", /original private local prompt/);
	assert.match(result.text ?? "", /cloud advice/);
	assert.equal(result.audit.selectedModel, "cloud/model");
});

test("consent denial and unavailable UI remain local", async () => {
	let cloudCalls = 0;
	const decision: PlacementDecision = {
		...base,
		placement: "cloud_with_consent",
		requires_consent: true,
		suggested_models: ["cloud/model"],
		reason_codes: ["CLOUD_CAPABILITY_REQUIRED", "SENSITIVE_DATA_DETECTED"],
	};
	const result = await executePlacementFlow(
		"original",
		decision,
		dependencies({
			confirmCloud: async () => false,
			invokeAdvisor: async () => (cloudCalls++, "advice"),
		}),
	);
	assert.equal(result.action, "continue");
	assert.equal(result.audit.consent, "denied");
	assert.equal(cloudCalls, 0);

	const unavailable = await executePlacementFlow(
		"original",
		decision,
		dependencies({ confirmCloud: async () => undefined }),
	);
	assert.equal(unavailable.action, "continue");
	assert.equal(unavailable.audit.consent, "unavailable");
});

test("missing cloud suggestion safely continues locally", async () => {
	const result = await executePlacementFlow(
		"original",
		{ ...base, placement: "cloud", suggested_models: [], reason_codes: ["NO_ELIGIBLE_MODEL"] },
		dependencies({ resolveCloud: () => undefined }),
	);
	assert.equal(result.action, "continue");
	assert.match(result.message, /no advertised cloud model/i);
});
