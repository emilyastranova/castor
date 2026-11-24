/** The company's branding on the server side (hooks.server.ts): its square logo for the tab,
 *  from the public /api/branding, cached briefly. */
import { env } from '$env/dynamic/private';
import type { Branding } from '$lib/api';

const API_INTERNAL_URL = (env.API_INTERNAL_URL ?? 'http://localhost:8000').replace(/\/$/, '');
let cached: { until: number; value: Branding | null } | null = null;

export async function serverBranding(): Promise<Branding | null> {
	if (cached && Date.now() < cached.until) return cached.value;
	let value: Branding | null = null;
	let ok = false;
	try {
		const r = await fetch(`${API_INTERNAL_URL}/api/branding`, { signal: AbortSignal.timeout(2000) });
		if (r.ok) {
			value = (await r.json()) as Branding;
			ok = true;
		}
	} catch {
		/* the API isn't up yet: Castor's own for now */
	}
	// An answer is kept for 30 s; no answer (the API still starting) is asked again soon.
	cached = { until: Date.now() + (ok ? 30_000 : 2_000), value };
	return value;
}

const attr = (s: string) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** The <head> tags that picture the app: the tab's icon follows the company's branding; the installed
 *  app (manifest, home-screen icon, app name) is always Castor with its kanban icon. */
export function brandHead(b: Branding | null): string {
	return [
		`<link rel="icon" href="${attr(b?.mark_url || '/favicon.svg')}" />`,
		'<link rel="manifest" href="/manifest.webmanifest" />',
		'<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png" />',
		'<meta name="application-name" content="Castor" />',
		'<meta name="apple-mobile-web-app-title" content="Castor" />'
	].join('\n\t\t');
}
