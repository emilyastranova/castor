import type { PageLoad } from './$types';
import { api, ApiError } from '$lib/api';
import { error } from '@sveltejs/kit';

/** /playbooks/edit/<id>, /playbooks/edit/new, or /playbooks/edit/new?from=<id> (a copy). */
export const load: PageLoad = async ({ fetch, params, url }) => {
	const client = api(fetch);
	const source = params.id === 'new' ? url.searchParams.get('from') : params.id;
	const [commands, kinds, playbook] = await Promise.all([
		client.commands.list(),
		client.variables.kinds(),
		source
			? client.playbooks.get(source).catch((err) => {
					if (err instanceof ApiError && err.status === 404) error(404, 'Playbook not found');
					throw err;
				})
			: Promise.resolve(null)
	]);
	return { commands, kinds, playbook, isNew: params.id === 'new' || playbook?.origin === 'builtin' };
};
