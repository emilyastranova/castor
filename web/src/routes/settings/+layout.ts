import { error } from '@sveltejs/kit';
import type { LayoutLoad } from './$types';

/** Settings are for admins. */
export const load: LayoutLoad = async ({ parent }) => {
	const { user } = await parent();
	if (user?.role !== 'admin') error(403, 'Settings are for admins');
	return {};
};
