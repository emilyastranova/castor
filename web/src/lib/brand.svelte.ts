/** The company's branding (Settings → Branding): its name in page titles and the navigation rail,
 *  its logos on the sign-in page and the rail. Loaded with every page (routes/+layout.server.ts,
 *  public, so the sign-in page has it too) and kept current by `branding.changed`. */
import type { Branding } from '$lib/api';
import { browser } from '$app/environment';

// Castor installed as an app (Chrome's Install, a home-screen icon) is always Castor, whatever the branding.
const INSTALLED = '(display-mode: standalone), (display-mode: window-controls-overlay), (display-mode: minimal-ui), (display-mode: fullscreen)';

class Brand {
	info = $state<Branding | null>(null);
	/** Running as the installed app. */
	installed = $state(false);

	constructor() {
		if (!browser) return;
		const q = window.matchMedia(INSTALLED);
		this.installed = q.matches;
		q.addEventListener('change', () => (this.installed = q.matches));
	}

	/** The name to show: the company's, or Castor. */
	get name(): string {
		return this.info?.name || 'Castor';
	}
	/** Something of the company's is set (a name or a logo). */
	get branded(): boolean {
		const b = this.info;
		return !!b && !!(b.name || b.mark_url || b.wordmark_url);
	}
	/** The name at the end of page titles: Castor in the installed app (its window title), else `name`. */
	get windowName(): string {
		return this.installed ? 'Castor' : this.name;
	}
	/** A page's title: "Board · Acme Red Team". */
	title(what?: string): string {
		return what ? `${what} · ${this.windowName}` : this.windowName;
	}
}

export const brand = new Brand();
