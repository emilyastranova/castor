import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch, params }) => {
	const [publisher, projects] = await Promise.all([api(fetch).plugins.publisher(params.id), api(fetch).projects.list()]);
	return { publisher, projects };
};
