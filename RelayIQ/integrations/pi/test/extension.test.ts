import assert from "node:assert/strict";
import test from "node:test";
import relayiQExtension from "../extensions/relayiQ/index.ts";

function model(provider: string, id: string) {
	return {
		provider,
		id,
		name: id,
		api: "openai-responses",
		baseUrl: "https://example.test",
		reasoning: false,
		input: ["text"],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: 128_000,
		maxTokens: 4_096,
	};
}

const local = model("local", "phi");
const cloud = model("cloud", "reasoner");

function decision(placement: "cloud" | "cloud_with_consent") {
	return {
		decision_id: "4e899b33-13bb-4b77-9e41-54738be58458",
		placement,
		policy: "Deep Reasoning v1",
		permitted_payload: "sanitized payload",
		requires_consent: placement === "cloud_with_consent",
		fallback: "local",
		suggested_models: ["cloud/reasoner"],
		reason_codes:
			placement === "cloud"
				? ["CLOUD_CAPABILITY_REQUIRED"]
				: ["CLOUD_CAPABILITY_REQUIRED", "SENSITIVE_DATA_DETECTED"],
		explanation: "Cloud capability is required.",
	};
}

function harness(hasUI = true) {
	const handlers = new Map<string, Function>();
	const commands = new Map<string, Function>();
	const entries: unknown[] = [];
	const messages: unknown[] = [];
	const notifications: string[] = [];
	const completions: unknown[] = [];
	const flags = new Map<string, string>([
		["relayiq-local-models", "local/phi"],
		["relayiq-cloud-models", "cloud/reasoner"],
	]);
	const registry = {
		getAvailable: () => [local, cloud],
		async complete(_model: unknown, context: unknown) {
			completions.push(context);
			return { content: [{ type: "text", text: "cloud advice" }], stopReason: "stop" };
		},
	};
	const ctx: any = {
		hasUI,
		mode: hasUI ? "tui" : "print",
		model: local,
		scopedModels: [],
		signal: undefined,
		modelRegistry: registry,
		sessionManager: { getEntries: () => [] },
		ui: {
			notify: (message: string) => notifications.push(message),
			setStatus: () => {},
			confirm: async () => true,
		},
	};
	const api: any = {
		registerFlag: () => {},
		getFlag: (name: string) => flags.get(name),
		registerMessageRenderer: () => {},
		registerEntryRenderer: () => {},
		registerCommand: (name: string, command: { handler: Function }) => commands.set(name, command.handler),
		on: (name: string, handler: Function) => handlers.set(name, handler),
		setModel: async (next: unknown) => {
			ctx.model = next;
			return true;
		},
		appendEntry: (_type: string, data: unknown) => entries.push(data),
		sendMessage: (message: unknown) => messages.push(message),
	};
	relayiQExtension(api);
	return {
		api,
		ctx,
		entries,
		messages,
		notifications,
		completions,
		commands,
		sessionStart: handlers.get("session_start") as (event: unknown, ctx: unknown) => Promise<void>,
		modelSelect: handlers.get("model_select") as (event: unknown, ctx: unknown) => Promise<void>,
		input: handlers.get("input") as (event: unknown, ctx: unknown) => Promise<any>,
	};
}

test("extension cloud path keeps Pi local and sends only permitted payload to advisor", async () => {
	const originalFetch = globalThis.fetch;
	globalThis.fetch = async () => new Response(JSON.stringify(decision("cloud")), { status: 200 });
	try {
		const app = harness();
		const result = await app.input(
			{ type: "input", text: "private local prompt", source: "interactive" },
			app.ctx,
		);
		assert.equal(result.action, "transform");
		assert.match(result.text, /private local prompt/);
		assert.match(result.text, /cloud advice/);
		assert.equal(app.ctx.model, local);
		assert.equal(app.completions.length, 1);
		assert.deepEqual((app.completions[0] as any).messages[0].content, [
			{ type: "text", text: "sanitized payload" },
		]);
		assert.deepEqual((app.completions[0] as any).tools, []);
		assert.equal(JSON.stringify(app.entries).includes("private local prompt"), false);
		assert.equal(JSON.stringify(app.entries).includes("sanitized payload"), false);
		assert.equal(JSON.stringify(app.entries).includes("cloud advice"), false);
	} finally {
		globalThis.fetch = originalFetch;
	}
});

