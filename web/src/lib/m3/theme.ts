/** Runtime theming: light/dark preference and optional custom seed colour. */
import { browser } from '$app/environment';
import customColors from './custom-colors.json';
import type { Theme } from '$lib/api';

export type ThemeMode = 'system' | 'light' | 'dark';

const MODE_KEY = 'castor.theme';
const SEED_KEY = 'castor.seed';

export function getMode(): ThemeMode {
	if (!browser) return 'system';
	try {
		const v = localStorage.getItem(MODE_KEY);
		return v === 'light' || v === 'dark' ? v : 'system';
	} catch {
		return 'system';
	}
}

export function setMode(mode: ThemeMode): void {
	if (!browser) return;
	try {
		if (mode === 'system') localStorage.removeItem(MODE_KEY);
		else localStorage.setItem(MODE_KEY, mode);
	} catch {
		/* private mode */
	}
	if (mode === 'system') delete document.documentElement.dataset.theme;
	else document.documentElement.dataset.theme = mode;
}

export function isDark(): boolean {
	if (!browser) return false;
	const modes = currentTheme().modes;
	if (modes !== 'both') return modes === 'dark';
	const mode = getMode();
	if (mode !== 'system') return mode === 'dark';
	return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

const ROLES = [
	'primary', 'onPrimary', 'primaryContainer', 'onPrimaryContainer',
	'secondary', 'onSecondary', 'secondaryContainer', 'onSecondaryContainer',
	'tertiary', 'onTertiary', 'tertiaryContainer', 'onTertiaryContainer',
	'error', 'onError', 'errorContainer', 'onErrorContainer',
	'surface', 'onSurface', 'surfaceVariant', 'onSurfaceVariant',
	'surfaceDim', 'surfaceBright', 'surfaceContainerLowest', 'surfaceContainerLow',
	'surfaceContainer', 'surfaceContainerHigh', 'surfaceContainerHighest',
	'inverseSurface', 'inverseOnSurface', 'inversePrimary',
	'outline', 'outlineVariant', 'shadow', 'scrim', 'surfaceTint',
	'primaryFixed', 'primaryFixedDim', 'onPrimaryFixed', 'onPrimaryFixedVariant',
	'secondaryFixed', 'secondaryFixedDim', 'onSecondaryFixed', 'onSecondaryFixedVariant',
	'tertiaryFixed', 'tertiaryFixedDim', 'onTertiaryFixed', 'onTertiaryFixedVariant',
	'background', 'onBackground'
] as const;

const kebab = (s: string) => s.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());

const customVarNames = (name: string) => [
	`--md-custom-color-${name}`,
	`--md-custom-color-on-${name}`,
	`--md-custom-color-${name}-container`,
	`--md-custom-color-on-${name}-container`
];

export function getSeed(): string | null {
	if (!browser) return null;
	try {
		return localStorage.getItem(SEED_KEY);
	} catch {
		return null;
	}
}

const VARS_KEY = 'castor.seed.vars';
type Palette = Record<string, string>;

/** Every colour variable for a seed, light and dark, generated in the browser. */
async function palettes(seed: string): Promise<{ light: Palette; dark: Palette }> {
	const mcu = await import('@material/material-color-utilities');
	const source = mcu.argbFromHex(seed);
	const build = (dark: boolean): Palette => {
		const out: Palette = {};
		const scheme = new mcu.SchemeTonalSpot(mcu.Hct.fromInt(source), dark, 0);
		for (const role of ROLES) {
			const color = (mcu.MaterialDynamicColors as unknown as Record<string, { getArgb(s: unknown): number }>)[role];
			if (color) out[`--md-sys-color-${kebab(role)}`] = mcu.hexFromArgb(color.getArgb(scheme));
		}
		for (const [name, hex] of Object.entries(customColors)) {
			const group = mcu.customColor(source, { name, value: mcu.argbFromHex(hex), blend: false });
			const g = dark ? group.dark : group.light;
			const [c, on, container, onContainer] = customVarNames(name);
			out[c] = mcu.hexFromArgb(g.color);
			out[on] = mcu.hexFromArgb(g.onColor);
			out[container] = mcu.hexFromArgb(g.colorContainer);
			out[onContainer] = mcu.hexFromArgb(g.onColorContainer);
		}
		return out;
	};
	return { light: build(false), dark: build(true) };
}

