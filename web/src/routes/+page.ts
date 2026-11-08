import { redirect } from '@sveltejs/kit';
import type { PageLoad } from './$types';
import { api } from '$lib/api';
import { currentProject } from '$lib/currentProject.svelte';

// Which project is current lives in this browser, so this only runs there.
export const ssr = false;

/** Home is the current project's dashboard; with none chosen (or one you can no longer see),
 *  your only project, or else the project picker. */
export const load: PageLoad = async ({ fetch, parent }) => {
	const { user } = await parent();
	if (user) currentProject.init(user.id);
	const projects = await api(fetch).projects.list();
	let id = currentProject.id && projects.some((p) => p.id === currentProject.id) ? currentProject.id : null;
	if (!id && projects.length === 1) id = projects[0].id;
	currentProject.set(id);
	redirect(307, id ? `/projects/${id}/dashboard` : '/projects');
};
