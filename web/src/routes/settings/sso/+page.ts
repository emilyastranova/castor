import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [providers, settings] = await Promise.all([client.sso.list(), client.settings.get()]);
	return { providers, settings };
};
