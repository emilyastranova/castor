import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		adapter: adapter(),
		// Preloads as <link> tags in the page rather than a Link header (hooks.server.ts drops the
		// header): it grows past 4 KB on big pages, more than nginx accepts from an upstream by
		// default, so a reverse proxy in front of Castor would answer 502.
		output: { preloadStrategy: 'preload-js' },
		alias: {
			$m3: 'src/lib/m3'
		}
	}
};

export default config;
