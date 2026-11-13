/** Where a library item came from: Castor's built-ins, someone here, or a plugin's pack
 *  (`plugin:<id>`, docs/PLUGINS.md). Built-ins and plugins' items are read-only: duplicate one to change it. */
export type Origin = 'builtin' | 'user' | `plugin:${string}`;

export const readOnlyOrigin = (origin: string | null | undefined): boolean => !!origin && origin !== 'user';

/** The plugin an item came with, or null. */
export const pluginOf = (origin: string | null | undefined): string | null => (origin?.startsWith('plugin:') ? origin.slice(7) : null);

/** A short word for the item's origin chip. */
export function originLabel(origin: string | null | undefined): string {
	const plugin = pluginOf(origin);
	if (plugin) return `from ${plugin}`;
	return origin === 'builtin' ? 'built-in' : 'yours';
}
