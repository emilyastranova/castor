import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => ({ emojis: await api(fetch).emojis.list() });
