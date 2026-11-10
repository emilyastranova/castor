<script lang="ts">
	import type { DashboardSummary, FeedItem, FeedKind } from '$lib/api';
	import { api } from '$lib/api';
	import { Button, Icon } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { notifications } from '$lib/notifications.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { me, isAdmin, canEdit } from '$lib/session';
	import { page } from '$app/state';
	import { snackbar } from '$lib/snackbar.svelte';
	import PostComposer from './PostComposer.svelte';
	import { fade } from 'svelte/transition';
	import TimelineItem from './TimelineItem.svelte';
	import ProjectNotes from './ProjectNotes.svelte';
	import { TRIVIA } from './trivia';
	import { enter, leave } from '$lib/motion';

	/** A project's home: a greeting with the headline numbers, what needs attention, what's
	 *  running now, and a timeline of everything that happened in the project. */
	interface Props {
		projectId: string;
	}
	let { projectId }: Props = $props();
	let scope = $derived(projectId);

	const ALL_KINDS: { kind: FeedKind; label: string; icon: string }[] = [
		{ kind: 'comment', label: 'Comments', icon: 'chat_bubble' },
		{ kind: 'job', label: 'Jobs', icon: 'terminal' },
		{ kind: 'card', label: 'Cards', icon: 'note_stack_add' },
		{ kind: 'move', label: 'Moves', icon: 'move_item' },
		{ kind: 'board', label: 'Board', icon: 'view_kanban' },
		{ kind: 'playbook', label: 'Playbooks', icon: 'account_tree' },
		{ kind: 'library', label: 'Library', icon: 'construction' },
		{ kind: 'file', label: 'Files', icon: 'attach_file' },
		{ kind: 'note', label: 'Notes', icon: 'push_pin' },
		{ kind: 'asset', label: 'Assets', icon: 'radar' },
		{ kind: 'agent', label: 'Agents', icon: 'dns' },
		{ kind: 'admin', label: 'Admin', icon: 'shield_person' }
	];
	let kindOptions = $derived(ALL_KINDS.filter((k) => k.kind !== 'admin' || isAdmin()));
	/** What the timeline shows by default: the work, not note edits, agent connections or admin. */
	const WORK: FeedKind[] = ['comment', 'job', 'card', 'playbook', 'library', 'file', 'asset'];
	let kinds = $state<Set<FeedKind>>(new Set(WORK));
	/** "Mine": only what's about you (your cards, mentions, replies). */
	let mine = $state(false);

	// The timeline filters are remembered per browser (the same for home and every project).
	// They're read before the first load, so the page doesn't load twice.
	const PREFS = 'castor.dashboard.filters';
	let prefsRead = $state(false);
	$effect(() => {
		try {
			const saved = JSON.parse(localStorage.getItem(PREFS) ?? 'null') as { kinds?: string[]; known?: string[]; mine?: boolean } | null;
			if (saved?.kinds) {
				// Kinds added since the filters were saved start as their default.
				const known = saved.known ?? ['comment', 'job', 'card', 'move', 'file', 'playbook', 'note', 'asset', 'agent', 'admin'];
				const added = WORK.filter((k) => !known.includes(k));
				kinds = new Set([...saved.kinds.filter((k): k is FeedKind => ALL_KINDS.some((x) => x.kind === k)), ...added]);
			}
			if (saved?.mine !== undefined) mine = !!saved.mine;
			else mine = localStorage.getItem('castor.dashboard.mine') === '1'; // before filters were saved
		} catch {
			/* storage unavailable, or a bad value: use the defaults */
		}
		prefsRead = true;
	});
	function savePrefs() {
		try {
			localStorage.setItem(PREFS, JSON.stringify({ kinds: [...kinds], known: ALL_KINDS.map((k) => k.kind), mine }));
		} catch {
			/* storage unavailable */
		}
	}
	function setKinds(next: Set<FeedKind>) {
		kinds = next;
		savePrefs();
	}
	function toggleMine() {
		mine = !mine;
		savePrefs();
	}
	let isDefault = $derived(kinds.size === WORK.length && WORK.every((k) => kinds.has(k)));

	/** Only the posts with this #hashtag (?tag=, from clicking one). */
	let tag = $derived(page.url.searchParams.get('tag'));
	function tagHref(t: string | null) {
		const url = new URL(page.url);
		if (t) url.searchParams.set('tag', t);
		else url.searchParams.delete('tag');
		url.searchParams.delete('post');
		return `${url.pathname}${url.search}`;
	}
	/** The hashtags the project's posts use, most used first. */
	let postTags = $state<{ tag: string; count: number }[]>([]);
	async function loadPostTags() {
		try {
			postTags = await api().posts.tags(projectId);
		} catch {
			/* keep the last ones */
		}
	}
	$effect(() => {
		void projectId;
		void loadPostTags();
	});

	let summary = $state<DashboardSummary | null>(null);
	let items = $state<FeedItem[]>([]);
	/** The newest comment on a card that's showing: the Companion sits on it, for people who turned her on. */
	let perchId = $derived(!me()?.companion ? null : items.find((i) => i.kind === 'comment' && i.comment && i.type === 'comment.created')?.id ?? null);
	let nextBefore = $state<number | null>(null);
	let loadingMore = $state(false);
	let loaded = $state(false);
	/** Animate timeline changes: only events arriving live (not a page or filter (re)load). */
	let liveFeed = $state(false);

	async function loadSummary() {
		try {
			summary = await api().dashboard.summary(scope);
		} catch {
			/* keep the last one */
		}
	}
	async function loadFeed() {
		const res = await api().dashboard.feed({ project_id: scope, kinds: [...kinds], mine, tag, limit: 40 });
		liveFeed = false;
		items = res.items;
		nextBefore = res.next_before;
		loaded = true;
	}
	/** New events: fetch the newest page and add what isn't shown yet. */
	async function refreshHead() {
		const res = await api().dashboard.feed({ project_id: scope, kinds: [...kinds], mine, tag, limit: 40 });
		const known = new Set(items.map((i) => i.id));
		const fresh = res.items.filter((i) => !known.has(i.id));
		if (!fresh.length) return;
		liveFeed = true;
		items = [...fresh, ...items];
	}
	async function more() {
		if (!nextBefore) return;
		loadingMore = true;
		try {
			const res = await api().dashboard.feed({ project_id: scope, kinds: [...kinds], mine, tag, before: nextBefore, limit: 40 });
			liveFeed = true;
			items = [...items, ...res.items];
			nextBefore = res.next_before;
		} finally {
			loadingMore = false;
		}
	}

	// (Re)load when the scope or the filters change.
	$effect(() => {
		if (!prefsRead) return;
		void scope;
		void kinds;
		void mine;
		void tag;
		loaded = false;
		void loadSummary();
		void loadFeed();
	});

	// Live: new activity and changing jobs/runs/agents refresh, a little debounced.
	let headTimer: ReturnType<typeof setTimeout> | undefined;
	let summaryTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.project_id && e.project_id !== projectId) return;
			if (e.name.startsWith('post.')) void loadPostTags();
			if (e.name === 'post.updated') {
				const p = e.data as { id: string; body: string; edited_at: string | null; mentions: string[] };
				const p2 = e.data as { tags: string[] };
				items = items
					.map((i) => (i.post?.id === p.id ? { ...i, post: { ...i.post, body: p.body, edited: !!p.edited_at, mentions: p.mentions, tags: p2.tags } } : i))
					// Filtered by a tag the post no longer has: it leaves.
					.filter((i) => !tag || !i.post || i.post.tags.some((t) => t.toLowerCase() === tag.toLowerCase()));
			} else if (e.name === 'post.deleted') {
				const id = (e.data as { id: string }).id;
				items = items.filter((i) => i.post?.id !== id);
			}
			if (e.name === 'activity.created') {
				clearTimeout(headTimer);
				headTimer = setTimeout(refreshHead, 400);
			}
			if (['activity.created', 'job.updated', 'playbook_run.updated', 'agent.updated', 'task.moved', 'project.notes', 'notification.created', 'notification.read', 'assets.changed', 'asset_triggers.changed'].includes(e.name)) {
				clearTimeout(summaryTimer);
				summaryTimer = setTimeout(loadSummary, 800);
			}
		})
	);
	// Running times tick.
	let now = $state(Date.now());
	$effect(() => {
		const t = setInterval(() => (now = Date.now()), 15000);
		return () => clearInterval(t);
	});

	/** Type to post: on Home, with nothing else taking keys, a printable key starts a post. The
	 *  box is focused during the keydown, so the browser types that very character into it. */
	function typeToPost(e: KeyboardEvent) {
		if (!canEdit() || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
		if (e.key.length !== 1 || e.key === ' ') return; // letters, digits, punctuation; space scrolls
		const at = e.target as HTMLElement | null;
		if (at?.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="textbox"], [role="menu"], dialog')) return;
		if (document.querySelector('dialog[open]')) return; // a card, a note or a prompt is open
		const box = document.querySelector<HTMLTextAreaElement>('textarea[data-timeline-composer]');
		if (!box) return;
		box.focus({ preventScroll: true });
		box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
	}

	async function post(body: string) {
		await api().posts.create(projectId, body);
		if (!kinds.has('comment') && !tag) snackbar.show('Posted. Comments are hidden by your timeline filters, so it isn\'t shown.');
	}

	// A link to a post (?post=<id>, from a mention): scroll to it once it's loaded.
	let shownPost = '';
	$effect(() => {
		const id = page.url.searchParams.get('post');
		if (!id || id === shownPost || !loaded) return;
		const el = document.getElementById(`post-${id}`);
		if (!el) return;
		shownPost = id;
		el.scrollIntoView({ block: 'center', behavior: 'smooth' });
		el.classList.add('post-flash');
		setTimeout(() => el.classList.remove('post-flash'), 2200);
	});

	function toggleKind(kind: FeedKind) {
		const next = new Set(kinds);
		if (next.has(kind)) next.delete(kind);
		else next.add(kind);
		setKinds(next);
	}

	// ---- greeting ----
	let user = $derived(me());
	let firstName = $derived((user?.display_name || user?.username || '').split(/\s+/)[0]);
	let greeting = $derived.by(() => {
		const h = new Date(now).getHours();
		return h < 5 ? 'Working late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
	});
	let today = $derived(new Date(now).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' }));
	// ---- headline: rotates between what's moving, what's yours and a random fact ----
	// (Discoveries join it with Phase 3f, mentions with 3b.)
	const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
	let moving = $derived.by(() => {
		if (!summary) return '';
		const queued = summary.live_jobs.filter((j) => j.status === 'queued');
		const running = summary.live_jobs.length - queued.length;
		if (!summary.live_jobs.length) {
			const idle = summary.agents.online;
			return idle ? `Nothing running · ${plural(idle, 'agent')} online and ready` : 'Nothing running · no agents online';
		}
		const parts = [];
		if (running) parts.push(`${plural(running, 'job')} running`);
		if (queued.length) parts.push(`${queued.length} queued`, `next up: ${queued[0].task.name}`);
		return parts.join(' · ');
	});
	let yours = $derived.by(() => {
		if (!summary) return '';
		const parts = [];
		if (summary.mine.assigned) parts.push(`${plural(summary.mine.assigned, 'card')} assigned to you`);
		if (summary.mine.mentions) parts.push(plural(summary.mine.mentions, 'unread mention'));
		if (summary.mine.opened) parts.push(`${plural(summary.mine.opened, 'open card')} you created`);
		if (notifications.unread) parts.push(plural(notifications.unread, 'unread notification'));
		return parts.length ? parts.join(' · ') : 'Nothing on your plate right now';
	});
	/** What scans found in the last 24 hours ("Found 12 hosts and 3 services today"). */
	const TYPE_WORDS: Record<string, [string, string]> = {
		host: ['host', 'hosts'], service: ['service', 'services'], url: ['URL', 'URLs'], domain: ['domain', 'domains'],
		user: ['user', 'users'], credential: ['credential', 'credentials'], share: ['share', 'shares']
	};
	let found = $derived.by(() => {
		const parts = Object.entries(summary?.found ?? {})
			.filter(([, n]) => n)
			.sort((a, b) => b[1] - a[1])
			.slice(0, 3)
			.map(([t, n]) => `${n} ${(TYPE_WORDS[t] ?? [t, t])[n === 1 ? 0 : 1]}`);
		return parts.length ? `Found ${parts.slice(0, -1).join(', ')}${parts.length > 1 ? ' and ' : ''}${parts.at(-1)} today` : '';
	});
	let fact = $state('');
	function pickFact() {
		// Facts about this workspace come up as often as general trivia.
		const own = summary?.facts ?? [];
		const pool = own.length && Math.random() < 0.5 ? own : TRIVIA;
		let next = pool[Math.floor(Math.random() * pool.length)];
		if (next === fact && pool.length > 1) next = pool[(pool.indexOf(next) + 1) % pool.length];
		fact = next;
	}
	let slides = $derived(
		summary
			? [
					{ key: 'moving', icon: 'play_circle', text: moving },
					{ key: 'yours', icon: 'person', text: yours },
					{ key: 'found', icon: 'radar', text: found },
					{ key: 'fact', icon: 'lightbulb', text: fact }
				].filter((s) => s.text)
			: []
	);
	let slideIndex = $state(0);
	let headlinePaused = $state(false);
	let slide = $derived(slides.length ? slides[slideIndex % slides.length] : null);
	function nextSlide() {
		slideIndex = (slideIndex + 1) % Math.max(slides.length, 1);
		if (slides[slideIndex]?.key === 'fact') pickFact();
	}
	$effect(() => {
		if (headlinePaused) return;
		// Not while the tab is hidden: its fades don't run, so lines would pile up until it's shown.
		const t = setInterval(() => !document.hidden && nextSlide(), 8000);
		return () => clearInterval(t);
	});
	$effect(() => {
		if (summary && !fact) pickFact();
	});

	// In Mine, Needs attention keeps only what's for you (your failed cards, and decisions).
	let attention = $derived((summary?.attention ?? []).filter((a) => !mine || a.for_you));

	// ---- timeline grouped by day ----
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

	const elapsed = (iso: string | null) => {
		if (!iso) return '';
		const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
		return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
	};
	let stats = $derived(
		summary
			? [
					{ label: 'Open cards', value: summary.counts.open_cards, icon: 'view_kanban' },
					{ label: 'Comments today', value: summary.counts.comments_24h, icon: 'chat_bubble' },
					{ label: 'New cards today', value: summary.counts.cards_24h, icon: 'note_stack_add' },
					{ label: 'Jobs succeeded', value: summary.counts.jobs_succeeded_24h, icon: 'check_circle' },
					{ label: 'Jobs failed', value: summary.counts.jobs_failed_24h, icon: 'error', bad: summary.counts.jobs_failed_24h > 0 }
				]
			: []
	);
</script>

<svelte:window onkeydown={typeToPost} />

<div class="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 pt-4 pb-10 sm:px-6 sm:pt-6">
	<!-- greeting -->
	<header class="flex flex-wrap items-end justify-between gap-4">
		<div>
			<p class="label-large text-on-surface-variant">{today}</p>
			<h1 class="display-small text-on-surface">{greeting}{firstName ? `, ${firstName}` : ''}</h1>
			<!-- Hover (or focus) holds the current line; a click moves on. -->
			<button
				type="button"
				class="mt-1 grid min-h-7 max-w-full text-left body-large text-on-surface-variant"
				title="Click for the next one"
				onclick={nextSlide}
				onmouseenter={() => (headlinePaused = true)}
				onmouseleave={() => (headlinePaused = false)}
				onfocus={() => (headlinePaused = true)}
				onblur={() => (headlinePaused = false)}
				aria-live="polite"
			>
				{#if slide}
					{#key slide.key + slide.text}
						<span class="col-start-1 row-start-1 flex items-start gap-2" in:fade={{ duration: 250, delay: 150 }} out:fade={{ duration: 150 }}>
							<Icon name={slide.icon} size={20} class="mt-0.5 shrink-0 text-primary" />
							<span>{slide.text}</span>
						</span>
					{/key}
				{/if}
			</button>
		</div>
	</header>

	<ProjectNotes {projectId} />

	<!-- headline numbers (last 24 hours) -->
	<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
		{#each stats as s (s.label)}
			<div class="flex items-center gap-3 rounded-lg bg-surface-container-low px-4 py-3">
				<Icon name={s.icon} class={s.bad ? 'text-error' : 'text-on-surface-variant'} />
				<div class="min-w-0">
					<p class="headline-small leading-none {s.bad ? 'text-error' : 'text-on-surface'}">{s.value}</p>
					<p class="mt-1 line-clamp-2 body-small leading-tight text-on-surface-variant">{s.label}</p>
				</div>
			</div>
		{/each}
	</div>

	<div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
		<!-- timeline -->
		<section class="min-w-0">
			<h2 class="mb-2 title-large text-on-surface sm:hidden">What's been happening</h2>
			<div class="mb-3 flex items-center gap-2 max-sm:-mx-4 max-sm:overflow-x-auto max-sm:px-4 max-sm:[scrollbar-width:none] sm:flex-wrap">
				<h2 class="mr-2 title-large text-on-surface max-sm:hidden">What's been happening</h2>
				<button
					type="button"
					class="touch-target flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 label-medium transition-colors {mine
						? 'border-transparent bg-primary text-on-primary'
						: 'border-outline-variant text-on-surface-variant hover:bg-surface-container-high'}"
					aria-pressed={mine}
					title="Only what's about you: your cards, mentions and replies"
					onclick={toggleMine}
				>
					<Icon name={mine ? 'check' : 'person'} size={14} />Mine
				</button>
				<span class="h-5 w-px shrink-0 bg-outline-variant"></span>
				{#each kindOptions as k (k.kind)}
					<button
						type="button"
						class="touch-target flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 label-medium whitespace-nowrap transition-colors disabled:opacity-40 {kinds.has(k.kind)
							? 'border-transparent bg-secondary-container text-on-secondary-container'
							: 'border-outline-variant text-on-surface-variant hover:bg-surface-container-high'}"
						aria-pressed={kinds.has(k.kind)}
						disabled={!!tag}
						title={tag ? 'Filtering by a hashtag shows only posts' : undefined}
						onclick={() => toggleKind(k.kind)}
					>
						<Icon name={kinds.has(k.kind) ? 'check' : k.icon} size={14} />{k.label}
					</button>
				{/each}
				{#if isDefault}
					<button type="button" class="touch-target shrink-0 label-medium whitespace-nowrap text-primary hover:underline" onclick={() => setKinds(new Set(kindOptions.map((k) => k.kind)))}>Show everything</button>
				{:else}
					<button type="button" class="touch-target shrink-0 label-medium whitespace-nowrap text-primary hover:underline" onclick={() => setKinds(new Set(WORK))}>Just the work</button>
				{/if}
			</div>
			{#if canEdit()}
				<div class="mb-3"><PostComposer {projectId} onsubmit={post} /></div>
			{/if}
			{#if tag}
				<div class="mb-3 flex flex-wrap items-center gap-2 rounded-md bg-secondary-container/50 px-3 py-2">
					<span class="body-medium text-on-surface">Posts tagged</span>
					<a href={tagHref(null)} class="state-layer flex items-center gap-1 rounded-full bg-secondary-container py-0.5 pr-1.5 pl-2.5 label-large text-on-secondary-container" title="Show the whole timeline" aria-label="Stop filtering by #{tag}">#{tag}<Icon name="close" size={16} /></a>
					<a href={tagHref(null)} class="ml-auto label-medium text-primary hover:underline">Whole timeline</a>
				</div>
			{:else if postTags.length}
				<div class="mb-3 flex items-center gap-1.5 max-sm:-mx-4 max-sm:overflow-x-auto max-sm:px-4 max-sm:[scrollbar-width:none] sm:flex-wrap" aria-label="Hashtags in posts">
					<Icon name="tag" size={16} class="shrink-0 text-on-surface-variant" />
					{#each postTags.slice(0, 12) as t (t.tag)}
						<a href={tagHref(t.tag)} class="hashtag shrink-0" title="{t.count} post{t.count === 1 ? '' : 's'}">#{t.tag}</a>
					{/each}
				</div>
			{/if}
			{#if !loaded}
				<p class="py-10 text-center body-medium text-on-surface-variant">Loading…</p>
			{:else if !items.length}
				<div class="flex flex-col items-center gap-2 rounded-lg bg-surface-container-low py-12 text-center text-on-surface-variant">
					<Icon name="history" size={36} />
					<p class="body-medium">{tag ? `No posts tagged #${tag}.` : `Nothing here yet${isDefault ? '' : ' for these filters'}.`}</p>
				</div>
			{:else}
				{#each days as day (day.label)}
					<div in:enter={{ enabled: liveFeed }} out:leave={{ enabled: liveFeed }}>
						<h3 class="sticky top-0 z-10 -mx-1 mb-2 bg-surface px-1 py-1 title-small text-on-surface-variant">{day.label}</h3>
						<ol>
							{#each day.items as item (item.id)}
								<li id={item.post ? `post-${item.post.id}` : undefined} class="rounded-md" in:enter={{ enabled: liveFeed }} out:leave={{ enabled: liveFeed }}><TimelineItem {item} showProject={false} activeTag={tag} perched={item.id === perchId} /></li>
							{/each}
						</ol>
					</div>
				{/each}
				{#if nextBefore}
					<div class="flex justify-center"><Button variant="tonal" loading={loadingMore} onclick={more}>Show older</Button></div>
				{/if}
			{/if}
		</section>

		<!-- side panels -->
		<aside class="flex flex-col gap-4">
			<section class="rounded-lg bg-surface-container-low p-4">
				<div class="mb-2 flex items-center gap-2">
					<Icon name="priority_high" class="text-tertiary" />
					<h2 class="flex-1 title-medium text-on-surface">Needs attention</h2>
					{#if notifications.unread}<span class="body-small text-on-surface-variant">{notifications.unread} unread notification{notifications.unread === 1 ? '' : 's'}</span>{/if}
				</div>
				{#if attention.length}
					<ul class="flex flex-col gap-1.5">
						{#each attention as a (`${a.kind}:${a.link}:${a.title}`)}
							<li in:enter out:leave>
								<a href={a.link} class="state-layer flex items-start gap-2.5 rounded-md px-2 py-2" onclick={() => a.notification_id && void api().notifications.read(a.notification_id).catch(() => {})}>
									<Icon name={a.kind === 'mention' ? 'alternate_email' : a.kind === 'decision' ? 'front_hand' : a.kind === 'approval' ? 'bolt' : a.kind === 'input' ? 'keyboard' : 'error'} size={18} class="mt-0.5 shrink-0 {a.kind === 'mention' ? 'text-primary' : a.kind === 'decision' || a.kind === 'approval' || a.kind === 'input' ? 'text-tertiary' : 'text-error'}" />
									<span class="min-w-0 flex-1">
										<span class="block body-medium text-on-surface">{a.title}{#if a.for_you && a.kind === 'failed'}<span class="ml-1.5 rounded-full bg-primary-container px-1.5 py-0.5 align-middle label-small text-on-primary-container">for you</span>{/if}</span>
										<span class="block truncate body-small text-on-surface-variant" title={a.detail}>
											{a.detail}{a.since ? ` · ${relativeTime(a.since)}` : ''}
										</span>
									</span>
								</a>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="flex items-center gap-2 px-2 py-2 body-medium text-on-surface-variant"><Icon name="check_circle" size={18} class="text-primary" />Nothing waiting on you.</p>
				{/if}
			</section>

			<section class="rounded-lg bg-surface-container-low p-4">
				<div class="mb-2 flex items-center gap-2">
					<Icon name="bolt" class="text-primary" />
					<h2 class="flex-1 title-medium text-on-surface">Live now</h2>
					{#if summary}
						<a href="/agents" class="flex items-center gap-1 body-small text-on-surface-variant hover:underline" title="{summary.agents.busy} of {summary.agents.slots} job slots busy">
							<Icon name={summary.agents.online ? 'dns' : 'cloud_off'} size={14} />{summary.agents.online} agent{summary.agents.online === 1 ? '' : 's'} · {summary.agents.busy}/{summary.agents.slots} slots
						</a>
					{/if}
				</div>
				{#if summary?.live_runs.length}
					<p class="mt-1 mb-1 px-2 label-medium text-on-surface-variant">Playbooks</p>
					<ul class="flex flex-col gap-1">
						{#each summary.live_runs as r (r.run_id)}
							<li in:enter out:leave>
								<a href="/playbooks?run={r.run_id}" class="state-layer block rounded-md px-2 py-2">
									<span class="flex items-center gap-2">
										<Icon name="account_tree" size={16} class="text-primary" />
										<span class="min-w-0 flex-1 truncate body-medium text-on-surface">{r.name}</span>
										<span class="shrink-0 body-small text-on-surface-variant">{r.done}/{r.total}</span>
									</span>
									<span class="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-surface-container-highest">
										<span class="block h-full rounded-full bg-primary transition-all" style:width="{r.total ? (100 * r.done) / r.total : 0}%"></span>
									</span>
									<span class="mt-1 block body-small text-on-surface-variant">
										{r.running} running{r.waiting ? ` · ${r.waiting} waiting for you` : ''} · started {relativeTime(r.started_at)}
									</span>
								</a>
							</li>
						{/each}
					</ul>
				{/if}
				{#if summary?.live_jobs.length}
					<p class="mt-2 mb-1 px-2 label-medium text-on-surface-variant">Jobs</p>
					<ul class="flex flex-col gap-0.5">
						{#each summary.live_jobs as j (j.job_id)}
							<li in:enter out:leave>
								<a href={j.project ? `/projects/${j.project.id}?task=${j.task.id}` : '#'} class="state-layer flex items-center gap-2 rounded-md px-2 py-1.5">
									{#if j.status === 'running'}
										<span class="size-2 shrink-0 animate-pulse rounded-full bg-primary"></span>
									{:else}
										<Icon name="hourglass_empty" size={14} class="shrink-0 text-tertiary" />
									{/if}
									<span class="min-w-0 flex-1 truncate body-medium text-on-surface" title={j.task.name}>{j.task.name}</span>
									<span class="shrink-0 body-small text-on-surface-variant" title={absoluteTime(j.started_at ?? j.queued_at)}>
										{j.status === 'running' ? `${elapsed(j.started_at)}${j.agent ? ` · ${j.agent}` : ''}` : 'queued'}
									</span>
								</a>
							</li>
						{/each}
					</ul>
				{/if}
				{#if summary && !summary.live_runs.length && !summary.live_jobs.length}
					<p class="px-2 py-2 body-medium text-on-surface-variant">Nothing running.</p>
				{/if}
			</section>
		</aside>
	</div>
</div>
