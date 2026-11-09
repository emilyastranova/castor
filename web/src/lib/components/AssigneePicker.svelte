<script lang="ts">
	import PersonName from '$lib/components/PersonName.svelte';
	import { Avatar, Icon, Menu } from '$lib/m3';
	import { people } from '$lib/people.svelte';
	import { me } from '$lib/session';

	/** A card's assignees: chips, and (when editable) a menu of the people on the project and
	 *  the groups (a group's people who can see the card are assigned through it). */
	interface Props {
		projectId: string;
		value: string[];
		/** Group ids. */
		groups?: string[];
		editable?: boolean;
		onchange: (ids: string[]) => void;
		ongroupschange?: (ids: string[]) => void;
	}
	let { projectId, value, groups = [], editable = false, onchange, ongroupschange }: Props = $props();

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

	let allGroups = $derived(people.groups().map((g) => g.group!));
	let assignedGroups = $derived(groups.map((id) => allGroups.find((g) => g.id === id) ?? { id, slug: 'group', name: 'A group', member_ids: [] as string[] }));
	let shownGroups = $derived(
		ongroupschange
			? allGroups.filter((g) => {
					const q = filter.trim().toLowerCase().replace(/^@/, '');
					return !q || g.name.toLowerCase().includes(q) || g.slug.toLowerCase().includes(q);
				})
			: []
	);
	const inGroup = (n: number) => `${n} ${n === 1 ? 'person' : 'people'}`;

	function toggleGroup(id: string) {
		ongroupschange?.(groups.includes(id) ? groups.filter((x) => x !== id) : [...groups, id]);
	}

	function toggle(id: string) {
		onchange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
	}
</script>

<div class="flex flex-col gap-1.5">
	<div class="flex items-center gap-2">
		<span class="flex-1 label-large text-on-surface-variant">Assignees</span>
		{#if editable && myId && !value.includes(myId)}
			<button type="button" class="touch-target label-medium text-primary hover:underline" onclick={() => onchange([...value, myId!])}>Assign to me</button>
		{/if}
		{#if editable}
			<Menu>
				{#snippet anchor()}
					<button type="button" class="state-layer touch-target flex size-7 items-center justify-center rounded-full text-on-surface-variant" aria-label="Choose assignees" title="Choose assignees"><Icon name="person_add" size={18} /></button>
				{/snippet}
				<div class="w-64" role="presentation" onclick={(e) => e.stopPropagation()}>
					<input class="mx-3 mb-1 h-9 w-[calc(100%-1.5rem)] rounded-xs border border-outline-variant bg-transparent px-2 body-medium text-on-surface outline-none focus:border-primary" placeholder={ongroupschange ? 'Find someone or a group' : 'Find someone'} bind:value={filter} />
					<ul class="max-h-64 overflow-y-auto">
						{#each shown as u (u.id)}
							<li>
								<button type="button" role="menuitemcheckbox" aria-checked={value.includes(u.id)} class="state-layer flex w-full items-center gap-2 px-3 py-1.5 text-left pointer-coarse:py-3" onclick={() => toggle(u.id)}>
									<Avatar name={u.display_name} src={u.avatar_url} size={24} />
									<span class="min-w-0 flex-1 truncate body-medium text-on-surface"><PersonName name={u.display_name} />{u.id === myId ? ' (you)' : ''}</span>
									{#if value.includes(u.id)}<Icon name="check" size={18} class="text-primary" />{/if}
								</button>
							</li>
						{/each}
						{#if shownGroups.length}
							<li class="px-3 pt-2 pb-1 label-small text-on-surface-variant" role="presentation">Groups</li>
							{#each shownGroups as g (g.id)}
								<li>
									<button type="button" role="menuitemcheckbox" aria-checked={groups.includes(g.id)} class="state-layer flex w-full items-center gap-2 px-3 py-1.5 text-left pointer-coarse:py-3" onclick={() => toggleGroup(g.id)}>
										<span class="flex size-6 shrink-0 items-center justify-center rounded-full bg-secondary-container text-on-secondary-container"><Icon name="groups" size={16} /></span>
										<span class="flex min-w-0 flex-1 flex-col"><span class="truncate body-medium text-on-surface">{g.name}</span><span class="truncate body-small text-on-surface-variant">@{g.slug} · {inGroup(g.member_ids.length)}</span></span>
										{#if groups.includes(g.id)}<Icon name="check" size={18} class="text-primary" />{/if}
									</button>
								</li>
							{/each}
						{/if}
						{#if !shown.length && !shownGroups.length}
							<li class="px-3 py-2 body-small text-on-surface-variant">Nobody matches.</li>
						{/if}
					</ul>
				</div>
			</Menu>
		{/if}
	</div>
	{#if assigned.length || assignedGroups.length}
		<ul class="flex flex-wrap gap-1.5">
			{#each assigned as u (u.id)}
				<li class="flex h-8 items-center gap-1.5 rounded-full bg-surface-container-high py-1 pr-1 pl-1 {editable ? '' : 'pr-3'}">
					<Avatar name={u.display_name} src={u.avatar_url} size={24} />
					{#if u.username}<a href="/people/{u.username}" class="max-w-36 truncate label-large text-on-surface hover:underline"><PersonName name={u.display_name} /></a>{:else}<span class="max-w-36 truncate label-large text-on-surface"><PersonName name={u.display_name} /></span>{/if}
					{#if editable}
						<button type="button" class="state-layer touch-target flex size-6 items-center justify-center rounded-full text-on-surface-variant" aria-label="Unassign {u.display_name}" title="Unassign" onclick={() => toggle(u.id)}><Icon name="close" size={16} /></button>
					{/if}
				</li>
			{/each}
			{#each assignedGroups as g (g.id)}
				<li class="flex h-8 items-center gap-1.5 rounded-full bg-surface-container-high py-1 pr-1 pl-1 {editable && ongroupschange ? '' : 'pr-3'}" title="{g.name}: {inGroup(g.member_ids.length)}, as far as they can see the card">
					<span class="flex size-6 items-center justify-center rounded-full bg-secondary-container text-on-secondary-container"><Icon name="groups" size={16} /></span>
					<span class="max-w-36 truncate label-large text-on-surface">@{g.slug}</span>
					{#if editable && ongroupschange}
						<button type="button" class="state-layer touch-target flex size-6 items-center justify-center rounded-full text-on-surface-variant" aria-label="Unassign @{g.slug}" title="Unassign" onclick={() => toggleGroup(g.id)}><Icon name="close" size={16} /></button>
					{/if}
				</li>
			{/each}
		</ul>
	{:else}
		<p class="body-small text-on-surface-variant">Nobody yet.</p>
	{/if}
</div>