test("consent-required work stays local when no dialog UI is available", async () => {
	const originalFetch = globalThis.fetch;
	globalThis.fetch = async () =>
		new Response(JSON.stringify(decision("cloud_with_consent")), { status: 200 });
	try {
		const app = harness(false);
		const result = await app.input(
			{ type: "input", text: "private local prompt", source: "interactive" },
			app.ctx,
		);
		assert.equal(result.action, "continue");
		assert.equal(app.completions.length, 0);
		assert.equal((app.entries[0] as any).consent, "unavailable");
	} finally {
		globalThis.fetch = originalFetch;
	}
});

test("image and extension-originated input bypass RelayIQ cloud routing", async () => {
	const originalFetch = globalThis.fetch;
	let fetchCalls = 0;
	globalThis.fetch = async () => {
		fetchCalls++;
		return new Response(JSON.stringify(decision("cloud")), { status: 200 });
	};
	try {
		const app = harness();
		const imageResult = await app.input(
			{
				type: "input",
				text: "inspect image",
				source: "interactive",
				images: [{ type: "image" }],
			},
			app.ctx,
		);
		app.ctx.model = cloud;
		const extensionResult = await app.input(
			{ type: "input", text: "generated", source: "extension" },
			app.ctx,
		);
		assert.equal(imageResult.action, "continue");
		assert.equal(extensionResult.action, "continue");
		assert.equal(fetchCalls, 0);
		assert.equal(app.completions.length, 0);
		assert.equal(app.ctx.model, local);
	} finally {
		globalThis.fetch = originalFetch;
	}
});

test("session restore and disabled routing still enforce the local executor", async () => {
	const app = harness();
	app.ctx.model = cloud;
	await app.sessionStart({ type: "session_start" }, app.ctx);
	assert.equal(app.ctx.model, local);

	await app.commands.get("relayiq-off")?.("", app.ctx);
	app.ctx.model = cloud;
	await app.modelSelect({ type: "model_select", model: cloud }, app.ctx);
	assert.equal(app.ctx.model, local);

	app.ctx.model = cloud;
	const result = await app.input(
		{ type: "input", text: "stay local", source: "interactive" },
		app.ctx,
	);
	assert.equal(result.action, "continue");
	assert.equal(app.ctx.model, local);
});

test("a local-model activation exception blocks the prompt", async () => {
	const app = harness();
	app.ctx.model = cloud;
	app.api.setModel = async () => {
		throw new Error("authentication unavailable");
	};
	const result = await app.input(
		{ type: "input", text: "must not reach cloud", source: "extension" },
		app.ctx,
	);
	assert.equal(result.action, "handled");
	assert.equal(app.ctx.model, cloud);
	assert.equal(app.completions.length, 0);
});

test("invalid placement configuration continues locally", async () => {
	const app = harness();
	app.api.getFlag = (name: string) =>
		name === "relayiq-url" ? "http://relayiq.example" : name === "relayiq-local-models" ? "local/phi" : undefined;
	const result = await app.input(
		{ type: "input", text: "stay local", source: "interactive" },
		app.ctx,
	);
	assert.equal(result.action, "continue");
	assert.deepEqual(app.entries[0], {
		kind: "error",
		code: "CONFIGURATION_ERROR",
		timestamp: (app.entries[0] as any).timestamp,
	});
});

test("service error details remain ephemeral and are not persisted", async () => {
	const originalFetch = globalThis.fetch;
	globalThis.fetch = async () =>
		new Response(
			JSON.stringify({
				error: "SERVICE_UNAVAILABLE",
				message: "sensitive remote diagnostic",
				decision_id: null,
			}),
			{ status: 503 },
		);
	try {
		const app = harness();
		const result = await app.input(
			{ type: "input", text: "private local prompt", source: "interactive" },
			app.ctx,
		);
		assert.equal(result.action, "continue");
		assert.equal(JSON.stringify(app.entries).includes("sensitive remote diagnostic"), false);
		assert.equal(JSON.stringify(app.messages).includes("sensitive remote diagnostic"), false);
		assert.equal((app.entries[0] as any).code, "SERVICE_ERROR_503");
	} finally {
		globalThis.fetch = originalFetch;
	}
});
