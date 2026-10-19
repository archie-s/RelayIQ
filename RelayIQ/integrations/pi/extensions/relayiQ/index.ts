import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Text } from "@earendil-works/pi-tui";
import { invokeCloudAdvisor } from "./advisor.ts";
import { decidePlacement, RelayIQClientError, type PlacementDecision } from "./client.ts";
import { readLocalModelIds, readRelayIQConfig, registerRelayIQFlags } from "./config.ts";
import { executePlacementFlow } from "./flow.ts";
import {
	buildModelInventory,
	canonicalModelId,
	resolveSuggestedModel,
	type ModelInventory,
	type PiModel,
} from "./models.ts";
import {
	auditSummary,
	RELAYIQ_STATE_TYPE,
	restoreRelayIQState,
	type RelayIQAudit,
} from "./state.ts";

interface RuntimeState {
	enabled: boolean;
	lastAudit?: RelayIQAudit;
	changingModel: boolean;
}

function availableModels(ctx: ExtensionContext): PiModel[] {
	return ctx.scopedModels.length > 0
		? ctx.scopedModels.map((entry) => entry.model)
		: ctx.modelRegistry.getAvailable();
}

function buildLocalInventory(pi: ExtensionAPI, ctx: ExtensionContext): ModelInventory {
	return buildModelInventory(availableModels(ctx), readLocalModelIds(pi), []);
}

async function ensureLocalExecutor(
	pi: ExtensionAPI,
	ctx: ExtensionContext,
	inventory: ModelInventory,
	state: RuntimeState,
): Promise<boolean> {
	const currentId = ctx.model ? canonicalModelId(ctx.model) : undefined;
	if (currentId && inventory.localIds.includes(currentId)) return true;
	const local = inventory.local[0];
	if (!local) {
		ctx.ui.notify("RelayIQ has no available configured local model; prompt blocked.", "error");
		return false;
	}
	state.changingModel = true;
	try {
		const selected = await pi.setModel(local);
		if (!selected) {
			ctx.ui.notify(`RelayIQ could not activate local model ${canonicalModelId(local)}.`, "error");
		}
		return selected;
	} catch (error) {
		const detail = error instanceof Error ? error.message : String(error);
		ctx.ui.notify(
			`RelayIQ could not activate local model ${canonicalModelId(local)}: ${detail}`,
			"error",
		);
		return false;
	} finally {
		state.changingModel = false;
	}
}

function fallbackCode(error: unknown): string {
	if (error instanceof RelayIQClientError) {
		return error.status === undefined ? error.code : `${error.code}_${error.status}`;
	}
	return "CLOUD_ADVISOR_ERROR";
}

function appendAudit(pi: ExtensionAPI, state: RuntimeState, audit: RelayIQAudit): void {
	state.lastAudit = audit;
	pi.appendEntry(RELAYIQ_STATE_TYPE, audit);
}

function showDecision(pi: ExtensionAPI, ctx: ExtensionContext, audit: RelayIQAudit, message: string): void {
	const summary = auditSummary(audit);
	ctx.ui.setStatus("relayiq", `RelayIQ: ${summary}`);
	pi.sendMessage(
		{
			customType: "relayiq-decision",
			content: `RelayIQ: ${summary}\n${message}`,
			display: true,
			details: audit,
		},
		{ triggerTurn: false },
	);
}

function showLocalFallback(
	pi: ExtensionAPI,
	ctx: ExtensionContext,
	state: RuntimeState,
	code: string,
	detail?: string,
): void {
	const audit: RelayIQAudit = { kind: "error", code, timestamp: Date.now() };
	appendAudit(pi, state, audit);
	showDecision(pi, ctx, audit, "Continuing with the local executor.");
	if (detail) ctx.ui.notify(detail, "error");
}

function consentText(decision: PlacementDecision, modelId: string): string {
	return [
		`Policy: ${decision.policy}`,
		`Model: ${modelId}`,
		decision.explanation,
		"",
		"Only this sanitized payload will be sent:",
		decision.permitted_payload,
	].join("\n");
}

