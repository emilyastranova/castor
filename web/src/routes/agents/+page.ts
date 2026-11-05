import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const client = api(fetch);
	const [agents, jobs, tokens] = await Promise.all([
		client.agents.list(),
		client.jobs.list(),
		// Tokens are for admins; everyone else just doesn't see them.
		client.agentTokens.list().catch(() => ({ tokens: [], shared_secret_allowed: true }))
	]);
	return { agents, jobs, tokens: tokens.tokens, sharedSecretAllowed: tokens.shared_secret_allowed };
};
