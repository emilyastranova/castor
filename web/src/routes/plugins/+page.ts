import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => ({ plugins: await api(fetch).plugins.mine() });
