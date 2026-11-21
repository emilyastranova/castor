import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [settings, branding, clients] = await Promise.all([client.settings.get(), client.branding.get(), client.clients.list()]);
	return { settings, branding, clients };
};
