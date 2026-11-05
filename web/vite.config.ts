import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

// The API the dev server proxies to (CASTOR_API_URL lets a second instance, such as the
// screenshot demo, run beside the usual one).
const api = (process.env.CASTOR_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	server: {
		port: 5173,
		proxy: {
			'/api': { target: api, changeOrigin: true },
			'/ws': { target: api.replace(/^http/, 'ws'), ws: true }
		}
	}
});
