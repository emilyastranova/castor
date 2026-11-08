/**
 * The project everything project-shaped is about: Home (its dashboard), Board and Assets in
 * the navigation. Remembered per person in this browser, so you pick it once. Opening any
 * project page makes that project current (links from notifications, search and the MCP keep
 * working); routes/projects/[id]/+layout.svelte does that and says so when it's a switch.
 */
class CurrentProject {
	id = $state<string | null>(null);
	#key: string | null = null;

	/** Read this person's remembered project, once per signed-in user (safe to call often). */
	init(userId: string): void {
		const key = `castor.project.${userId}`;
		if (this.#key === key) return;
		this.#key = key;
		try {
			this.id = localStorage.getItem(this.#key);
		} catch {
			this.id = null;
		}
	}

	set(id: string | null): void {
		this.id = id;
		if (!this.#key) return;
		try {
			if (id) localStorage.setItem(this.#key, id);
			else localStorage.removeItem(this.#key);
		} catch {
			/* storage unavailable: it lasts for this tab only */
		}
	}

	/** Where Home, Board and Assets go (the project picker when nothing is chosen yet). */
	home = $derived(this.id ? `/projects/${this.id}/dashboard` : '/projects');
	board = $derived(this.id ? `/projects/${this.id}` : '/projects');
	assets = $derived(this.id ? `/projects/${this.id}/assets` : '/projects');
}

export const currentProject = new CurrentProject();
