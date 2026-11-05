/** The asset types (host, service, url…) with their fields, loaded once. */
import type { AssetType } from '$lib/api';
import { api } from '$lib/api';

class AssetTypes {
	list = $state<AssetType[]>([]);
	private loading: Promise<void> | null = null;

	load(): Promise<void> {
		this.loading ??= api()
			.assets.types()
			.then((t) => {
				this.list = t;
			})
			.catch(() => {
				this.loading = null;
			});
		return this.loading;
	}

	get(name: string): AssetType | undefined {
		return this.list.find((t) => t.name === name);
	}
}

export const assetTypes = new AssetTypes();

/** A field value as text (booleans as yes/no, lists comma separated). */
export function show(value: unknown): string {
	if (value === true) return 'yes';
	if (value === false) return 'no';
	if (Array.isArray(value)) return value.join(', ');
	return value === null || value === undefined ? '' : String(value);
}
