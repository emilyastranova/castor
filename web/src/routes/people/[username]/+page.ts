import { error } from '@sveltejs/kit';
import type { PageLoad } from './$types';
import { api, ApiError } from '$lib/api';

export const load: PageLoad = async ({ fetch, params }) => {
	try {
		return { person: await api(fetch).people.get(params.username) };
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) error(404, 'No such person');
		throw err;
	}
};
