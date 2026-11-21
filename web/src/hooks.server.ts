import type { Handle, HandleFetch } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { brandHead, serverBranding } from '$lib/server/branding';

const API_INTERNAL_URL = (env.API_INTERNAL_URL ?? 'http://localhost:8000').replace(/\/$/, '');

/** Server-side fetches to /api/* go straight to the API, carrying the browser's session cookie. */
export const handleFetch: HandleFetch = async ({ event, request, fetch }) => {
	const url = new URL(request.url);
	if (url.origin === event.url.origin && (url.pathname.startsWith('/api/') || url.pathname === '/api')) {
		const target = API_INTERNAL_URL + url.pathname + url.search;
		const headers = new Headers(request.headers);
		const cookie = event.request.headers.get('cookie');
		if (cookie) headers.set('cookie', cookie);
		request = new Request(target, {
			method: request.method,
			headers,
			body: request.body,
			// @ts-expect-error duplex is required for streaming bodies in Node fetch
			duplex: 'half'
		});
	}
	return fetch(request);
};

/** The instance's default theme (Settings → Themes), cached briefly: app.html shows it on a
 *  browser that has never signed in (the sign-in page), before anything else runs. */
let defaultTheme: { at: number; json: string } | null = null;
async function defaultThemeJson(): Promise<string> {
	if (defaultTheme && Date.now() - defaultTheme.at < 30_000) return defaultTheme.json;
	let json = 'null';
	try {
		const r = await fetch(`${API_INTERNAL_URL}/api/themes/default`, { signal: AbortSignal.timeout(2000) });
		if (r.ok) {
			const t = await r.json();
			// Inside a <script> element: never let the text close it.
			if (t?.id) json = JSON.stringify(t).replace(/</g, '\\u003c');
		}
	} catch {
		/* the API isn't up yet: Castor's own look */
	}
	defaultTheme = { at: Date.now(), json };
	return json;
}

export const handle: Handle = async ({ event, resolve }) => {
	const theme = await defaultThemeJson();
	const head = brandHead(await serverBranding());
	const response = await resolve(event, {
		transformPageChunk: ({ html }) => html.replace('%castor.default_theme%', theme).replace('%castor.brand_head%', head)
	});
	// The page lists the same preloads as tags (svelte.config.js preloadStrategy). The header,
	// one entry per module, outgrows the 4 KB of response headers nginx takes by default.
	if (!response.headers.has('link')) return response;
	try {
		response.headers.delete('link');
		return response;
	} catch {
		// immutable headers (a response made by fetch): copy it without the header
		const copy = new Response(response.body, response);
		copy.headers.delete('link');
		return copy;
	}
};
