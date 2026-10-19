import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { normalizeEndpoint } from "./client.ts";

export interface RelayIQConfig {
	enabled: boolean;
	endpoint: string;
	timeoutMs: number;
	localModels: string[];
	cloudModels: string[];
	cloudBudgetKb?: number | null;
}

function parseList(value: string | undefined): string[] {
	if (!value) return [];
	const seen = new Set<string>();
	const result: string[] = [];
	for (const item of value.split(",").map((part) => part.trim())) {
		if (item && !seen.has(item)) {
			seen.add(item);
			result.push(item);
		}
	}
	return result;
}

function parsePositiveInteger(value: string | undefined, fallback: number): number {
	if (!value) return fallback;
	const parsed = Number(value);
	if (!Number.isInteger(parsed) || parsed <= 0) {
		throw new Error(`Expected a positive integer, received "${value}".`);
	}
	return parsed;
}

function parseBudget(value: string | undefined): number | null | undefined {
	if (value === undefined || value === "" || value === "observed") return undefined;
	if (value === "null" || value === "unbounded") return null;
	const parsed = Number(value);
	if (!Number.isFinite(parsed) || parsed < 0) {
		throw new Error(`Invalid cloud budget "${value}".`);
	}
	return parsed;
}

function stringFlag(pi: ExtensionAPI, name: string): string | undefined {
	const value = pi.getFlag(name);
	return typeof value === "string" && value.length > 0 ? value : undefined;
}

export function registerRelayIQFlags(pi: ExtensionAPI): void {
	pi.registerFlag("relayiq-url", { type: "string", description: "RelayIQ service URL" });
	pi.registerFlag("relayiq-local-models", {
		type: "string",
		description: "Comma-separated local provider/model IDs",
	});
	pi.registerFlag("relayiq-cloud-models", {
		type: "string",
		description: "Comma-separated cloud provider/model IDs",
	});
	pi.registerFlag("relayiq-timeout-ms", { type: "string", description: "RelayIQ request timeout" });
	pi.registerFlag("relayiq-cloud-budget-kb", {
		type: "string",
		description: "Observed, unbounded, or a nonnegative request budget",
	});
}

export function readLocalModelIds(pi: ExtensionAPI): string[] {
	return parseList(
		stringFlag(pi, "relayiq-local-models") ?? process.env.RELAYIQ_PI_LOCAL_MODELS,
	);
}

export function readRelayIQConfig(pi: ExtensionAPI, enabled: boolean): RelayIQConfig {
	const endpoint = normalizeEndpoint(
		stringFlag(pi, "relayiq-url") ?? process.env.RELAYIQ_PI_URL ?? "http://127.0.0.1:8000",
	);
	return {
		enabled,
		endpoint,
		timeoutMs: parsePositiveInteger(
			stringFlag(pi, "relayiq-timeout-ms") ?? process.env.RELAYIQ_PI_TIMEOUT_MS,
			10_000,
		),
		localModels: readLocalModelIds(pi),
		cloudModels: parseList(
			stringFlag(pi, "relayiq-cloud-models") ?? process.env.RELAYIQ_PI_CLOUD_MODELS,
		),
		cloudBudgetKb: parseBudget(
			stringFlag(pi, "relayiq-cloud-budget-kb") ?? process.env.RELAYIQ_PI_CLOUD_BUDGET_KB,
		),
	};
}
