import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [routes, agents, projects, sshHosts] = await Promise.all([
		client.routes.list(),
		client.agents.list(),
		client.projects.list(),
		// SSH hosts are for admins (to make a route from one); everyone else doesn't see them.
		client.sshHosts.list().catch(() => [])
	]);
	return { routes, agents, projects, sshHosts };
};
