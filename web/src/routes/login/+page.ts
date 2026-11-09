import type { PageLoad } from './$types';
import { api } from '$lib/api';

/** The sign-on providers to offer, and whether the password form is on. */
export const load: PageLoad = async ({ fetch }) => {
	try {
		return await api(fetch).auth.signInOptions();
	} catch {
		return { providers: [], password_login: true };
	}
};
