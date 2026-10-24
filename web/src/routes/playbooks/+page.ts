import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const [playbooks, runs, projects, commands] = await Promise.all([
		api(fetch).playbooks.list(),
		api(fetch).playbookRuns.list({ limit: 50 }),
		api(fetch).projects.list(),
		api(fetch).commands.list()
	]);

	return {
		playbooks,
		runs,
		projects,
		commands
	};
};
