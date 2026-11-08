<script lang="ts">
	import { TopAppBar, IconButton } from '$lib/m3';
	import Dashboard from '$lib/components/dashboard/Dashboard.svelte';
	import ProjectViewSwitch from '$lib/components/ProjectViewSwitch.svelte';

	let { data } = $props();
</script>

<svelte:head><title>{data.project.codename} · Dashboard · Castor</title></svelte:head>

<TopAppBar>
	<div class="flex min-w-0 items-center gap-3">
		<IconButton icon="arrow_back" label="All projects" href="/projects" />
		<div class="min-w-0">
			<p class="label-medium text-primary">{data.project.codename}</p>
			<h1 class="truncate title-large text-on-surface">{data.project.name}</h1>
		</div>
	</div>
	{#snippet actions()}
		<div class="hidden md:contents"><ProjectViewSwitch projectId={data.project.id} current="dashboard" /></div>
	{/snippet}
</TopAppBar>
<div class="px-4 pb-2 md:hidden"><ProjectViewSwitch projectId={data.project.id} current="dashboard" /></div>

<div class="flex-1 overflow-auto">
	{#key data.project.id}
		<Dashboard projectId={data.project.id} />
	{/key}
</div>
