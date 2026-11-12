import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const [plugins, projects, mode] = await Promise.all([api(fetch).plugins.list(), api(fetch).projects.list(), api(fetch).plugins.mode()]);
	return { plugins, projects, mode };
};
