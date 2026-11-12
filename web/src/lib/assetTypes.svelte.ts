/** The asset types (host, service, url…) with their fields, and the kinds of relationship
 *  between them, loaded once (and again when plugins, which can add both, change). */
import type { AssetLinkKind, AssetType } from '$lib/api';
import { api } from '$lib/api';
import { realtime } from '$lib/realtime.svelte';

class AssetTypes {
	list = $state<AssetType[]>([]);
	kinds = $state<AssetLinkKind[]>([]);
	private loading: Promise<void> | null = null;

	private watching = false;

	load(): Promise<void> {
		if (!this.watching && typeof window !== 'undefined') {
			this.watching = true;
			realtime.subscribe((e) => {
				if (e.name !== 'plugins.changed' || !this.loading) return;
				this.loading = null;
				void this.load();
			});
		}
		this.loading ??= Promise.all([api().assets.types(), api().assets.linkKinds()])
			.then(([t, k]) => {
				this.list = t;
				this.kinds = k;
			})
			.catch(() => {
				this.loading = null;
			});
		return this.loading;
	}

	get(name: string): AssetType | undefined {
		return this.list.find((t) => t.name === name);
	}

	kind(name: string): AssetLinkKind | undefined {
		return this.kinds.find((k) => k.name === name);
	}

	/** The relationships an asset of this type can have, read from it: each kind forwards (it is
	 *  the "from" end) and backwards, with the types at the other end ([] for any). */
	relationsOf(type: string): Relation[] {
		const out: Relation[] = [];
		for (const k of this.kinds) {
			if (!k.pairs.length) {
				out.push({ kind: k.name, direction: 'out', label: k.forward, others: [] });
				continue;
			}
			const to = k.pairs.filter(([a]) => a === type).map(([, b]) => b);
			if (to.length) out.push({ kind: k.name, direction: 'out', label: k.forward, others: to });
			const from = k.pairs.filter(([, b]) => b === type).map(([a]) => a);
			if (from.length) out.push({ kind: k.name, direction: 'in', label: k.reverse, others: from });
		}
		return out;
	}
}

export interface Relation {
	kind: string;
	direction: 'out' | 'in';
	label: string;
	/** The types the other asset can be; empty: any. */
	others: string[];
}

export const assetTypes = new AssetTypes();

/** A field value as text (booleans as yes/no, lists comma separated). */
export function show(value: unknown): string {
	if (value === true) return 'yes';
	if (value === false) return 'no';
	if (Array.isArray(value)) return value.join(', ');
	return value === null || value === undefined ? '' : String(value);
}

/** A relationship's details in a few words, protocol first: "smb · port 445 · admin". */
export function linkDetails(fields: Record<string, unknown>): string {
	const order = (k: string) => (k === 'protocol' ? 0 : k === 'port' ? 1 : 2);
	return Object.entries(fields)
		.filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== false)
		.sort(([a], [b]) => order(a) - order(b))
		.map(([k, v]) => (v === true ? k : k === 'port' ? `port ${v}` : k === 'protocol' ? String(v) : `${k} ${show(v)}`))
		.join(' · ');
}
