import { api, type CustomEmoji } from './api';
import { realtime } from './realtime.svelte';

/** The instance's custom emoji (Settings → Emoji), loaded once and kept current. */
class CustomEmojis {
	list = $state<CustomEmoji[]>([]);
	/** The list has arrived (a name it lacks is one that was removed). */
	loaded = $state(false);
	#loaded = false;
	#unsubscribe: (() => void) | null = null;

	load(): void {
		if (this.#loaded) return;
		this.#loaded = true;
		void this.refresh();
		this.#unsubscribe ??= realtime.subscribe((e) => {
			if (e.name === 'emojis.changed') void this.refresh();
		});
	}

	async refresh(): Promise<void> {
		try {
			this.list = await api().emojis.list();
		} catch {
			/* signed out, or no access: none */
		}
		this.loaded = true;
	}

	url(emoji: string): string | null {
		const m = /^:([a-z0-9_]+):$/.exec(emoji);
		return m ? (this.list.find((e) => e.name === m[1])?.url ?? null) : null;
	}
}

export const customEmojis = new CustomEmojis();
