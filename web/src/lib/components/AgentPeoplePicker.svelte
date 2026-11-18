<script lang="ts">
	import type { AgentPeople, Group, UserBrief } from '$lib/api';
	import { Avatar, Chip, Icon } from '$lib/m3';

	/** Who may run work on an agent (admins): everyone (null), or the people and groups ticked here.
	 *  Admins always may; automation counts as whoever set it up. Laid out like AgentProjectsPicker. */
	interface Props {
		value: AgentPeople | null;
		users: UserBrief[];
		groups: Group[];
	}
	let { value = $bindable(null), users, groups }: Props = $props();
	const labelId = `agent-people-${Math.random().toString(36).slice(2, 8)}`;

	let q = $state('');
	const match = (text: string) => !q.trim() || text.toLowerCase().includes(q.trim().toLowerCase());
	// Admins may use every agent already: not offered.
	let shownGroups = $derived(groups.filter((g) => match(`${g.name} ${g.slug}`)));
	let shownUsers = $derived(users.filter((u) => u.role !== 'admin' && match(`${u.display_name} ${u.username}`)));
	let count = $derived(value ? value.users.length + value.groups.length : 0);

	function toggle(kind: 'users' | 'groups', id: string) {
		if (!value) return;
		value[kind] = value[kind].includes(id) ? value[kind].filter((x) => x !== id) : [...value[kind], id];
	}
	// Ones it names that aren't in the lists (a removed account or group): kept until unticked.
	let unknown = $derived(
		value
			? [
					...value.users.filter((id) => !users.some((u) => u.id === id)).map((id) => ({ kind: 'users' as const, id })),
					...value.groups.filter((id) => !groups.some((g) => g.id === id)).map((id) => ({ kind: 'groups' as const, id }))
				]
			: []
	);
</script>

<div class="flex flex-col gap-2">
	<span class="label-large text-on-surface" id={labelId}>Who can use it</span>
	<div class="flex flex-wrap gap-2" role="radiogroup" aria-labelledby={labelId}>
		<Chip variant="filter" role="radio" aria-checked={value === null} selected={value === null} onclick={() => (value = null)}>Everyone</Chip>
		<Chip variant="filter" role="radio" aria-checked={value !== null} selected={value !== null} onclick={() => value === null && (value = { users: [], groups: [] })}>Only some people</Chip>
	</div>
	<p class="body-small text-on-surface-variant">
		{value === null
			? 'Anyone’s cards and runs can go to it.'
			: 'Only their work goes to it: what they start, and automation they set up (playbook runs, triggers). Admins always may.'}
	</p>
	{#if value !== null}
		<div class="flex flex-col gap-1 rounded-md border border-outline-variant/70 p-2">
			<div class="flex items-center gap-2 px-1">
				<span class="flex-1 label-large text-on-surface">Can use it</span>
				<span class="body-small text-on-surface-variant">{count ? `${count} chosen` : 'None: admins only'}</span>
			</div>
			{#if users.length + groups.length > 8}
				<label class="mx-1 flex items-center gap-2 rounded-full bg-surface-container-high px-3">
					<Icon name="search" size={18} class="text-on-surface-variant" />
					<input class="h-9 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Find a person or group" bind:value={q} />
				</label>
			{/if}
			<ul class="max-h-56 overflow-y-auto" role="group" aria-label="People and groups">
				{#each unknown as x (x.id)}
					<li>
						<button type="button" role="checkbox" aria-checked="true" class="state-layer flex w-full items-center gap-3 rounded-sm px-2 py-1.5 text-left" onclick={() => toggle(x.kind, x.id)}>
							<Icon name="check_box" size={20} class="text-primary" />
							<span class="min-w-0 flex-1 body-medium text-on-surface-variant">{x.kind === 'users' ? 'A removed account' : 'A removed group'}</span>
						</button>
					</li>
				{/each}
				{#each shownGroups as g (g.id)}
					{@const on = value.groups.includes(g.id)}
					<li>
						<button type="button" role="checkbox" aria-checked={on} class="state-layer flex w-full items-center gap-3 rounded-sm px-2 py-1.5 text-left" onclick={() => toggle('groups', g.id)}>
							<Icon name={on ? 'check_box' : 'check_box_outline_blank'} size={20} class={on ? 'text-primary' : 'text-on-surface-variant'} />
							<span class="flex size-6 shrink-0 items-center justify-center rounded-full bg-secondary-container text-on-secondary-container"><Icon name="group" size={16} /></span>
							<span class="min-w-0 flex-1">
								<span class="block truncate body-medium text-on-surface">{g.name}</span>
								<span class="block truncate body-small text-on-surface-variant">@{g.slug} · everyone in it</span>
							</span>
						</button>
					</li>
				{/each}
				{#each shownUsers as u (u.id)}
					{@const on = value.users.includes(u.id)}
					<li>
						<button type="button" role="checkbox" aria-checked={on} class="state-layer flex w-full items-center gap-3 rounded-sm px-2 py-1.5 text-left" onclick={() => toggle('users', u.id)}>
							<Icon name={on ? 'check_box' : 'check_box_outline_blank'} size={20} class={on ? 'text-primary' : 'text-on-surface-variant'} />
							<Avatar name={u.display_name} src={u.avatar_url} size={24} />
							<span class="min-w-0 flex-1">
								<span class="block truncate body-medium text-on-surface">{u.display_name}</span>
								<span class="block truncate body-small text-on-surface-variant">@{u.username}</span>
							</span>
						</button>
					</li>
				{/each}
				{#if !shownGroups.length && !shownUsers.length && !unknown.length}
					<li class="px-2 py-2 body-small text-on-surface-variant">{users.length + groups.length ? 'Nobody matches.' : 'No other people or groups yet.'}</li>
				{/if}
			</ul>
		</div>
	{/if}
</div>
