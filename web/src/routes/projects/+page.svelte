<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { TopAppBar, Card, Fab, Icon, Chip } from '$lib/m3';
	import ProjectDialog from '$lib/components/ProjectDialog.svelte';
	import { relativeTime } from '$lib/format';
	import { realtime } from '$lib/realtime.svelte';

	let { data } = $props();
	let createOpen = $state(false);

	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name.startsWith('project.') || e.name.startsWith('job.') || e.name.startsWith('task.')) {
				void invalidateAll();
			}
		})
	);

	const online = $derived(data.agents.filter((a) => a.connected).length);
</script>

<svelte:head><title>Projects · Castor</title></svelte:head>

<TopAppBar title="Projects" subtitle="{data.projects.length} project{data.projects.length === 1 ? '' : 's'} · {online} agent{online === 1 ? '' : 's'} online" />

<div class="flex-1 overflow-auto px-6 pb-24">
	{#if data.projects.length === 0}
		<div class="mx-auto mt-24 flex max-w-md flex-col items-center gap-4 text-center">
			<span class="flex size-20 items-center justify-center rounded-xl bg-primary-container text-on-primary-container">
				<Icon name="view_kanban" size={40} />
			</span>
			<h2 class="headline-small text-on-surface">No projects yet</h2>
			<p class="body-medium text-on-surface-variant">
				Create a project to orchestrate your tasks and workflows. Each one gets its own board with To Do, In Progress, Review and Done columns.
			</p>
		</div>
	{:else}
		<div class="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4 pt-2">
			{#each data.projects as project (project.id)}
				<a href="/projects/{project.id}" class="focus-ring rounded-md">
					<Card variant="elevated" interactive class="flex h-full flex-col gap-3 p-5">
						<div class="flex items-start justify-between gap-2">
							<div class="min-w-0">
								<p class="label-medium text-primary">{project.codename}</p>
								<h2 class="truncate title-large text-on-surface">{project.name}</h2>
								{#if project.client_name}
									<p class="body-medium text-on-surface-variant">{project.client_name}</p>
								{/if}
							</div>
							{#if project.running_count > 0}
								<span class="flex items-center gap-1 rounded-full bg-tertiary-container px-2 py-0.5 text-on-tertiary-container label-small">
									<Icon name="bolt" size={14} filled />
									{project.running_count} running
								</span>
							{/if}
						</div>
						{#if project.description}
							<p class="line-clamp-2 body-medium text-on-surface-variant">{project.description}</p>
						{/if}
						<div class="mt-auto flex items-center gap-2 pt-2">
							<Chip variant="suggestion">{project.kind}</Chip>
							<span class="ml-auto body-small text-on-surface-variant">
								{project.done_count}/{project.task_count} done · {relativeTime(project.updated_at)}
							</span>
						</div>
						{#if project.task_count > 0}
							<div class="h-1 overflow-hidden rounded-full bg-surface-container-highest">
								<div class="h-full bg-primary" style:width="{(project.done_count / project.task_count) * 100}%"></div>
							</div>
						{/if}
					</Card>
				</a>
			{/each}
		</div>
	{/if}
</div>

<div class="fixed right-6 bottom-6">
	<Fab icon="add" label="New project" extended onclick={() => (createOpen = true)}>New project</Fab>
</div>

<ProjectDialog bind:open={createOpen} clients={data.clients} oncreated={() => invalidateAll()} />
