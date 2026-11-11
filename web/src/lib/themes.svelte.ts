/** The themes people can pick: Castor's own plus whatever the server has (built-ins from
 *  themes/*.yaml and custom ones from Settings → Themes). Loaded once a person is signed in,
 *  and again when an admin changes one (`themes.changed`). */
import { api, type Theme } from '$lib/api';
import { CASTOR } from '$lib/m3/themes';

class Themes {
	list = $state<Theme[]>([CASTOR]);
	loaded = $state(false);
	#loading: Promise<void> | null = null;

	load(force = false): Promise<void> {
		if (this.#loading && !force) return this.#loading;
		this.#loading = api()
			.themes.list()
			.then((rows) => {
				this.list = [CASTOR, ...rows];
				this.loaded = true;
			})
			.catch(() => {
				this.#loading = null;
			});
		return this.#loading;
	}

	/** The theme with this id; Castor's own for '' (and, once loaded, for one that's gone). */
	byId(id: string | null | undefined): Theme | undefined {
		if (!id) return CASTOR;
		return this.list.find((t) => t.id === id) ?? (this.loaded ? CASTOR : undefined);
	}
}

export const themes = new Themes();
