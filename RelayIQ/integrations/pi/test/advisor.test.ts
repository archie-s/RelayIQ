import assert from "node:assert/strict";
import test from "node:test";
import { invokeCloudAdvisor } from "../extensions/relayiQ/advisor.ts";

test("cloud advisor receives only one permitted-payload message and no tools", async () => {
	let capturedContext: any;
	let capturedOptions: any;
	const registry = {
		async complete(_model: unknown, context: unknown, options: unknown) {
			capturedContext = context;
			capturedOptions = options;
			return {
				content: [{ type: "text", text: "Isolated advice" }],
				stopReason: "stop",
			};
		},
	};
	const result = await invokeCloudAdvisor(
		registry as any,
		{ provider: "cloud", id: "model-A" } as any,
		"[REDACTED_EMPLOYEE_ID]",
	);
	assert.equal(result, "Isolated advice");
	assert.deepEqual(capturedContext.messages, [
		{
			role: "user",
			content: [{ type: "text", text: "[REDACTED_EMPLOYEE_ID]" }],
			timestamp: capturedContext.messages[0].timestamp,
		},
	]);
	assert.deepEqual(capturedContext.tools, []);
	assert.equal("systemPrompt" in capturedContext, false);
	assert.equal(capturedContext.messages.length, 1);
	assert.equal(capturedOptions.cacheRetention, "none");
	assert.equal(capturedOptions.maxRetries, 0);
});

test("cloud advisor rejects empty and failed responses", async () => {
	await assert.rejects(
		invokeCloudAdvisor(
			{ complete: async () => ({ content: [], stopReason: "stop" }) } as any,
			{} as any,
			"payload",
		),
		/no text/,
	);
	await assert.rejects(
		invokeCloudAdvisor(
			{
				complete: async () => ({
					content: [],
					stopReason: "error",
					errorMessage: "provider failed",
				}),
			} as any,
			{} as any,
			"payload",
		),
		/provider failed/,
	);
});
