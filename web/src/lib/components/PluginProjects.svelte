<script lang="ts">
	import type { ProjectSummary, UUID } from '$lib/api';

	/** Which projects a plugin is enabled in: every one, or those picked. */
	interface Props {
		projects: ProjectSummary[];
		projectIds: UUID[];
		allProjects: boolean;
	}
	let { projects, projectIds = $bindable(), allProjects = $bindable() }: Props = $props();
	const toggle = (id: UUID, on: boolean) => (projectIds = on ? [...projectIds, id] : projectIds.filter((x) => x !== id));
</script>

<div class="flex flex-col gap-2">
	<label class="flex cursor-pointer items-center gap-2 body-medium text-on-surface">
		<input type="radio" checked={allProjects} onchange={() => (allProjects = true)} class="accent-primary" />Every project, now and later
	</label>
	<label class="flex cursor-pointer items-center gap-2 body-medium text-on-surface">
		<input type="radio" checked={!allProjects} onchange={() => (allProjects = false)} class="accent-primary" />Only these
	</label>
	{#if !allProjects}
		<ul class="ml-6 flex max-h-48 flex-col overflow-auto rounded-md border border-outline-variant">
			{#each projects as p (p.id)}
				<li>
					<label class="state-layer flex cursor-pointer items-center gap-2 px-3 py-1.5 body-medium text-on-surface">
						<input type="checkbox" checked={projectIds.includes(p.id)} onchange={(e) => toggle(p.id, (e.currentTarget as HTMLInputElement).checked)} class="accent-primary" />
						<span class="font-mono text-[13px] text-on-surface-variant">{p.codename}</span>{p.name}
					</label>
				</li>
			{:else}
				<li class="px-3 py-2 body-small text-on-surface-variant">No projects yet.</li>
			{/each}
		</ul>
	{/if}
</div>
