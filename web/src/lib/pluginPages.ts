import type { PluginPanel, UUID } from '$lib/api';

/** Where a plugin's page in the navigation rail (manifest `ui` slot nav.page) opens in a project. */
export const pluginPageHref = (projectId: UUID, panel: Pick<PluginPanel, 'id' | 'plugin'>) =>
	`/projects/${projectId}/plugins/${encodeURIComponent(panel.plugin.id)}/${encodeURIComponent(panel.id)}`;
