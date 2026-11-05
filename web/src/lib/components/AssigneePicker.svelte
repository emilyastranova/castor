<script lang="ts">
	import { Avatar, Icon, Menu } from '$lib/m3';
	import { people } from '$lib/people.svelte';
	import { me } from '$lib/session';

	/** A card's assignees: chips, and (when editable) a menu of the people on the project. */
	interface Props {
		projectId: string;
		value: string[];
		editable?: boolean;
		onchange: (ids: string[]) => void;
	}
	let { projectId, value, editable = false, onchange }: Props = $props();

	let everyone = $derived(people.list(projectId));
	let assigned = $derived(value.map((id) => everyone.find((u) => u.id === id) ?? { id, display_name: 'Someone', username: '', avatar_url: null }));
	let myId = $derived(me()?.id);
	let filter = $state('');
	let shown = $derived(
		everyone.filter((u) => {
			const q = filter.trim().toLowerCase();
			return !q || u.display_name.toLowerCase().includes(q) || u.username.toLowerCase().includes(q);
		})
	);

	function toggle(id: string) {
		onchange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
	}
</script>

<div class="flex flex-col gap-1.5">
	<div class="flex items-center gap-2">
		<span class="flex-1 label-large text-on-surface-variant">Assignees</span>
		{#if editable && myId && !value.includes(myId)}
			<button type="button" class="label-medium text-primary hover:underline" onclick={() => onchange([...value, myId!])}>Assign to me</button>
		{/if}
		{#if editable}
			<Menu>
				{#snippet anchor()}
					<button type="button" class="state-layer flex size-7 items-center justify-center rounded-full text-on-surface-variant" aria-label="Choose assignees" title="Choose assignees"><Icon name="person_add" size={18} /></button>
				{/snippet}
				<div class="w-64" role="presentation" onclick={(e) => e.stopPropagation()}>
					<input class="mx-3 mb-1 h-9 w-[calc(100%-1.5rem)] rounded-xs border border-outline-variant bg-transparent px-2 body-medium text-on-surface outline-none focus:border-primary" placeholder="Find someone" bind:value={filter} />
					<ul class="max-h-64 overflow-y-auto">
						{#each shown as u (u.id)}
							<li>
								<button type="button" role="menuitemcheckbox" aria-checked={value.includes(u.id)} class="state-layer flex w-full items-center gap-2 px-3 py-1.5 text-left" onclick={() => toggle(u.id)}>
									<Avatar name={u.display_name} src={u.avatar_url} size={24} />
									<span class="min-w-0 flex-1 truncate body-medium text-on-surface">{u.display_name}{u.id === myId ? ' (you)' : ''}</span>
									{#if value.includes(u.id)}<Icon name="check" size={18} class="text-primary" />{/if}
								</button>
							</li>
						{:else}
							<li class="px-3 py-2 body-small text-on-surface-variant">Nobody matches.</li>
						{/each}
					</ul>
				</div>
			</Menu>
		{/if}
	</div>
	{#if assigned.length}
		<ul class="flex flex-wrap gap-1.5">
			{#each assigned as u (u.id)}
				<li class="flex h-8 items-center gap-1.5 rounded-full bg-surface-container-high py-1 pr-1 pl-1 {editable ? '' : 'pr-3'}">
					<Avatar name={u.display_name} src={u.avatar_url} size={24} />
					<span class="max-w-36 truncate label-large text-on-surface">{u.display_name}</span>
					{#if editable}
						<button type="button" class="state-layer flex size-6 items-center justify-center rounded-full text-on-surface-variant" aria-label="Unassign {u.display_name}" title="Unassign" onclick={() => toggle(u.id)}><Icon name="close" size={16} /></button>
					{/if}
				</li>
			{/each}
		</ul>
	{:else}
		<p class="body-small text-on-surface-variant">Nobody yet.</p>
	{/if}
</div>
