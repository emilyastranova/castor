/** Runtime theming: light/dark preference and optional custom seed colour. */
import { browser } from '$app/environment';
import customColors from './custom-colors.json';
import { themeById, type ThemeDef, type ThemeId } from './themes';

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
	if (currentTheme().darkOnly) return true;
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
/** Bump when accent() changes, so cached accents are worked out again. */
const ACCENT_VERSION = 3;
/** Read by an 'accent' theme (themes/astralabs.css): its accent and the roles drawn from it. */
const ACCENT_VARS = ['--al-acid', '--al-acid-hi', '--md-sys-color-primary-container', '--md-sys-color-inverse-primary', '--md-sys-color-primary-fixed-dim', '--md-sys-color-on-primary-fixed-variant'];

/** A vivid accent in the seed's hue for a dark theme: the lightest tone (60–92, so 5:1 or more
 *  against carbon, with carbon text on it) at which the hue still holds its colour. Yellows and
 *  greens stay vivid high up, as acid does; reds and purples only lower down, where light tones
 *  would turn them pastel. Also its hover, a deep container and the inverse for light grounds. */
async function accent(seed: string): Promise<Palette> {
	const mcu = await import('@material/material-color-utilities');
	const hct = mcu.Hct.fromInt(mcu.argbFromHex(seed));
	const want = Math.max(hct.chroma, 64);
	const chromaAt = (tone: number) => mcu.Hct.from(hct.hue, want, tone).chroma;
	let tone = 92;
	let best = { tone: 60, chroma: 0 };
	for (let t = 92; t >= 60; t -= 2) {
		const c = chromaAt(t);
		if (c > best.chroma) best = { tone: t, chroma: c };
		if (c >= want * 0.9) {
			tone = t;
			break;
		}
		tone = best.tone;
	}
	const at = (t: number, c = want) => mcu.hexFromArgb(mcu.Hct.from(hct.hue, c, t).toInt());
	return {
		'--al-acid': at(tone),
		'--al-acid-hi': at(Math.min(tone + 8, 96)),
		'--md-sys-color-primary-container': at(18, Math.min(want, 40)),
		'--md-sys-color-inverse-primary': at(40),
		'--md-sys-color-primary-fixed-dim': at(Math.max(tone - 10, 50)),
		'--md-sys-color-on-primary-fixed-variant': at(30)
	};
}
function cachedAccent(seed: string): Palette | null {
	try {
		const c = JSON.parse(localStorage.getItem(ACCENT_KEY) ?? 'null');
		return c?.seed === seed && c.v === ACCENT_VERSION ? c.vars : null;
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
	if (currentTheme().seed === 'accent') {
		// The theme keeps its own surfaces; the colour only replaces its accent.
		const vars = cachedAccent(seed) ?? (await accent(seed));
		try {
			localStorage.setItem(SEED_KEY, seed);
			localStorage.setItem(ACCENT_KEY, JSON.stringify({ seed, vars, v: ACCENT_VERSION }));
		} catch {
			/* private mode */
		}
		clearVars(root);
		root.dataset.seed = seed;
		for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
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

// ---------- themes (themes.ts) ----------

const SKIN_KEY = 'castor.skin';

/** The theme this browser shows (saved with what app.html needs before first paint). */
export function currentTheme(): ThemeDef {
	if (!browser) return themeById('');
	try {
		return themeById(JSON.parse(localStorage.getItem(SKIN_KEY) ?? 'null')?.id);
	} catch {
		return themeById('');
	}
}

function loadFonts(urls: string[]) {
	for (const href of urls) {
		if (document.querySelector(`link[data-theme-font="${CSS.escape(href)}"]`)) continue;
		const link = document.createElement('link');
		link.rel = 'stylesheet';
		link.href = href;
		link.dataset.themeFont = href;
		document.head.appendChild(link);
	}
}

/** Switch the whole interface to a theme ('' for Castor's own) and remember it here. */
export function applyTheme(id: ThemeId | string): void {
	if (!browser) return;
	const theme = themeById(id);
	const root = document.documentElement;
	try {
		if (theme.id) localStorage.setItem(SKIN_KEY, JSON.stringify({ id: theme.id, fonts: theme.fonts, seed: theme.seed, darkOnly: theme.darkOnly }));
		else localStorage.removeItem(SKIN_KEY);
	} catch {
		/* private mode: it still applies for this page */
	}
	if (theme.id) root.dataset.skin = theme.id;
	else delete root.dataset.skin;
	loadFonts(theme.fonts);
	const seed = getSeed();
	if (seed) void applySeed(seed);
	else clearVars(root);
}
