/** The looks people can pick (users.theme; the server's schemas.ThemeId lists the same ids).
 *  A theme is CSS scoped to `:root[data-skin='<id>']` (see themes/<id>.css) over the Material
 *  tokens, plus the fonts it needs and whether it keeps light/dark and the seed colour. */

export type ThemeId = '' | 'astralabs';

export interface ThemeDef {
	id: ThemeId;
	name: string;
	description: string;
	/** Dark only: the light/dark switch is hidden and isDark() is true. */
	darkOnly: boolean;
	/** What the person's theme colour recolours: every role ('palette', Material You), or only
	 *  the accent the theme keeps for action, focus and selection ('accent'). */
	seed: 'palette' | 'accent';
	/** The colour shown as "Default" in the colour picker (the theme's own accent). */
	defaultColour: string;
	/** Stylesheets (web fonts) it loads when chosen; app.html loads them before first paint. */
	fonts: string[];
	/** For the picker: background, surface, text, accent. */
	preview: [string, string, string, string];
}

export const THEMES: ThemeDef[] = [
	{
		id: '',
		name: 'Castor',
		description: 'Soft Material 3, in light or dark, coloured from your theme colour.',
		darkOnly: false,
		seed: 'palette',
		defaultColour: '#4f5bd5',
		fonts: [],
		preview: ['#fbf8ff', '#efedf4', '#1b1b21', '#545a92']
	},
	{
		id: 'astralabs',
		name: 'AstraLabs',
		description: 'Carbon and bone, with acid or your theme colour. Square corners, hairline rules, mono labels. Dark only.',
		darkOnly: true,
		seed: 'accent',
		defaultColour: '#b6ff2e',
		fonts: [
			'https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,300..900&display=swap',
			'https://fonts.googleapis.com/css2?family=Material+Symbols+Sharp:opsz,wght,FILL,GRAD@20..48,200..500,0..1,0&display=block'
		],
		preview: ['#0a0909', '#131212', '#ede6e1', '#b6ff2e']
	}
];

export const themeById = (id: string | null | undefined): ThemeDef => THEMES.find((t) => t.id === id) ?? THEMES[0];
