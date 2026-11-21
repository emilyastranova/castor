import type { RequestHandler } from './$types';
import { serverBranding } from '$lib/server/branding';

/** The installed app's manifest, named and pictured by the company's branding (Castor's own without). */
export const GET: RequestHandler = async () => {
	const b = await serverBranding();
	const name = b?.name || 'Castor';
	const icons = b?.mark_url
		? [{ src: b.mark_url, sizes: b.mark_type === 'image/svg+xml' ? 'any' : '512x512', type: b.mark_type ?? 'image/png', purpose: 'any' }]
		: [
				{ src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
				{ src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
				{ src: '/icons/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
				{ src: '/favicon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }
			];
	const manifest = {
		name,
		short_name: name.length > 12 ? name.split(/\s+/)[0] : name,
		description: b?.tagline || 'A Kanban board that runs the work.',
		id: '/',
		start_url: '/',
		scope: '/',
		display: 'standalone',
		background_color: '#131318',
		theme_color: '#545a92',
		icons
	};
	return new Response(JSON.stringify(manifest), {
		headers: { 'content-type': 'application/manifest+json', 'cache-control': 'public, max-age=300' }
	});
};
