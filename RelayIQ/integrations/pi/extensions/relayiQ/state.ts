import type { Placement, ReasonCode } from "./client.ts";

export const RELAYIQ_STATE_TYPE = "relayiq-state";

export type ConsentOutcome = "not_required" | "approved" | "denied" | "unavailable";

export interface DecisionAudit {
	kind: "decision";
	decisionId: string;
	placement: Placement;
	policy: string;
	reasonCodes: ReasonCode[];
	selectedModel?: string;
	consent: ConsentOutcome;
	timestamp: number;
}

export interface ErrorAudit {
	kind: "error";
	code: string;
	timestamp: number;
}

export interface EnabledAudit {
	kind: "enabled";
	enabled: boolean;
	timestamp: number;
}

export type RelayIQAudit = DecisionAudit | ErrorAudit | EnabledAudit;

interface SessionEntry {
	type: string;
	customType?: string;
	data?: unknown;
}

const CONSENT_OUTCOMES = new Set<ConsentOutcome>(["not_required", "approved", "denied", "unavailable"]);

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseAudit(value: unknown): RelayIQAudit | undefined {
	if (!isRecord(value) || typeof value.timestamp !== "number") return undefined;
	if (value.kind === "enabled" && typeof value.enabled === "boolean") {
		return { kind: "enabled", enabled: value.enabled, timestamp: value.timestamp };
	}
	if (value.kind === "error" && typeof value.code === "string") {
		return { kind: "error", code: value.code, timestamp: value.timestamp };
	}
	if (
		value.kind === "decision" &&
		typeof value.decisionId === "string" &&
		typeof value.placement === "string" &&
		typeof value.policy === "string" &&
		Array.isArray(value.reasonCodes) &&
		value.reasonCodes.every((code) => typeof code === "string") &&
		typeof value.consent === "string" &&
		CONSENT_OUTCOMES.has(value.consent as ConsentOutcome) &&
		(value.selectedModel === undefined || typeof value.selectedModel === "string")
	) {
		return {
			kind: "decision",
			decisionId: value.decisionId,
			placement: value.placement as Placement,
			policy: value.policy,
			reasonCodes: value.reasonCodes as ReasonCode[],
			...(typeof value.selectedModel === "string" ? { selectedModel: value.selectedModel } : {}),
			consent: value.consent as ConsentOutcome,
			timestamp: value.timestamp,
		};
	}
	return undefined;
}

export function restoreRelayIQState(entries: readonly SessionEntry[]): {
	enabled: boolean;
	lastAudit?: RelayIQAudit;
} {
	let enabled = true;
	let lastAudit: RelayIQAudit | undefined;
	for (const entry of entries) {
		if (entry.type !== "custom" || entry.customType !== RELAYIQ_STATE_TYPE) continue;
		const audit = parseAudit(entry.data);
		if (!audit) continue;
		lastAudit = audit;
		if (audit.kind === "enabled") enabled = audit.enabled;
	}
	return { enabled, lastAudit };
}

export function auditSummary(audit: RelayIQAudit | undefined): string {
	if (!audit) return "No RelayIQ decision recorded.";
	if (audit.kind === "enabled") return `RelayIQ ${audit.enabled ? "enabled" : "disabled"}.`;
	if (audit.kind === "error") return `RelayIQ local fallback: ${audit.code}`;
	const model = audit.selectedModel ? ` via ${audit.selectedModel}` : "";
	return `${audit.placement}${model} [${audit.reasonCodes.join(", ")}]`;
}
