import type { PageLoad } from './$types';
import { api, ApiError } from '$lib/api';
import { error } from '@sveltejs/kit';

export const load: PageLoad = async ({ fetch }) => {
	try {
		return { users: await api(fetch).users.list() };
	} catch (err) {
		if (err instanceof ApiError && err.status === 403) error(403, 'Only admins can manage users');
		throw err;
	}
};
