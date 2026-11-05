<script lang="ts">
	import type { FeedItem, Person } from '$lib/api';
	import { api } from '$lib/api';
	import { Avatar, Button, Icon, IconButton, TopAppBar } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { ROLE_LABELS, isAdmin, me } from '$lib/session';
	import { enter, leave } from '$lib/motion';
	import TimelineItem from '$lib/components/dashboard/TimelineItem.svelte';
	import ColumnGlyph from '$lib/components/ColumnGlyph.svelte';
	import PriorityGlyph from '$lib/components/PriorityGlyph.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let person = $state<Person>(data.person);
	// svelte-ignore state_referenced_locally
	let loaded = data.person;
	$effect(() => {
		if (data.person !== loaded) {
			loaded = data.person;
			person = data.person;
		}
	});
	let isMe = $derived(me()?.id === person.id);

	// ---- what they did: the dashboard feed, filtered to them ----
	let items = $state<FeedItem[]>([]);
	let nextBefore = $state<number | null>(null);
	let feedLoaded = $state(false);
	let loadingMore = $state(false);
	let live = $state(false);
	async function loadFeed() {
		const res = await api().dashboard.feed({ actor: person.id, limit: 30 });
		live = false;
		items = res.items;
		nextBefore = res.next_before;
		feedLoaded = true;
	}
	async function more() {
		if (!nextBefore) return;
		loadingMore = true;
		try {
			const res = await api().dashboard.feed({ actor: person.id, before: nextBefore, limit: 30 });
			live = true;
			items = [...items, ...res.items];
			nextBefore = res.next_before;
		} finally {
			loadingMore = false;
		}
	}
	$effect(() => {
		void person.id;
		feedLoaded = false;
		void loadFeed();
	});
	// New things they do appear live; the side panels refresh with them.
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name !== 'activity.created' && e.name !== 'task.updated' && e.name !== 'comment.created') return;
			clearTimeout(timer);
			timer = setTimeout(async () => {
				const [res, fresh] = await Promise.all([api().dashboard.feed({ actor: person.id, limit: 30 }), api().people.get(person.username)]);
				const known = new Set(items.map((i) => i.id));
				const added = res.items.filter((i) => !known.has(i.id));
				if (added.length) {
					live = true;
					items = [...added, ...items];
				}
				person = fresh;
			}, 600);
		})
	);

	let days = $derived.by(() => {
		const groups: { label: string; items: FeedItem[] }[] = [];
		const todayKey = new Date().toDateString();
		const yesterdayKey = new Date(Date.now() - 864e5).toDateString();
		for (const item of items) {
			const d = new Date(item.created_at);
			const key = d.toDateString();
			const label = key === todayKey ? 'Today' : key === yesterdayKey ? 'Yesterday' : d.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' });
			if (groups.at(-1)?.label !== label) groups.push({ label, items: [] });
			groups.at(-1)!.items.push(item);
		}
		return groups;
	});

	let stats = $derived([
		{ label: 'Open cards assigned', value: person.counts.open_assigned, icon: 'assignment_ind' },
		{ label: 'Comments (30 days)', value: person.counts.comments_30d, icon: 'chat_bubble' },
		{ label: 'Cards created (30 days)', value: person.counts.cards_30d, icon: 'note_stack_add' },
		{ label: 'Runs started (30 days)', value: person.counts.runs_30d, icon: 'terminal' }
	]);
	const STATUS: Record<string, string> = {
		succeeded: 'text-primary',
		failed: 'text-error',
		lost: 'text-error',
		running: 'text-tertiary',
		queued: 'text-on-surface-variant',
		dispatched: 'text-on-surface-variant',
		cancelled: 'text-on-surface-variant'
	};
</script>

<svelte:head><title>{person.display_name} · Castor</title></svelte:head>

