/** Saved lists (variable sets with a list field, e.g. target lists) shared by every list
 *  picker on the page, fetched once and refreshed when a list is added. */
import type { VariableKind, VariableSet } from '$lib/api';
import { api } from '$lib/api';

export interface SavedList {
	set: VariableSet;
	kind: VariableKind;
	/** The field a reference reads: `entries`, or the kind's first list field. */
	field: string;
	entries: string[];
}

class Lists {
	all = $state<SavedList[]>([]);
	loaded = $state(false);
	#loading: Promise<void> | null = null;

	load(force = false): Promise<void> {
		if (this.#loading && !force) return this.#loading;
		this.#loading = Promise.all([api().variables.sets(), api().variables.kinds()]).then(([sets, kinds]) => {
			const out: SavedList[] = [];
			for (const set of sets) {
				const kind = kinds.find((k) => k.id === set.kind_id);
				const listFields = kind?.fields.filter((f) => f.type === 'list') ?? [];
				// Choice lists feed dropdowns; they aren't targets.
				if (!kind || !listFields.length || kind.id === 'choice-list') continue;
				const field = (listFields.find((f) => f.name === 'entries') ?? listFields[0]).name;
				const value = set.values[field];
				out.push({ set, kind, field, entries: Array.isArray(value) ? value.map(String) : [] });
			}
			this.all = out.sort((a, b) => a.set.name.localeCompare(b.set.name));
			this.loaded = true;
		});
		return this.#loading;
	}

	find(ref: string): SavedList | undefined {
		return this.all.find((l) => l.set.id === ref || l.set.name === ref);
	}

	/** Lists a project may use (sets limited to other projects are left out). */
	forProject(projectId?: string | null): SavedList[] {
		return this.all.filter((l) => !l.set.project_ids.length || (projectId && l.set.project_ids.includes(projectId)));
	}
}

export const lists = new Lists();

/** {"list": id} in a command argument: the card links a saved list. */
export type ListRef = { list: string };
export const isListRef = (v: unknown): v is ListRef =>
	!!v && typeof v === 'object' && !Array.isArray(v) && typeof (v as ListRef).list === 'string';
