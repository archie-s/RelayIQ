import assert from "node:assert/strict";
import test from "node:test";
import { buildModelInventory, resolveSuggestedModel } from "../extensions/relayiQ/models.ts";

const models = [
	{ provider: "local", id: "Phi-4" },
	{ provider: "cloud", id: "GPT-5" },
	{ provider: "cloud", id: "gpt-5" },
] as any[];

test("uses explicit model lanes and preserves configured order", () => {
	const inventory = buildModelInventory(
		models,
		["local/Phi-4"],
		["cloud/gpt-5", "cloud/GPT-5", "cloud/gpt-5"],
	);
	assert.deepEqual(inventory.localIds, ["local/Phi-4"]);
	assert.deepEqual(inventory.cloudIds, ["cloud/gpt-5", "cloud/GPT-5", "cloud/gpt-5"]);
	assert.equal(resolveSuggestedModel(["cloud/GPT-5"], inventory.cloud)?.id, "GPT-5");
	assert.equal(resolveSuggestedModel(["cloud/Gpt-5"], inventory.cloud), undefined);
});

test("does not infer unavailable or unconfigured models", () => {
	const inventory = buildModelInventory(models, ["unknown/model"], ["cloud/GPT-5"]);
	assert.deepEqual(inventory.localIds, []);
	assert.equal(resolveSuggestedModel(["local/Phi-4"], inventory.cloud), undefined);
});
