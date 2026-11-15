import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => ({ connections: await api(fetch).mcp.list() });
