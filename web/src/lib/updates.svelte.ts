/**
 * Telling people a newer Castor is running than the page they have open.
 *
 * Two things can change under an open tab: the web app (a new build: SvelteKit's `updated`, which
 * compares the build this page came from with `/_app/version.json`) and the server (its version
 * from `/api/health`). Either is checked when the tab comes back into view, when the live connection
 * comes back after dropping (an upgrade restarts the server) and every CHECK_EVERY. When one
 * changed, `available` is set: `UpdateBanner` asks people to refresh, and the root layout makes the
 * next move to another page a full load (so it picks up the new version by itself).
 */
import { browser } from '$app/environment';
import { updated } from '$app/state';

const CHECK_EVERY = 10 * 60_000;
const MIN_GAP = 30_000; // between checks on focus
const LATER = 60 * 60_000; // "Later" hides it this long

class Updates {
	/** The server's version when the page loaded, and the one it reports now if that differs. */
	#first: string | null = null;
	serverNow = $state<string | null>(null);
	webChanged = $state(false);
	/** Hidden with Later (for LATER). */
	hidden = $state(false);
	#last = 0;
	#timer: ReturnType<typeof setInterval> | null = null;

	get serverChanged(): boolean {
		return !!this.#first && !!this.serverNow && this.serverNow !== this.#first;
	}

	/** A newer Castor is running than this page. */
	get available(): boolean {
		return this.webChanged || this.serverChanged;
	}

	/** Show the banner (not hidden with Later). */
	get show(): boolean {
		return this.available && !this.hidden;
	}

	start(): () => void {
		if (!browser) return () => {};
		void this.check(true);
		const onVisible = () => {
			if (document.visibilityState === 'visible' && Date.now() - this.#last > MIN_GAP) void this.check();
		};
		document.addEventListener('visibilitychange', onVisible);
		window.addEventListener('focus', onVisible);
		this.#timer = setInterval(() => void this.check(), CHECK_EVERY);
		return () => {
			document.removeEventListener('visibilitychange', onVisible);
			window.removeEventListener('focus', onVisible);
			if (this.#timer) clearInterval(this.#timer);
		};
	}

	async check(first = false): Promise<void> {
		this.#last = Date.now();
		try {
			const res = await fetch('/api/health', { cache: 'no-store' });
			const v = res.ok ? ((await res.json()) as { version?: string }).version : undefined;
			if (v) {
				if (first || !this.#first) this.#first = v;
				this.serverNow = v;
			}
		} catch {
			/* the server is away (restarting?): the reconnect will check again */
		}
		if (first) return;
		try {
			this.webChanged = await updated.check();
		} catch {
			/* no version.json (the dev server): nothing to compare */
		}
	}

	later(): void {
		this.hidden = true;
		setTimeout(() => (this.hidden = false), LATER);
	}
}

export const updates = new Updates();
