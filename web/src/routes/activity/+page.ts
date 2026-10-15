import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [activity, projects] = await Promise.all([client.activity.recent(100), client.projects.list()]);
	return { activity, projects };
};
