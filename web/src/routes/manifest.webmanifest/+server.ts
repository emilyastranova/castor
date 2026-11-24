import type { RequestHandler } from './$types';

/** The installed app's manifest: always Castor, with Castor's own kanban icons. Company branding
 *  names and pictures the pages (the tab, the rail, the sign-in page), not the app people install. */
const MANIFEST = JSON.stringify({
	name: 'Castor',
	short_name: 'Castor',
	description: 'A Kanban board that runs the work.',
	id: '/',
	start_url: '/',
	scope: '/',
	display: 'standalone',
	background_color: '#131318',
	theme_color: '#545a92',
	icons: [
		{ src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
		{ src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
		{ src: '/icons/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
		{ src: '/favicon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }
	]
});

export const GET: RequestHandler = () =>
	new Response(MANIFEST, { headers: { 'content-type': 'application/manifest+json', 'cache-control': 'public, max-age=300' } });
