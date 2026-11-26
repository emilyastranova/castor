<script lang="ts">
	import { page } from '$app/state';
	import { brand } from '$lib/brand.svelte';
	import { Icon, TopAppBar } from '$lib/m3';
	import ProjectSwitcher from '$lib/components/ProjectSwitcher.svelte';
	import PluginFrame from '$lib/components/PluginFrame.svelte';
	import { pluginPanels } from '$lib/plugins.svelte';

	/** A plugin's page in the navigation rail (castor-plugin.yaml `ui`, slot nav.page), in this project. */
	let { data } = $props();
	let project = $derived(data.project);

	let ready = $state(false);
	$effect(() => {
		const id = project.id;
		ready = false;
		void pluginPanels.load(id).then(() => (ready = true));
	});
	let panel = $derived(
		pluginPanels.for(project.id, 'nav.page').find((p) => p.plugin.id === page.params.plugin && p.id === page.params.panel)
	);
</script>

<svelte:head><title>{project.codename} · {panel?.title ?? 'Plugin'} · {brand.windowName}</title></svelte:head>

<!-- The page's name sits beside the project's, so the plugin's frame starts right under the bar. -->
<TopAppBar>
	<div class="flex min-w-0 items-center gap-3">
		<div class="min-w-0 shrink"><ProjectSwitcher {project} /></div>
		{#if panel}
			<span class="h-8 w-px shrink-0 bg-outline-variant" aria-hidden="true"></span>
			<h1 class="flex max-w-[55%] min-w-0 shrink-0 items-center gap-2 title-large text-on-surface">
				<Icon name={panel.icon} src={panel.icon_url} class="shrink-0 text-primary" />
				<span class="truncate">{panel.title}</span>
				<span class="hidden shrink-0 body-small text-on-surface-variant sm:inline">· {panel.plugin.name}</span>
			</h1>
		{/if}
	</div>
</TopAppBar>

<div class="flex min-h-0 flex-1 flex-col overflow-auto">
	<div class="mx-auto flex h-full min-h-[34rem] w-full flex-col gap-3 px-6 pb-4">
		{#if panel}
			{#key panel.url}
				<PluginFrame {panel} projectId={project.id} fill />
			{/key}
		{:else if ready}
			<p class="py-6 body-medium text-on-surface-variant">
				That plugin page isn’t in {project.codename}: the plugin may be off, removed, or not enabled in this project.
			</p>
		{/if}
	</div>
</div>
