import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [kinds, providers, settings, usage] = await Promise.all([client.ai.kinds(), client.ai.providers(), client.settings.get(), client.ai.usage()]);
	return { kinds, providers, settings, usage };
};
