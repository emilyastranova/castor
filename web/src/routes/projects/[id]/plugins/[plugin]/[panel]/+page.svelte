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

<TopAppBar>
	<ProjectSwitcher {project} />
</TopAppBar>

<div class="flex min-h-0 flex-1 flex-col overflow-auto">
	<div class="mx-auto flex h-full min-h-[34rem] w-full flex-col gap-3 px-6 pt-2 pb-4">
		{#if panel}
			<h1 class="flex items-center gap-2 title-large text-on-surface">
				<Icon name={panel.icon} class="text-primary" />{panel.title}
				<span class="body-small text-on-surface-variant">· {panel.plugin.name}</span>
			</h1>
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
