<script lang="ts">
	import type { UUID } from '$lib/api';
	import { Icon, Progress } from '$lib/m3';
	import { pluginActions } from '$lib/plugins.svelte';
	import PluginIcon from './PluginIcon.svelte';

	/** The plugin actions running in a project right now, with how far along they are. */
	let { projectId }: { projectId: UUID } = $props();
	let runs = $derived(pluginActions.running(projectId));
	const iconOf = (pluginId: string) => (pluginActions.byProject[projectId] ?? []).find((a) => a.plugin.id === pluginId)?.plugin.icon_url ?? null;
	const share = (p: { done?: number; total?: number }) => (p.total ? Math.min(1, (p.done ?? 0) / p.total) : null);
</script>

{#if runs.length}
	<ul class="flex flex-col gap-2" aria-label="Plugin actions running">
		{#each runs as r (r.id)}
			<li class="flex flex-col gap-1.5 rounded-md bg-surface-container px-3 py-2" aria-busy="true">
				<p class="flex items-center gap-2 body-medium text-on-surface">
					<PluginIcon src={iconOf(r.plugin_id)} size={22} />
					<span class="min-w-0 flex-1 truncate">{r.label ?? r.action}{r.file_name ? `: ${r.file_name}` : ''}</span>
					<span class="shrink-0 body-small text-on-surface-variant">{r.progress.text ?? r.plugin_name ?? ''}</span>
				</p>
				<Progress value={share(r.progress)} />
			</li>
		{/each}
	</ul>
{/if}
