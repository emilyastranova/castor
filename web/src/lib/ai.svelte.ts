/** Whether AI features are set up (Settings → AI), loaded once and refreshed when an admin
 *  changes the model chains. Buttons that ask the AI show only when it's on. */
import { browser } from '$app/environment';
import { api } from '$lib/api';
import { realtime } from '$lib/realtime.svelte';

class AiStatus {
	enabled = $state(false);
	resolver = $state(false);
	private loading: Promise<void> | null = null;

	constructor() {
		if (!browser) return;
		realtime.subscribe((e) => {
			if (e.name === 'settings.changed') void this.reload();
		});
	}

	load(): Promise<void> {
		this.loading ??= this.reload();
		return this.loading;
	}

	async reload() {
		try {
			const s = await api().ai.status();
			this.enabled = s.enabled;
			this.resolver = s.resolver;
		} catch {
			this.loading = null;
		}
	}
}

export const aiStatus = new AiStatus();
