<script lang="ts">
	import type { PersonBrief } from '$lib/api';
	import { Avatar, Icon, TopAppBar } from '$lib/m3';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { ROLE_LABELS, me } from '$lib/session';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let people = $state<PersonBrief[]>(data.people);
	// svelte-ignore state_referenced_locally
	let loaded = data.people;
	$effect(() => {
		if (data.people !== loaded) {
			loaded = data.people;
			people = data.people;
		}
	});

	let filter = $state('');
	let sort = $state<'name' | 'load' | 'recent'>('name');
	let shown = $derived(
		people
			.filter((p) => {
				const q = filter.trim().toLowerCase();
				return !q || p.display_name.toLowerCase().includes(q) || p.username.toLowerCase().includes(q);
			})
			.sort((a, b) =>
				sort === 'load'
					? b.open_assigned - a.open_assigned || a.display_name.localeCompare(b.display_name)
					: sort === 'recent'
						? (b.last_active ?? '').localeCompare(a.last_active ?? '')
						: a.display_name.localeCompare(b.display_name)
			)
	);
	let myId = $derived(me()?.id);
</script>

<svelte:head><title>People · Castor</title></svelte:head>

<TopAppBar title="People" subtitle="{people.length} {people.length === 1 ? 'person' : 'people'} · open cards and activity in the projects you can see">
	{#snippet actions()}
		<div class="mr-2 hidden items-center rounded-full border border-outline-variant p-0.5 md:flex" role="group" aria-label="Sort">
			{#each [{ id: 'name', label: 'Name' }, { id: 'load', label: 'Most assigned' }, { id: 'recent', label: 'Recently active' }] as s (s.id)}
				<button type="button" class="rounded-full px-3 py-1 label-large {sort === s.id ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface-variant'}" aria-pressed={sort === s.id} onclick={() => (sort = s.id as typeof sort)}>{s.label}</button>
			{/each}
		</div>
		<label class="hidden items-center gap-2 rounded-full bg-surface-container-high px-4 md:flex">
			<Icon name="search" size={20} class="text-on-surface-variant" />
			<input class="h-10 w-44 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter people" bind:value={filter} autocomplete="off" />
		</label>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-8">
	<div class="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3 pt-2">
		{#each shown as p (p.id)}
			<a href="/people/{p.username}" class="state-layer flex items-center gap-3 rounded-lg bg-surface-container-low p-4">
				<Avatar name={p.display_name} src={p.avatar_url} size={48} />
				<div class="min-w-0 flex-1">
					<p class="truncate title-medium text-on-surface">{p.display_name}{p.id === myId ? ' (you)' : ''}</p>
					<p class="truncate body-small text-on-surface-variant">@{p.username} · {ROLE_LABELS[p.role].label}</p>
					<p class="mt-1 flex items-center gap-3 body-small text-on-surface-variant">
						<span class="flex items-center gap-1 {p.open_assigned ? 'text-on-surface' : ''}" title="Open cards assigned to them"><Icon name="assignment_ind" size={16} />{p.open_assigned}</span>
						<span class="flex items-center gap-1 truncate" title={p.last_active ? absoluteTime(p.last_active) : 'No activity in projects you can see'}><Icon name="schedule" size={16} />{p.last_active ? relativeTime(p.last_active) : 'not yet'}</span>
					</p>
				</div>
			</a>
		{:else}
			<p class="body-medium text-on-surface-variant">Nobody matches.</p>
		{/each}
	</div>
</div>
