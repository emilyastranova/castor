import type { PageLoad } from './$types';
import { api } from '$lib/api';

/** Home: the dashboard across every project the user can see. */
export const load: PageLoad = async ({ fetch }) => {
	const projects = await api(fetch).projects.list();
	return { projects: projects.map((p) => ({ id: p.id, codename: p.codename, name: p.name })) };
};
