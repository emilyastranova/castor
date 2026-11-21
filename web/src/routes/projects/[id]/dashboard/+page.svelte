<script lang="ts">
	import { brand } from '$lib/brand.svelte';
	import { TopAppBar } from '$lib/m3';
	import Dashboard from '$lib/components/dashboard/Dashboard.svelte';
	import ProjectSwitcher from '$lib/components/ProjectSwitcher.svelte';

	let { data } = $props();
</script>

<svelte:head><title>{data.project.codename} · Home · {brand.name}</title></svelte:head>

<TopAppBar>
	<ProjectSwitcher project={data.project} />
</TopAppBar>

<div class="flex-1 overflow-auto">
	{#key data.project.id}
		<Dashboard projectId={data.project.id} />
	{/key}
</div>
