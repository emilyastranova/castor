import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [kinds, sets, projects] = await Promise.all([
		client.variables.kinds(),
		client.variables.sets(),
		client.projects.list()
	]);
	return { kinds, sets, projects };
};
