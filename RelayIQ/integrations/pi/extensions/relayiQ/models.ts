import type { Model } from "@earendil-works/pi-ai";

export type PiModel = Model<any>;

export function canonicalModelId(model: Pick<PiModel, "provider" | "id">): string {
	return `${model.provider}/${model.id}`;
}

export interface ModelInventory {
	available: PiModel[];
	local: PiModel[];
	cloud: PiModel[];
	localIds: string[];
	cloudIds: string[];
}

export function buildModelInventory(
	available: readonly PiModel[],
	localConfig: readonly string[],
	cloudConfig: readonly string[],
): ModelInventory {
	const byId = new Map(available.map((model) => [canonicalModelId(model), model]));
	const local = localConfig.flatMap((id) => {
		const model = byId.get(id);
		return model ? [model] : [];
	});
	const cloud = cloudConfig.flatMap((id) => {
		const model = byId.get(id);
		return model ? [model] : [];
	});
	return {
		available: [...available],
		local,
		cloud,
		localIds: local.map(canonicalModelId),
		cloudIds: cloud.map(canonicalModelId),
	};
}

export function resolveSuggestedModel(
	suggestions: readonly string[],
	candidates: readonly PiModel[],
): PiModel | undefined {
	const byId = new Map(candidates.map((model) => [canonicalModelId(model), model]));
	for (const suggestion of suggestions) {
		const model = byId.get(suggestion);
		if (model) return model;
	}
	return undefined;
}
