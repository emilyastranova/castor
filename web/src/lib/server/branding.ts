/** The company's branding on the server side (hooks.server.ts, the manifest): its name and square
 *  logo, from the public /api/branding, cached briefly. */
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

/** The <head> tags that name and picture the app: the tab's icon, the installed app's. */
export function brandHead(b: Branding | null): string {
	const name = attr(b?.name || 'Castor');
	const icon = b?.mark_url;
	// Apple's home-screen icon has to be a raster image: an SVG logo leaves Castor's there.
	const touch = icon && b?.mark_type && b.mark_type !== 'image/svg+xml' ? icon : '/icons/apple-touch-icon.png';
	return [
		`<link rel="icon" href="${attr(icon || '/favicon.svg')}" />`,
		'<link rel="manifest" href="/manifest.webmanifest" />',
		`<link rel="apple-touch-icon" href="${attr(touch)}" />`,
		`<meta name="application-name" content="${name}" />`,
		`<meta name="apple-mobile-web-app-title" content="${name}" />`
	].join('\n\t\t');
}