export default function relayiQExtension(pi: ExtensionAPI): void {
	const state: RuntimeState = { enabled: true, changingModel: false };
	registerRelayIQFlags(pi);

	pi.registerMessageRenderer("relayiq-decision", (message, _options, theme) => {
		return new Text(theme.fg("accent", String(message.content)), 1, 0);
	});

	pi.registerEntryRenderer<RelayIQAudit>(RELAYIQ_STATE_TYPE, (entry, _options, theme) => {
		return new Text(theme.fg("dim", `RelayIQ: ${auditSummary(entry.data)}`), 1, 0);
	});

	pi.on("session_start", async (_event, ctx) => {
		const restored = restoreRelayIQState(
			ctx.sessionManager.getEntries() as Array<{
				type: string;
				customType?: string;
				data?: unknown;
			}>,
		);
		state.enabled = restored.enabled;
		state.lastAudit = restored.lastAudit;
		await ensureLocalExecutor(pi, ctx, buildLocalInventory(pi, ctx), state);
		ctx.ui.setStatus(
			"relayiq",
			`RelayIQ: ${state.enabled ? "on" : "routing off; local boundary enforced"}`,
		);
	});

	pi.on("model_select", async (event, ctx) => {
		if (state.changingModel) return;
		const inventory = buildLocalInventory(pi, ctx);
		if (inventory.localIds.includes(canonicalModelId(event.model))) return;
		ctx.ui.notify("RelayIQ keeps the primary Pi session local; restoring the local executor.", "warning");
		await ensureLocalExecutor(pi, ctx, inventory, state);
	});

	pi.on("input", async (event, ctx) => {
		if (!(await ensureLocalExecutor(pi, ctx, buildLocalInventory(pi, ctx), state))) {
			return { action: "handled" as const };
		}
		if (!state.enabled || event.source === "extension" || event.streamingBehavior !== undefined) {
			return { action: "continue" as const };
		}
		if (event.images && event.images.length > 0) {
			ctx.ui.notify("RelayIQ v1 approves text only; image prompt kept local.", "info");
			return { action: "continue" as const };
		}
		let config;
		try {
			config = readRelayIQConfig(pi, state.enabled);
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			showLocalFallback(pi, ctx, state, "CONFIGURATION_ERROR", message);
			return { action: "continue" as const };
		}
		const inventory = buildModelInventory(availableModels(ctx), config.localModels, config.cloudModels);

		try {
			const decision = await decidePlacement(event.text, {
				endpoint: config.endpoint,
				timeoutMs: config.timeoutMs,
				signal: ctx.signal,
				context: {
					...(config.cloudBudgetKb !== undefined ? { cloud_budget_kb: config.cloudBudgetKb } : {}),
					required_capabilities: [],
					available_local: inventory.localIds,
					available_cloud: inventory.cloudIds,
				},
			});
			const result = await executePlacementFlow<PiModel>(event.text, decision, {
				resolveLocal: (suggestions) => resolveSuggestedModel(suggestions, inventory.local),
				resolveCloud: (suggestions) => resolveSuggestedModel(suggestions, inventory.cloud),
				modelId: (model) => canonicalModelId(model),
				setLocal: async (model) => {
					state.changingModel = true;
					try {
						return await pi.setModel(model);
					} finally {
						state.changingModel = false;
					}
				},
				confirmCloud: async (placement, modelId) => {
					if (!ctx.hasUI) return undefined;
					return ctx.ui.confirm("RelayIQ cloud consent", consentText(placement, modelId));
				},
				invokeAdvisor: (model, payload) =>
					invokeCloudAdvisor(ctx.modelRegistry, model, payload, ctx.signal),
				now: Date.now,
			});
			appendAudit(pi, state, result.audit);
			showDecision(pi, ctx, result.audit, result.message);
			if (result.action === "transform") {
				return { action: "transform" as const, text: result.text ?? event.text };
			}
			return { action: result.action };
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			showLocalFallback(pi, ctx, state, fallbackCode(error), message);
			return { action: "continue" as const };
		}
	});

	pi.registerCommand("relayiq-status", {
		description: "Show RelayIQ integration status",
		handler: async (_args, ctx) => {
			try {
				const config = readRelayIQConfig(pi, state.enabled);
				ctx.ui.notify(
					[
						`RelayIQ: ${state.enabled ? "enabled" : "disabled"}`,
						`Endpoint: ${config.endpoint}`,
						`Local: ${config.localModels.join(", ") || "(not configured)"}`,
						`Cloud: ${config.cloudModels.join(", ") || "(not configured)"}`,
						`Last: ${auditSummary(state.lastAudit)}`,
					].join("\n"),
					"info",
				);
			} catch (error) {
				ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
			}
		},
	});

	pi.registerCommand("relayiq-on", {
		description: "Enable RelayIQ placement for this session",
		handler: async (_args, ctx) => {
			state.enabled = true;
			const audit: RelayIQAudit = { kind: "enabled", enabled: true, timestamp: Date.now() };
			appendAudit(pi, state, audit);
			await ensureLocalExecutor(pi, ctx, buildLocalInventory(pi, ctx), state);
			ctx.ui.setStatus("relayiq", "RelayIQ: on");
		},
	});

	pi.registerCommand("relayiq-off", {
		description: "Disable RelayIQ placement for this session",
		handler: async (_args, ctx) => {
			state.enabled = false;
			const audit: RelayIQAudit = { kind: "enabled", enabled: false, timestamp: Date.now() };
			appendAudit(pi, state, audit);
			await ensureLocalExecutor(pi, ctx, buildLocalInventory(pi, ctx), state);
			ctx.ui.setStatus("relayiq", "RelayIQ: routing off; local boundary enforced");
		},
	});
}
