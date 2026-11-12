import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const [plugins, projects] = await Promise.all([api(fetch).plugins.list(), api(fetch).projects.list()]);
	return { plugins, projects };
};
