/** Runtime theming: light/dark preference and optional custom seed colour. */
import { browser } from '$app/environment';
import customColors from './custom-colors.json';

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

function clearVars(root: HTMLElement) {
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
		} catch {
			/* ignore */
		}
		delete root.dataset.seed;
		clearVars(root);
		return;
	}
	const both = cached(seed) ?? (await palettes(seed));
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
