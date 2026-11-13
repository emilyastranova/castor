import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch, params }) => {
	const [entry, projects] = await Promise.all([api(fetch).plugins.directoryPlugin(params.id), api(fetch).projects.list()]);
	return { entry, projects };
};
