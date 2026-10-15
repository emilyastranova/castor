import type { Handle, HandleFetch } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';

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

export const handle: Handle = async ({ event, resolve }) => {
	return resolve(event);
};
