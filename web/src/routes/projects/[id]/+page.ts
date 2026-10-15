import { error } from '@sveltejs/kit';
import type { PageLoad } from './$types';
import { api, ApiError } from '$lib/api';

export const load: PageLoad = async ({ fetch, params }) => {
	const client = api(fetch);
	try {
		const [board, activity, agents] = await Promise.all([
			client.projects.board(params.id),
			client.projects.activity(params.id, 50),
			client.agents.list()
		]);
		return { board, activity, agents };
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) error(404, 'Project not found');
		throw err;
	}
};
