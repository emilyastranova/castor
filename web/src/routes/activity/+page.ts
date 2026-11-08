import { redirect } from '@sveltejs/kit';

/** The all-projects activity feed moved to Settings → Activity log (a project's own activity
 *  is its Home timeline). */
export const load = () => redirect(308, '/settings/activity');