function cached(seed: string): { light: Palette; dark: Palette } | null {
	try {
		const c = JSON.parse(localStorage.getItem(VARS_KEY) ?? 'null');
		return c?.seed === seed ? c : null;
	} catch {
		return null;
	}
}

const ACCENT_KEY = 'castor.seed.accent';
/** Bump when accent() changes, so cached accents are worked out again (app.html checks it too). */
const ACCENT_VERSION = 4;
/** What an `colour: accent` theme reads (server theme_spec.ACCENT_VARS). */
const ACCENT_VARS = ['--theme-accent', '--theme-accent-hover', '--theme-accent-container', '--theme-accent-inverse', '--theme-accent-dim', '--theme-accent-deep'];

/** A vivid accent in the seed's hue, for dark and for light grounds. On dark: the lightest tone
 *  (60–92: 5:1 or more on a dark surface, dark text on it) at which the hue still holds its
 *  colour, as acid does high up while reds and purples only stay vivid lower down. On light: the
 *  darkest such tone from 30 to 50 (white text on it). With its hover, container and inverse. */
async function accent(seed: string): Promise<{ dark: Palette; light: Palette }> {
	const mcu = await import('@material/material-color-utilities');
	const hct = mcu.Hct.fromInt(mcu.argbFromHex(seed));
	const want = Math.max(hct.chroma, 64);
	const at = (t: number, c = want) => mcu.hexFromArgb(mcu.Hct.from(hct.hue, c, t).toInt());
	const pick = (tones: number[]) => {
		let best = { tone: tones[0], chroma: 0 };
		for (const t of tones) {
			const c = mcu.Hct.from(hct.hue, want, t).chroma;
			if (c >= want * 0.9) return t;
			if (c > best.chroma) best = { tone: t, chroma: c };
		}
		return best.tone;
	};
	const dark = pick([92, 90, 88, 86, 84, 82, 80, 78, 76, 74, 72, 70, 68, 66, 64, 62, 60]);
	const light = pick([30, 32, 34, 36, 38, 40, 42, 44, 46, 48, 50].reverse());
	return {
		dark: {
			'--theme-accent': at(dark),
			'--theme-accent-hover': at(Math.min(dark + 8, 96)),
			'--theme-accent-container': at(18, Math.min(want, 40)),
			'--theme-accent-inverse': at(40),
			'--theme-accent-dim': at(Math.max(dark - 10, 50)),
			'--theme-accent-deep': at(30)
		},
		light: {
			'--theme-accent': at(light),
			'--theme-accent-hover': at(Math.max(light - 8, 20)),
			'--theme-accent-container': at(90, Math.min(want, 40)),
			'--theme-accent-inverse': at(80),
			'--theme-accent-dim': at(Math.min(light + 10, 60)),
			'--theme-accent-deep': at(30)
		}
	};
}
function cachedAccent(seed: string): { dark: Palette; light: Palette } | null {
	try {
		const c = JSON.parse(localStorage.getItem(ACCENT_KEY) ?? 'null');
		return c?.seed === seed && c.v === ACCENT_VERSION ? c : null;
	} catch {
		return null;
	}
}

function clearVars(root: HTMLElement) {
	for (const v of ACCENT_VARS) root.style.removeProperty(v);
	for (const role of ROLES) root.style.removeProperty(`--md-sys-color-${kebab(role)}`);
	for (const name of Object.keys(customColors)) {
		for (const v of customVarNames(name)) root.style.removeProperty(v);
	}
}

/** Recolour the whole app from a seed. Pass null to return to the built-in palette. Both
 *  palettes are cached so app.html can paint the right one before any script loads, and a
 *  light/dark switch needs no recomputing. */
