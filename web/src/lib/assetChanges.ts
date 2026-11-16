import type { AssetChangeKind, AssetFieldChange } from '$lib/api';
import { show } from '$lib/assetTypes.svelte';

/** How each kind of change reads, for an asset's timeline and the project's Changes. */
export const CHANGE_KIND: Record<AssetChangeKind, { label: string; icon: string; tone: string }> = {
	new: { label: 'New', icon: 'add_circle', tone: 'text-primary' },
	changed: { label: 'Changed', icon: 'change_circle', tone: 'text-tertiary' },
	seen: { label: 'Seen', icon: 'visibility', tone: 'text-on-surface-variant' },
	gone: { label: 'Not seen', icon: 'visibility_off', tone: 'text-error' },
	still_gone: { label: 'Still not seen', icon: 'visibility_off', tone: 'text-on-surface-variant' },
	back: { label: 'Back', icon: 'history', tone: 'text-primary' }
};

/** "version 1.24 → 1.26", "title: Login" (first reported). */
export function describeChange(c: AssetFieldChange): string {
	if (c.field === 'look') return c.before === null || c.before === undefined ? 'screenshot taken' : 'looks different';
	return c.before === null || c.before === undefined ? `${c.field}: ${show(c.after)}` : `${c.field} ${show(c.before)} → ${show(c.after)}`;
}

/** A day heading: "Today", "Yesterday", "Tue 10 Nov". */
export function dayLabel(iso: string, now = new Date()): string {
	const d = new Date(iso);
	const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
	const days = Math.round((start(now) - start(d)) / 86_400_000);
	if (days === 0) return 'Today';
	if (days === 1) return 'Yesterday';
	return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' });
}
