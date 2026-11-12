import type { PageLoad } from './$types';
import { api } from '$lib/api';

export const load: PageLoad = async ({ fetch }) => {
	const [themes, settings] = await Promise.all([api(fetch).themes.list(), api(fetch).settings.get()]);
	return { themes, defaultTheme: settings.default_theme ?? '' };
};
