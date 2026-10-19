import assert from "node:assert/strict";
import test from "node:test";
import { RELAYIQ_STATE_TYPE, restoreRelayIQState } from "../extensions/relayiQ/state.ts";

test("restores enablement and decision metadata without payload data", () => {
	const result = restoreRelayIQState([
		{
			type: "custom",
			customType: RELAYIQ_STATE_TYPE,
			data: { kind: "enabled", enabled: false, timestamp: 1 },
		},
		{
			type: "custom",
			customType: RELAYIQ_STATE_TYPE,
			data: {
				kind: "decision",
				decisionId: "id",
				placement: "local",
				policy: "Local First v1",
				reasonCodes: ["LOCAL_CAPABILITY_SUFFICIENT"],
				consent: "not_required",
				timestamp: 2,
			},
		},
	]);
	assert.equal(result.enabled, false);
	assert.equal(result.lastAudit?.kind, "decision");
	assert.equal(JSON.stringify(result).includes("payload"), false);
});

test("sanitizes restored error audit entries", () => {
	const result = restoreRelayIQState([
		{
			type: "custom",
			customType: RELAYIQ_STATE_TYPE,
			data: {
				kind: "error",
				code: "SERVICE_ERROR_503",
				message: "remote content must not be restored",
				timestamp: 1,
			},
		},
	]);
	assert.deepEqual(result.lastAudit, {
		kind: "error",
		code: "SERVICE_ERROR_503",
		timestamp: 1,
	});
});
