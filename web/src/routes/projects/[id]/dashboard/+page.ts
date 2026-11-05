import { error } from '@sveltejs/kit';
import type { PageLoad } from './$types';
import { api, ApiError } from '$lib/api';

export const load: PageLoad = async ({ fetch, params }) => {
	try {
		return { project: await api(fetch).projects.get(params.id) };
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) error(404, 'Project not found');
		throw err;
	}
};
