import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [projects, clients, agents] = await Promise.all([
		client.projects.list(),
		client.clients.list(),
		client.agents.list()
	]);
	return { projects, clients, agents };
};
