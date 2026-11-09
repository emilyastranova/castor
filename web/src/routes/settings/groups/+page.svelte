<script lang="ts">
	import type { Group } from '$lib/api';
	import { Button, Card, Icon, IconButton, TopAppBar } from '$lib/m3';
	import GroupDialog from '$lib/components/GroupDialog.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let groups = $state<Group[]>(data.groups);
	let dialogOpen = $state(false);
	let editing = $state<Group | null>(null);

	function saved(g: Group) {
		groups = [...groups.filter((x) => x.id !== g.id), g].sort((a, b) => a.name.localeCompare(b.name));
	}
	const people = (n: number) => `${n} ${n === 1 ? 'person' : 'people'}`;
</script>

<svelte:head><title>Groups · Settings · Castor</title></svelte:head>

<TopAppBar title="Groups" subtitle="People put together: mention them all, share projects with them">
	{#snippet actions()}
		<Button icon="add" onclick={() => { editing = null; dialogOpen = true; }}>Add group</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-2 pt-2">
		<p class="body-small text-on-surface-variant">
			Mentioning a group in a comment (<code class="font-mono">@handle</code>) notifies everyone in it who can see the project. Share a project with groups from its Members dialog.
		</p>
		{#each groups as g (g.id)}
			<Card variant="outlined" class="flex items-center gap-3 p-3">
				<span class="flex size-9 shrink-0 items-center justify-center rounded-full bg-tertiary-container text-on-tertiary-container"><Icon name="groups" size={20} /></span>
				<button type="button" class="min-w-0 flex-1 text-left" onclick={() => { editing = g; dialogOpen = true; }}>
					<span class="block truncate title-small text-on-surface">{g.name} <span class="body-small text-on-surface-variant">@{g.slug}</span></span>
					<span class="block truncate body-small text-on-surface-variant">
						{people(g.member_count)} · {g.project_count} project{g.project_count === 1 ? '' : 's'}{g.sso_group ? ` · follows ${g.sso_group}` : ''}{g.description ? ` · ${g.description}` : ''}
					</span>
				</button>
				<IconButton icon="edit" label="Edit @{g.slug}" onclick={() => { editing = g; dialogOpen = true; }} />
			</Card>
		{:else}
			<p class="mt-4 body-medium text-on-surface-variant">No groups yet. Add one for a team, a client's testers or the on-call rota.</p>
		{/each}
	</div>
</div>

<GroupDialog bind:open={dialogOpen} group={editing} onsaved={saved} onremoved={(id) => (groups = groups.filter((g) => g.id !== id))} />
