/** Castor's version, for the account menu and Settings: the server's (from /api/health) and
 *  this web app's (from package.json at build time). They're released together, so they only
 *  differ while one has been upgraded and the other not yet. */
export const WEB_VERSION: string = __CASTOR_WEB_VERSION__;

class Version {
	server = $state<string | null>(null);
	#loading = false;

	/** Load once, on first use. */
	load(): void {
		if (this.server !== null || this.#loading) return;
		this.#loading = true;
		fetch('/api/health')
			.then((r) => (r.ok ? r.json() : null))
			.then((d: { version?: string } | null) => (this.server = d?.version ?? ''))
			.catch(() => (this.server = ''))
			.finally(() => (this.#loading = false));
	}

	/** "Castor 1.2.0", with the web app's version too when it isn't the server's. */
	get label(): string {
		const server = this.server || WEB_VERSION;
		return this.server && this.server !== WEB_VERSION ? `Castor ${server} (web ${WEB_VERSION})` : `Castor ${server}`;
	}

	get mismatch(): boolean {
		return !!this.server && this.server !== WEB_VERSION;
	}
}

export const version = new Version();
