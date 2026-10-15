import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [agents, jobs] = await Promise.all([client.agents.list(), client.jobs.list()]);
	return { agents, jobs };
};