export async function applySeed(seed: string | null): Promise<void> {
	if (!browser) return;
	const root = document.documentElement;
	if (!seed) {
		try {
			localStorage.removeItem(SEED_KEY);
			localStorage.removeItem(VARS_KEY);
			localStorage.removeItem(ACCENT_KEY);
		} catch {
			/* ignore */
		}
		delete root.dataset.seed;
		clearVars(root);
		return;
	}
	const colour = currentTheme().colour;
	if (colour !== 'palette') {
		// The theme keeps its own colours: an accent theme takes the seed for its accent only, a
		// fixed one not at all (the seed is kept for when they pick another theme).
		try {
			localStorage.setItem(SEED_KEY, seed);
		} catch {
			/* private mode */
		}
		clearVars(root);
		delete root.dataset.seed;
		if (colour === 'fixed') return;
		const both = cachedAccent(seed) ?? (await accent(seed));
		try {
			localStorage.setItem(ACCENT_KEY, JSON.stringify({ seed, ...both, v: ACCENT_VERSION }));
		} catch {
			/* private mode */
		}
		root.dataset.seed = seed;
		for (const [name, value] of Object.entries(isDark() ? both.dark : both.light)) root.style.setProperty(name, value);
		return;
	}
	const both = cached(seed) ?? (await palettes(seed));
	clearVars(root);
	try {
		localStorage.setItem(SEED_KEY, seed);
		localStorage.setItem(VARS_KEY, JSON.stringify({ seed, ...both }));
	} catch {
		/* private mode: it still applies for this page */
	}
	root.dataset.seed = seed;
	for (const [name, value] of Object.entries(isDark() ? both.dark : both.light)) root.style.setProperty(name, value);
}

/** Call once on app start: re-applies a custom seed and keeps it in sync with light/dark. */
export function initTheme(): void {
	if (!browser) return;
	const seed = getSeed();
	if (seed) void applySeed(seed);
	window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
		const s = getSeed();
		if (s) void applySeed(s);
	});
}

export function toggleMode(): ThemeMode {
	const next: ThemeMode = isDark() ? 'light' : 'dark';
	setMode(next);
	const seed = getSeed();
	if (seed) void applySeed(seed);
	return next;
}

// ---------- themes (docs/THEME_SPEC.md) ----------

const SKIN_KEY = 'castor.skin';

/** What this browser remembers of the chosen theme: enough for app.html to paint it before any
 *  script runs (its CSS and fonts), and for isDark() and applySeed(). */
interface SavedTheme {
	id: string;
	modes: Theme['modes'];
	colour: Theme['colour'];
	css: string;
	stylesheets: string[];
}
const NONE: SavedTheme = { id: '', modes: 'both', colour: 'palette', css: '', stylesheets: [] };

export function currentTheme(): SavedTheme {
	if (!browser) return NONE;
	try {
		const saved = JSON.parse(localStorage.getItem(SKIN_KEY) ?? 'null');
		return saved?.id ? { ...NONE, ...saved } : NONE;
	} catch {
		return NONE;
	}
}

function loadStylesheets(urls: string[]) {
	for (const href of urls) {
		if (document.querySelector(`link[data-theme-font="${CSS.escape(href)}"]`)) continue;
		const link = document.createElement('link');
		link.rel = 'stylesheet';
		link.href = href;
		link.dataset.themeFont = href;
		document.head.appendChild(link);
	}
}

/** Switch the interface to a theme (Castor's own when its id is '') and remember it here. */
export function applyTheme(theme: Pick<Theme, 'id' | 'modes' | 'colour' | 'css' | 'stylesheets'>): void {
	if (!browser) return;
	const root = document.documentElement;
	const saved: SavedTheme = { id: theme.id, modes: theme.modes, colour: theme.colour, css: theme.css, stylesheets: theme.stylesheets };
	try {
		if (theme.id) localStorage.setItem(SKIN_KEY, JSON.stringify(saved));
		else localStorage.removeItem(SKIN_KEY);
	} catch {
		/* private mode: it still applies for this page */
	}
	let style = document.getElementById('castor-theme') as HTMLStyleElement | null;
	if (theme.id) {
		if (!style) {
			style = document.createElement('style');
			style.id = 'castor-theme';
			document.head.appendChild(style);
		}
		if (style.textContent !== theme.css) style.textContent = theme.css;
		root.dataset.skin = theme.id;
		loadStylesheets(theme.stylesheets);
	} else {
		style?.remove();
		delete root.dataset.skin;
	}
	const seed = getSeed();
	if (seed) void applySeed(seed);
	else clearVars(root);
}
