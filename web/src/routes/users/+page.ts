import { redirect } from '@sveltejs/kit';
import type { PageLoad } from './$types';

// Users moved under Settings; old links and bookmarks still work.
export const load: PageLoad = () => redirect(308, '/settings/users');
