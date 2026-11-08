<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { ProjectSummary } from '$lib/api';
	import { api } from '$lib/api';
	import { Icon, Menu, MenuItem } from '$lib/m3';
	import { canEdit } from '$lib/session';
	import { currentProject } from '$lib/currentProject.svelte';

	/** A project page's title, which is also where you switch projects: the codename and name,
	 *  and a menu of your other projects that opens the same view (Home, Board, Assets) there. */
	let { project }: { project: { id: string; codename: string; name: string } } = $props();

	const RECENT = 8;
	let open = $state(false);
	let projects = $state<ProjectSummary[] | null>(null);
	$effect(() => {
		if (open && projects === null) {
			api()
				.projects.list()
				.then((list) => (projects = list.toSorted((a, b) => b.updated_at.localeCompare(a.updated_at))))
				.catch(() => (projects = []));
		}
	});
	let others = $derived((projects ?? []).filter((p) => p.id !== project.id).slice(0, RECENT));

	function viewIn(id: string): string {
		const route = page.route.id ?? '';
		if (route.endsWith('/assets')) return `/projects/${id}/assets`;
		if (route.endsWith('/dashboard')) return `/projects/${id}/dashboard`;
		return `/projects/${id}`;
	}
	function pick(id: string) {
		currentProject.set(id); // chosen here, so the project layout doesn't announce it
		void goto(viewIn(id));
	}
</script>

<Menu bind:open align="left" class="min-w-0">
	{#snippet anchor()}
		<button
			type="button"
			class="state-layer focus-ring -mx-2 flex min-w-0 items-center gap-1 rounded-md px-2 py-0.5 text-left"
			aria-haspopup="menu"
			aria-expanded={open}
			title="Switch project"
		>
			<span class="min-w-0">
				<span class="block label-medium text-primary">{project.codename}</span>
				<span class="block truncate title-large text-on-surface">{project.name}</span>
			</span>
			<Icon name="unfold_more" size={20} class="mt-4 shrink-0 text-on-surface-variant" />
		</button>
	{/snippet}
	<p class="px-3 pt-1 pb-2 label-medium text-on-surface-variant">Switch project</p>
	{#if projects === null}
		<p class="px-3 pb-2 body-small text-on-surface-variant">Loading…</p>
	{:else}
		<div class="max-h-[50dvh] overflow-y-auto">
			{#each others as p (p.id)}
				<MenuItem icon="view_kanban" onclick={() => pick(p.id)}>
					<span class="block label-medium text-primary">{p.codename}</span>
					<span class="block truncate body-medium text-on-surface">{p.name}</span>
				</MenuItem>
			{:else}
				<p class="px-3 pb-2 body-small text-on-surface-variant">This is your only project.</p>
			{/each}
		</div>
	{/if}
	<div class="my-1 border-t border-outline-variant/60"></div>
	<MenuItem icon="apps" onclick={() => goto('/projects')}>All projects…</MenuItem>
	{#if canEdit()}<MenuItem icon="add" onclick={() => goto('/projects?new=1')}>New project</MenuItem>{/if}
</Menu>
