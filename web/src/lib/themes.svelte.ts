/** The themes people can pick: Castor's own plus whatever the server has (built-ins from
 *  themes/*.yaml and custom ones from Settings → Themes), and the instance default (for people
 *  who never picked one). Loaded once a person is signed in, and again when an admin changes
 *  one or the default (`themes.changed`). */
import { api, type Theme } from '$lib/api';
import { CASTOR } from '$lib/m3/themes';

class Themes {
	list = $state<Theme[]>([CASTOR]);
	loaded = $state(false);
	/** The instance default's id ("" for Castor's own). */
	defaultId = $state('');
	#loading: Promise<void> | null = null;

	load(force = false): Promise<void> {
		if (this.#loading && !force) return this.#loading;
		this.#loading = Promise.all([api().themes.list(), api().themes.default()])
			.then(([rows, dflt]) => {
				this.list = [CASTOR, ...rows];
				this.defaultId = dflt.id;
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

	/** Someone's theme id: theirs, or the instance default if they never picked one (null). */
	effectiveId(theme: string | null | undefined): string {
		return theme ?? this.defaultId;
	}

	/** Someone's theme: theirs, or the default (undefined until loaded, for a theme not yet known). */
	effective(theme: string | null | undefined): Theme | undefined {
		if (theme === null || theme === undefined) return this.loaded ? this.byId(this.defaultId) : undefined;
		return this.byId(theme);
	}
}

export const themes = new Themes();
