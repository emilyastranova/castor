import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')) as { version: string };

// The API the dev server proxies to (CASTOR_API_URL lets a second instance, such as the
// screenshot demo, run beside the usual one).
const api = (process.env.CASTOR_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	// Shown with the server's in the account menu and Settings (lib/version.svelte.ts).
	define: { __CASTOR_WEB_VERSION__: JSON.stringify(pkg.version) },
	server: {
		port: 5173,
		proxy: {
			// xfwd: the API names this host in plugin pages' CSP (X-Forwarded-Host).
			'/api': { target: api, changeOrigin: true, xfwd: true },
			'/ws': { target: api.replace(/^http/, 'ws'), ws: true }
		}
	}
});
