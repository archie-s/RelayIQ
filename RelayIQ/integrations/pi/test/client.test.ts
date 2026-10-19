import assert from "node:assert/strict";
import test from "node:test";
import {
	decidePlacement,
	normalizeEndpoint,
	parsePlacementDecision,
	RelayIQClientError,
	type PlacementDecision,
} from "../extensions/relayiQ/client.ts";

const decision: PlacementDecision = {
	decision_id: "4e899b33-13bb-4b77-9e41-54738be58458",
	placement: "cloud",
	policy: "Deep Reasoning v1",
	permitted_payload: "Analyze this.",
	requires_consent: false,
	fallback: "local",
	suggested_models: ["cloud/model-A"],
	reason_codes: ["CLOUD_CAPABILITY_REQUIRED"],
	explanation: "Cloud capability is required.",
};

test("parses the exact RelayIQ response contract", () => {
	assert.deepEqual(parsePlacementDecision(decision), decision);
	assert.throws(
		() => parsePlacementDecision({ ...decision, factors: [] }),
		(error: unknown) => error instanceof RelayIQClientError && error.code === "INVALID_RESPONSE",
	);
	assert.throws(
		() => parsePlacementDecision({ ...decision, placement: "cloud_with_consent" }),
		/consent invariant/,
	);
});

test("allows loopback HTTP and requires HTTPS elsewhere", () => {
	assert.equal(normalizeEndpoint("http://127.0.0.1:8000/"), "http://127.0.0.1:8000");
	assert.equal(normalizeEndpoint("https://relayiq.example/"), "https://relayiq.example");
	assert.throws(() => normalizeEndpoint("http://relayiq.example"), /requires HTTPS/);
});

test("sends only the v1 request fields and preserves explicit null budget", async () => {
	let body: unknown;
	let redirect: RequestRedirect | undefined;
	const fetchImpl: typeof fetch = async (_input, init) => {
		body = JSON.parse(String(init?.body));
		redirect = init?.redirect;
		return new Response(JSON.stringify(decision), {
			status: 200,
			headers: { "content-type": "application/json" },
		});
	};
	const result = await decidePlacement("Analyze this.", {
		endpoint: "http://localhost:8000",
		timeoutMs: 1_000,
		fetchImpl,
		context: {
			cloud_budget_kb: null,
			required_capabilities: [],
			available_local: ["local/model"],
			available_cloud: ["cloud/model-A"],
		},
	});
	assert.equal(result.decision_id, decision.decision_id);
	assert.equal(redirect, "error");
	assert.deepEqual(body, {
		input: "Analyze this.",
		context: {
			cloud_budget_kb: null,
			required_capabilities: [],
			available_local: ["local/model"],
			available_cloud: ["cloud/model-A"],
		},
	});
});

test("maps service errors without returning a success-shaped fallback", async () => {
	const fetchImpl: typeof fetch = async () =>
		new Response(
			JSON.stringify({
				error: "SERVICE_UNAVAILABLE",
				message: "Placement unavailable.",
				decision_id: null,
			}),
			{ status: 503 },
		);
	await assert.rejects(
		decidePlacement("Analyze this.", {
			endpoint: "http://localhost:8000",
			timeoutMs: 1_000,
			fetchImpl,
			context: {
				required_capabilities: [],
				available_local: [],
				available_cloud: [],
			},
		}),
		(error: unknown) =>
			error instanceof RelayIQClientError &&
			error.code === "SERVICE_ERROR" &&
			error.status === 503,
	);
});
