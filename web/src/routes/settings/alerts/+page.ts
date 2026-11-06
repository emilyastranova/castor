import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [presets, channels] = await Promise.all([client.alerts.presets(), client.alerts.channels()]);
	return { presets, channels };
};
