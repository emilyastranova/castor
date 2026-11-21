import { redirect } from '@sveltejs/kit';
import type { LayoutServerLoad } from './$types';
import { api, ApiError } from '$lib/api';

export const load: LayoutServerLoad = async ({ fetch, url, route, untrack }) => {
	// The company's name and logos (public): every page, the sign-in page too (lib/brand.svelte.ts).
	const branding = await api(fetch).branding.get().catch(() => null);
	// Use route.id (not url) so this load does not re-run on every query-string change.
	if (route.id === '/login') return { user: null, branding };
	try {
		const user = await api(fetch).auth.me();
		return { user, branding };
	} catch (err) {
		if (err instanceof ApiError && err.status === 401) {
			const next = untrack(() => url.pathname + url.search);
			redirect(303, `/login?next=${encodeURIComponent(next)}`);
		}
		throw err;
	}
};
