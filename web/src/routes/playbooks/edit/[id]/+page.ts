import { readOnlyOrigin } from '$lib/origin';
import type { PageLoad } from './$types';
import { api, ApiError } from '$lib/api';
import { error } from '@sveltejs/kit';
import { browser } from '$app/environment';
import type { PlaybookSpec } from '$lib/api';

/** A draft handed over in this tab (an activity's subtasks saved as a playbook, SubtaskPanel): ?draft=1. */
const DRAFT_KEY = 'castor.playbook-draft';

/** /playbooks/edit/<id>, /playbooks/edit/new, or /playbooks/edit/new?from=<id> (a copy). */
export const load: PageLoad = async ({ fetch, params, url }) => {
	const client = api(fetch);
	const source = params.id === 'new' ? url.searchParams.get('from') : params.id;
	const [commands, tools, kinds, playbook] = await Promise.all([
		client.commands.list(),
		client.tools.list(),
		client.variables.kinds(),
		source
			? client.playbooks.get(source).catch((err) => {
					if (err instanceof ApiError && err.status === 404) error(404, 'Playbook not found');
					throw err;
				})
			: Promise.resolve(null)
	]);
	let draft: PlaybookSpec | null = null;
	if (browser && params.id === 'new' && url.searchParams.get('draft')) {
		try {
			draft = JSON.parse(sessionStorage.getItem(DRAFT_KEY) ?? 'null');
		} catch {
			draft = null;
		}
	}
	return { commands, tools, kinds, playbook, draft, isNew: params.id === 'new' || readOnlyOrigin(playbook?.origin) };
};
