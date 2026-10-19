export const PLACEMENTS = ["local", "cloud", "cloud_with_consent", "deferred"] as const;
export type Placement = (typeof PLACEMENTS)[number];

export const REASON_CODES = [
	"LOCAL_CAPABILITY_SUFFICIENT",
	"CLOUD_CAPABILITY_REQUIRED",
	"NO_ELIGIBLE_MODEL",
	"SENSITIVE_DATA_DETECTED",
	"CONNECTIVITY_UNAVAILABLE",
	"CLOUD_BUDGET_EXCEEDED",
	"CLASSIFIER_DEGRADED",
] as const;
export type ReasonCode = (typeof REASON_CODES)[number];

export interface PlacementDecision {
	decision_id: string;
	placement: Placement;
	policy: string;
	permitted_payload: string;
	requires_consent: boolean;
	fallback: "local";
	suggested_models: string[];
	reason_codes: ReasonCode[];
	explanation: string;
}

export interface PlacementRequestContext {
	cloud_budget_kb?: number | null;
	required_capabilities: string[];
	available_local: string[];
	available_cloud: string[];
}

export interface DecideOptions {
	endpoint: string;
	timeoutMs: number;
	context: PlacementRequestContext;
	signal?: AbortSignal;
	fetchImpl?: typeof fetch;
}

export class RelayIQClientError extends Error {
	constructor(
		message: string,
		readonly code: "INVALID_ENDPOINT" | "TRANSPORT_ERROR" | "SERVICE_ERROR" | "INVALID_RESPONSE",
		readonly status?: number,
	) {
		super(message);
		this.name = "RelayIQClientError";
	}
}

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

export function normalizeEndpoint(value: string): string {
	let endpoint: URL;
	try {
		endpoint = new URL(value);
	} catch {
		throw new RelayIQClientError(`Invalid RelayIQ endpoint: ${value}`, "INVALID_ENDPOINT");
	}
	if (endpoint.protocol !== "https:" && !(endpoint.protocol === "http:" && LOOPBACK_HOSTS.has(endpoint.hostname))) {
		throw new RelayIQClientError(
			"RelayIQ requires HTTPS unless the endpoint is loopback.",
			"INVALID_ENDPOINT",
		);
	}
	return endpoint.toString().replace(/\/$/, "");
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
	return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isUuid(value: unknown): value is string {
	return (
		typeof value === "string" &&
		/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
	);
}

export function parsePlacementDecision(value: unknown): PlacementDecision {
	if (!isRecord(value)) {
		throw new RelayIQClientError("RelayIQ returned a non-object response.", "INVALID_RESPONSE");
	}
	const expected = new Set([
		"decision_id",
		"placement",
		"policy",
		"permitted_payload",
		"requires_consent",
		"fallback",
		"suggested_models",
		"reason_codes",
		"explanation",
	]);
	if (Object.keys(value).some((key) => !expected.has(key)) || Object.keys(value).length !== expected.size) {
		throw new RelayIQClientError("RelayIQ returned an unexpected response shape.", "INVALID_RESPONSE");
	}
	if (
		!isUuid(value.decision_id) ||
		!PLACEMENTS.includes(value.placement as Placement) ||
		typeof value.policy !== "string" ||
		typeof value.permitted_payload !== "string" ||
		typeof value.requires_consent !== "boolean" ||
		value.fallback !== "local" ||
		!isStringArray(value.suggested_models) ||
		!isStringArray(value.reason_codes) ||
		value.reason_codes.length === 0 ||
		!value.reason_codes.every((code) => REASON_CODES.includes(code as ReasonCode)) ||
		typeof value.explanation !== "string"
	) {
		throw new RelayIQClientError("RelayIQ returned invalid decision fields.", "INVALID_RESPONSE");
	}
	if (value.requires_consent !== (value.placement === "cloud_with_consent")) {
		throw new RelayIQClientError("RelayIQ returned an invalid consent invariant.", "INVALID_RESPONSE");
	}
	return value as unknown as PlacementDecision;
}

function errorMessage(value: unknown, status: number): string {
	if (isRecord(value) && typeof value.message === "string") {
		return value.message;
	}
	return `RelayIQ request failed with HTTP ${status}.`;
}

export async function decidePlacement(input: string, options: DecideOptions): Promise<PlacementDecision> {
	const endpoint = normalizeEndpoint(options.endpoint);
	const timeoutSignal = AbortSignal.timeout(options.timeoutMs);
	const signal = options.signal ? AbortSignal.any([options.signal, timeoutSignal]) : timeoutSignal;
	const fetchImpl = options.fetchImpl ?? fetch;
	let response: Response;
	try {
		response = await fetchImpl(`${endpoint}/v1/placement/decide`, {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: JSON.stringify({ input, context: options.context }),
			signal,
			redirect: "error",
		});
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		throw new RelayIQClientError(`RelayIQ transport failed: ${message}`, "TRANSPORT_ERROR");
	}

	let payload: unknown;
	try {
		payload = await response.json();
	} catch {
		throw new RelayIQClientError("RelayIQ returned malformed JSON.", "INVALID_RESPONSE", response.status);
	}
	if (!response.ok) {
		throw new RelayIQClientError(errorMessage(payload, response.status), "SERVICE_ERROR", response.status);
	}
	return parsePlacementDecision(payload);
}
