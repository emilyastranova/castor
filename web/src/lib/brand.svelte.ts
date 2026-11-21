/** The company's branding (Settings → Branding): its name in page titles and the navigation rail,
 *  its logos on the sign-in page and the rail. Loaded with every page (routes/+layout.server.ts,
 *  public, so the sign-in page has it too) and kept current by `branding.changed`. */
import type { Branding } from '$lib/api';

class Brand {
	info = $state<Branding | null>(null);

	/** The name to show: the company's, or Castor. */
	get name(): string {
		return this.info?.name || 'Castor';
	}
	/** Something of the company's is set (a name or a logo). */
	get branded(): boolean {
		const b = this.info;
		return !!b && !!(b.name || b.mark_url || b.wordmark_url);
	}
	/** A page's title: "Board · Acme Red Team". */
	title(what?: string): string {
		return what ? `${what} · ${this.name}` : this.name;
	}
}

export const brand = new Brand();
