import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [agents, jobs, tokens] = await Promise.all([client.agents.list(), client.jobs.list(), client.agentTokens.list()]);
	return { agents, jobs, tokens: tokens.tokens, sharedSecretAllowed: tokens.shared_secret_allowed };
};
