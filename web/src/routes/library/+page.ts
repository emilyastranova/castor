import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [commands, tools] = await Promise.all([client.commands.list(), client.tools.list()]);
	return { commands, tools };
};
