import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => ({ tokens: await api(fetch).apiTokens.list(true) });
