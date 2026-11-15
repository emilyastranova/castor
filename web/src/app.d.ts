import type { User } from '$lib/api';

declare global {
	/** web/package.json's version, set by vite.config.ts. */
	const __CASTOR_WEB_VERSION__: string;
	namespace App {
		interface Locals {
			user: User | null;
		}
		interface PageData {
			user?: User | null;
		}
	}
}

export {};
