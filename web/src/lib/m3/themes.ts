/** Castor's own look, the theme everyone starts on. Every other theme comes from the server
 *  (docs/THEME_SPEC.md, `GET /api/themes`) as CSS it compiled; lib/themes.svelte.ts lists them. */
import type { Theme } from '$lib/api';

export const CASTOR: Theme = {
	id: '',
	origin: 'castor',
	name: 'Castor',
	description: 'Soft Material 3, in light or dark, coloured from your theme colour.',
	modes: 'both',
	colour: 'palette',
	default_colour: '#4f5bd5',
	preview: ['#fbf8ff', '#efedf4', '#1b1b21', '#545a92'],
	css: '',
	stylesheets: [],
	source_yaml: '',
	version: 1,
	used_by: 0,
	spec: {}
};
