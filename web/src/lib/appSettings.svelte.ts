/** Instance-wide settings (Settings → General), loaded once and kept fresh from realtime. */
import { browser } from '$app/environment';
import type { AppSettings } from '$lib/api';
import { api } from '$lib/api';
import { realtime } from '$lib/realtime.svelte';

const DEFAULTS: AppSettings = { notes_per_project: 4, ai_chains: {} };

class AppSettingsStore {
	value = $state<AppSettings>({ ...DEFAULTS });
	private loading: Promise<void> | null = null;

	constructor() {
		if (!browser) return;
		realtime.subscribe((e) => {
			if (e.name === 'settings.changed') this.value = { ...DEFAULTS, ...(e.data as AppSettings) };
		});
	}

	load(): Promise<void> {
		this.loading ??= api()
			.settings.get()
			.then((s) => {
				this.value = { ...DEFAULTS, ...s };
			})
			.catch(() => {
				this.loading = null;
			});
		return this.loading;
	}
}

export const appSettings = new AppSettingsStore();
