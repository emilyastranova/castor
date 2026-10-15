import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => ({ parsers: await api(fetch).parsers.list() });
