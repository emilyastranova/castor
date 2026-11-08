import { error } from '@sveltejs/kit';
import type { LayoutLoad } from './$types';
import { api, ApiError } from '$lib/api';

/** The project every page under it is about (Home, Board, Assets). */
export const load: LayoutLoad = async ({ fetch, params }) => {
	try {
		return { project: await api(fetch).projects.get(params.id) };
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) error(404, 'Project not found');
		throw err;
	}
};
