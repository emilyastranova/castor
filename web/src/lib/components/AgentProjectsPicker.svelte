<script lang="ts">
	import type { ProjectSummary, UUID } from '$lib/api';
	import { api } from '$lib/api';
	import { Chip, Icon } from '$lib/m3';

	/** Which projects' jobs an agent may run: any project (null), or the ones ticked here. */
	interface Props {
		value: UUID[] | null;
		/** What it's about, under the choice. */
		supporting?: string;
		/** Only some projects: "Any project" isn't offered. */
		limited?: boolean;
		/** The heading over the list of projects. */
		listLabel?: string;
	}
	let { value = $bindable(null), supporting = '', limited = false, listLabel = 'Runs jobs from' }: Props = $props();
	$effect(() => {
		if (limited && value === null) value = [];
	});
	const labelId = `agent-projects-${Math.random().toString(36).slice(2, 8)}`;

	let projects = $state<ProjectSummary[]>([]);
	let q = $state('');
	$effect(() => {
		api()
			.projects.list()
			.then((p) => (projects = [...p].sort((a, b) => a.codename.localeCompare(b.codename))))
			.catch(() => (projects = []));
	});
	let shown = $derived(
		q.trim() ? projects.filter((p) => `${p.codename} ${p.name} ${p.client_name ?? ''}`.toLowerCase().includes(q.trim().toLowerCase())) : projects
	);
	function toggle(id: UUID) {
		const now = value ?? [];
		value = now.includes(id) ? now.filter((x) => x !== id) : [...now, id];
	}
</script>

<div class="flex flex-col gap-2">
	<span class="label-large text-on-surface" id={labelId}>Projects</span>
	{#if !limited}
	<div class="flex flex-wrap gap-2" role="radiogroup" aria-labelledby={labelId}>
		<Chip variant="filter" role="radio" aria-checked={value === null} selected={value === null} onclick={() => (value = null)}>Any project</Chip>
		<Chip variant="filter" role="radio" aria-checked={value !== null} selected={value !== null} onclick={() => value === null && (value = [])}>Only some projects</Chip>
	</div>
	{/if}
	{#if supporting}<p class="body-small text-on-surface-variant">{supporting}</p>{/if}
	{#if value !== null}
		<div class="flex flex-col gap-1 rounded-md border border-outline-variant/70 p-2">
			<div class="flex items-center gap-2 px-1">
				<span class="flex-1 label-large text-on-surface">{listLabel}</span>
				<span class="body-small {value.length ? 'text-on-surface-variant' : 'text-error'}">{value.length ? `${value.length} chosen` : 'None: it runs nothing'}</span>
			</div>
			{#if projects.length > 8}
				<label class="mx-1 flex items-center gap-2 rounded-full bg-surface-container-high px-3">
					<Icon name="search" size={18} class="text-on-surface-variant" />
					<input class="h-9 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Find a project" bind:value={q} />
				</label>
			{/if}
			<ul class="max-h-56 overflow-y-auto" role="group" aria-label="Projects">
				{#each shown as p (p.id)}
					{@const on = value.includes(p.id)}
					<li>
						<button type="button" role="checkbox" aria-checked={on} class="state-layer flex w-full items-center gap-3 rounded-sm px-2 py-1.5 text-left" onclick={() => toggle(p.id)}>
							<Icon name={on ? 'check_box' : 'check_box_outline_blank'} size={20} class={on ? 'text-primary' : 'text-on-surface-variant'} />
							<span class="min-w-0 flex-1">
								<span class="block truncate body-medium text-on-surface">{p.codename}</span>
								<span class="block truncate body-small text-on-surface-variant">{p.name}{p.client_name ? ` · ${p.client_name}` : ''}</span>
							</span>
						</button>
					</li>
				{:else}
					<li class="px-2 py-2 body-small text-on-surface-variant">{projects.length ? 'No project matches.' : 'No projects yet.'}</li>
				{/each}
			</ul>
		</div>
	{/if}
</div>