<TopAppBar>
	<div class="flex items-center gap-3">
		<IconButton icon="arrow_back" label="People" href="/people" />
		<p class="title-large text-on-surface">People</p>
	</div>
	{#snippet actions()}
		{#if isAdmin()}<Button variant="text" icon="manage_accounts" href="/settings/users">Manage users</Button>{/if}
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto">
	<div class="mx-auto flex w-full max-w-7xl flex-col gap-6 px-6 pt-2 pb-10">
		<header class="flex flex-wrap items-center gap-5">
			<Avatar name={person.display_name} src={person.avatar_url} size={88} />
			<div class="min-w-0 flex-1">
				<h1 class="display-small text-on-surface">{person.display_name}{isMe ? ' (you)' : ''}</h1>
				<p class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 body-large text-on-surface-variant">
					<span>@{person.username}</span>
					<span class="flex items-center gap-1"><Icon name={ROLE_LABELS[person.role].icon} size={18} />{ROLE_LABELS[person.role].label}</span>
					<span title={person.last_active ? absoluteTime(person.last_active) : ''}>{person.last_active ? `Active ${relativeTime(person.last_active)}` : 'No activity yet in projects you can see'}</span>
					<span class="body-medium">Joined {new Date(person.created_at).toLocaleDateString([], { month: 'short', year: 'numeric' })}</span>
				</p>
				{#if person.projects.length}
					<div class="mt-3 flex flex-wrap gap-1.5">
						{#each person.projects as p (p.id)}
							<a href="/projects/{p.id}" class="state-layer rounded-full border border-outline-variant px-2.5 py-0.5 body-small text-on-surface-variant" title={p.name}><span class="font-mono">{p.codename}</span> · {p.name}</a>
						{/each}
					</div>
				{/if}
			</div>
		</header>

		<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
			{#each stats as s (s.label)}
				<div class="flex items-center gap-3 rounded-lg bg-surface-container-low px-4 py-3">
					<Icon name={s.icon} class="text-on-surface-variant" />
					<div class="min-w-0">
						<p class="headline-small leading-none text-on-surface">{s.value}</p>
						<p class="mt-1 truncate body-small text-on-surface-variant">{s.label}</p>
					</div>
				</div>
			{/each}
		</div>

		<div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
			<section class="min-w-0">
				<h2 class="mb-3 title-large text-on-surface">What {isMe ? 'you' : person.display_name.split(/\s+/)[0]} did</h2>
				{#if !feedLoaded}
					<p class="body-medium text-on-surface-variant">Loading…</p>
				{:else if !items.length}
					<div class="flex flex-col items-center gap-2 py-12 text-on-surface-variant">
						<Icon name="history" size={36} />
						<p class="body-medium">Nothing yet in the projects you can see.</p>
					</div>
				{:else}
					{#each days as day (day.label)}
						<div in:enter={{ enabled: live }} out:leave={{ enabled: live }}>
							<h3 class="sticky top-0 z-10 -mx-1 mb-2 bg-surface px-1 py-1 title-small text-on-surface-variant">{day.label}</h3>
							<ol>
								{#each day.items as item (item.id)}
									<li in:enter={{ enabled: live }} out:leave={{ enabled: live }}><TimelineItem {item} /></li>
								{/each}
							</ol>
						</div>
					{/each}
					{#if nextBefore}
						<div class="flex justify-center"><Button variant="tonal" loading={loadingMore} onclick={more}>Show older</Button></div>
					{/if}
				{/if}
			</section>

			<aside class="flex flex-col gap-4">
				<section class="rounded-lg bg-surface-container-low p-4">
					<div class="mb-2 flex items-center gap-2">
						<Icon name="assignment_ind" class="text-primary" />
						<h2 class="flex-1 title-medium text-on-surface">On {isMe ? 'your' : 'their'} plate</h2>
						<span class="body-small text-on-surface-variant">{person.assigned.length} open</span>
					</div>
					{#if person.assigned.length}
						<ul class="flex flex-col">
							{#each person.assigned as c (c.id)}
								<li in:enter out:leave>
									<a href="/projects/{c.project?.id}?task={c.id}" class="state-layer flex items-start gap-2.5 rounded-md px-2 py-2">
										<span class="mt-0.5 shrink-0"><ColumnGlyph role={c.column_role} size={16} /></span>
										<span class="min-w-0 flex-1">
											<span class="flex items-center gap-1.5 body-medium text-on-surface"><span class="truncate">{c.title}</span>{#if c.priority > 0}<PriorityGlyph level={c.priority} size={14} />{/if}</span>
											<span class="block truncate body-small text-on-surface-variant">
												{c.column}{c.status ? ' · ' : ''}{#if c.status}<span class={STATUS[c.status] ?? ''}>{c.status}</span>{/if}{c.project ? ` · ${c.project.codename}` : ''}
											</span>
										</span>
									</a>
								</li>
							{/each}
						</ul>
					{:else}
						<p class="px-2 py-1 body-medium text-on-surface-variant">Nothing assigned right now.</p>
					{/if}
				</section>

				<section class="rounded-lg bg-surface-container-low p-4">
					<div class="mb-2 flex items-center gap-2">
						<Icon name="alternate_email" class="text-primary" />
						<h2 class="flex-1 title-medium text-on-surface">Mentioned in</h2>
					</div>
					{#if person.mentions.length}
						<ul class="flex flex-col gap-1">
							{#each person.mentions as mn (mn.id)}
								<li in:enter out:leave>
									<a href={mn.link} class="state-layer flex items-start gap-2.5 rounded-md px-2 py-2">
										<Avatar name={mn.author.name} src={mn.author.avatar_url} size={24} />
										<span class="min-w-0 flex-1">
											<span class="block truncate body-small text-on-surface-variant"><span class="font-medium text-on-surface">{mn.author.name}</span> on {mn.task.title}</span>
											<span class="block line-clamp-2 body-medium text-on-surface">{mn.body}</span>
											<span class="block body-small text-on-surface-variant" title={absoluteTime(mn.created_at)}>{relativeTime(mn.created_at)}{mn.project ? ` · ${mn.project.codename}` : ''}</span>
										</span>
									</a>
								</li>
							{/each}
						</ul>
					{:else}
						<p class="px-2 py-1 body-medium text-on-surface-variant">No mentions in the projects you can see.</p>
					{/if}
				</section>
			</aside>
		</div>
	</div>
</div>
