/** Runtime theming: light/dark preference and optional custom seed colour. */
import { browser } from '$app/environment';

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

export function getSeed(): string | null {
	if (!browser) return null;
	try {
		return localStorage.getItem(SEED_KEY);
	} catch {
		return null;
	}
}

/** Recolour the whole app from a seed. Pass null to return to the built-in palette. */
export async function applySeed(seed: string | null): Promise<void> {
	if (!browser) return;
	const root = document.documentElement;
	try {
		if (seed) localStorage.setItem(SEED_KEY, seed);
		else localStorage.removeItem(SEED_KEY);
	} catch {
		/* ignore */
	}
	if (!seed) {
		delete root.dataset.seed;
		for (const role of ROLES) root.style.removeProperty(`--md-sys-color-${kebab(role)}`);
		return;
	}
	root.dataset.seed = seed;
	const mcu = await import('@material/material-color-utilities');
	const scheme = new mcu.SchemeTonalSpot(mcu.Hct.fromInt(mcu.argbFromHex(seed)), isDark(), 0);
	for (const role of ROLES) {
		const color = (mcu.MaterialDynamicColors as unknown as Record<string, { getArgb(s: unknown): number }>)[role];
		if (color) root.style.setProperty(`--md-sys-color-${kebab(role)}`, mcu.hexFromArgb(color.getArgb(scheme)));
	}
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
