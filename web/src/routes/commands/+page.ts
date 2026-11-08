import { redirect } from '@sveltejs/kit';
import type { PageLoad } from './$types';

/** Commands live in the Library now (inside their tool); old links keep working. */
export const load: PageLoad = ({ url }) => {
	const id = url.searchParams.get('open');
	redirect(308, id ? `/library?c=${encodeURIComponent(id)}` : '/library');
};
