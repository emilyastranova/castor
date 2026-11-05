/** The global search palette (Ctrl+K / Cmd+K, or the search button in the rail). */
import type { SearchHit } from '$lib/api';

const RECENT_KEY = 'castor.search.recent';

class PaletteStore {
	open = $state(false);
	/** Text to start with (e.g. "in:NWL26-001 " from a project page). */
	initial = $state('');

	show(text = '') {
		this.initial = text;
		this.open = true;
	}

	hide() {
		this.open = false;
	}

	/** Things opened from search, newest first: shown when nothing is typed. */
	recent(): SearchHit[] {
		try {
			return (JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]') as SearchHit[]).slice(0, 8);
		} catch {
			return [];
		}
	}

	remember(hit: SearchHit) {
		try {
			const rest = this.recent().filter((h) => h.link !== hit.link);
			localStorage.setItem(RECENT_KEY, JSON.stringify([{ ...hit, at: null }, ...rest].slice(0, 8)));
		} catch {
			/* storage unavailable */
		}
	}
}

export const palette = new PaletteStore();
