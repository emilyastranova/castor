/** Unrestricted plugins' scripts (manifest `ui` slot `page.script`, docs/PLUGINS.md phase D): they
 *  run in Castor's own page, as whoever is looking, which is why only unrestricted plugins (an
 *  admin's explicit choice, outside restricted mode) can have one. Loaded once per page load.
 *
 *  They get `window.castorPage`:
 *    castorPage.user                    // { id, username, display_name, role }
 *    castorPage.notify("text")          // a snackbar
 *    castorPage.goto("/projects/…")     // a page of Castor (same-origin paths only)
 *    castorPage.onNavigate((url) => …)  // after every navigation, and once now
 *    castorPage.onEvent((e) => …)       // Castor's live events ({ name, data, project_id })
 *  and can call Castor's API with fetch (the person's session). */
import { goto } from '$app/navigation';
import type { User } from '$lib/api';
import { api } from '$lib/api';
import { realtime } from '$lib/realtime.svelte';
import { snackbar } from '$lib/snackbar.svelte';

type NavListener = (url: URL) => void;
const navListeners: NavListener[] = [];
let lastUrl: URL | null = null;
let loadedFor: string | null = null;

/** Called by the root layout after each navigation. */
export function navigated(url: URL) {
	lastUrl = url;
	for (const fn of navListeners) {
		try {
			fn(url);
		} catch (err) {
			console.error('A plugin script failed on navigation', err);
		}
	}
}

export async function loadPluginScripts(user: User) {
	if (loadedFor === user.id || typeof window === 'undefined') return;
	loadedFor = user.id;
	let scripts: Awaited<ReturnType<ReturnType<typeof api>['plugins']['scripts']>>;
	try {
		scripts = await api().plugins.scripts();
	} catch {
		return;
	}
	if (!scripts.length) return;
	(window as unknown as { castorPage: unknown }).castorPage = {
		version: 1,
		user: { id: user.id, username: user.username, display_name: user.display_name, role: user.role },
		notify: (text: unknown) => snackbar.show(String(text).slice(0, 300)),
		goto: (path: unknown) => {
			const p = String(path);
			if (p.startsWith('/') && !p.startsWith('//')) void goto(p);
		},
		onNavigate: (fn: NavListener) => {
			navListeners.push(fn);
			if (lastUrl) fn(lastUrl);
		},
		onEvent: (fn: (e: unknown) => void) => realtime.subscribe((e) => fn(e))
	};
	for (const s of scripts) {
		const el = document.createElement('script');
		el.type = 'module';
		el.src = s.url;
		el.dataset.castorPlugin = s.plugin.id;
		el.onerror = () => console.error(`The script of the plugin ${s.plugin.name} didn't load`);
		document.head.append(el);
	}
}
