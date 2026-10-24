<script lang="ts">
	import { goto } from '$app/navigation';
	import type { Activity } from '$lib/api';
	import { TopAppBar, Chip } from '$lib/m3';
	import ActivityFeed from '$lib/components/ActivityFeed.svelte';
	import { realtime } from '$lib/realtime.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let items = $state<Activity[]>(data.activity);
	let projectFilter = $state<string | null>(null);
	// svelte-ignore state_referenced_locally
	let loaded = data.activity;
	$effect(() => {
		if (data.activity !== loaded) {
			loaded = data.activity;
			items = data.activity;
		}
	});
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name === 'activity.created') items = [e.data as Activity, ...items].slice(0, 300);
		})
	);
	let visible = $derived(projectFilter ? items.filter((a) => a.project_id === projectFilter) : items);
	let projectOf = (id: string | null) => data.projects.find((p) => p.id === id);
</script>

<svelte:head><title>Activity · Castor</title></svelte:head>

<TopAppBar title="Activity" subtitle="Everything that happened across workflows" />
<div class="flex flex-wrap gap-2 px-6 pb-2">
	<Chip variant="filter" selected={projectFilter === null} onclick={() => (projectFilter = null)}>All</Chip>
	{#each data.projects as p (p.id)}
		<Chip variant="filter" selected={projectFilter === p.id} onclick={() => (projectFilter = p.id)}>{p.codename}</Chip>
	{/each}
</div>
<div class="flex-1 overflow-auto px-2 pb-8">
	<div class="mx-auto max-w-3xl rounded-md bg-surface-container-low">
		<ActivityFeed
			items={visible}
			onopentask={(taskId) => {
				const a = visible.find((x) => x.task_id === taskId);
				const p = projectOf(a?.project_id ?? null);
				if (p) void goto(`/projects/${p.id}?task=${taskId}`);
			}}
		/>
	</div>
</div>
