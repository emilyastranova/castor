import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => ({ groups: await api(fetch).groups.list() });
