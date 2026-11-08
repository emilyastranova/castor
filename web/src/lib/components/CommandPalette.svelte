<script lang="ts">
	import { currentProject } from '$lib/currentProject.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { SearchGroup, SearchHit } from '$lib/api';
	import { api } from '$lib/api';
	import { Avatar, Icon } from '$lib/m3';
	import { palette } from '$lib/palette.svelte';
	import { relativeTime } from '$lib/format';
	import { isAdmin } from '$lib/session';

	let dialog = $state<HTMLDialogElement>();
	let input = $state<HTMLInputElement>();
	let listEl = $state<HTMLDivElement>();
	let query = $state('');
	let groups = $state<SearchGroup[]>([]);
	let note = $state('');
	let loading = $state(false);
	let active = $state(0);

	// A modal <dialog> sits in the top layer: above an open card too.
	$effect(() => {
		if (!dialog) return;
		if (palette.open && !dialog.open) {
			query = palette.initial;
			groups = [];
			note = '';
			active = 0;
			dialog.showModal();
			queueMicrotask(() => {
				input?.focus();
				input?.setSelectionRange(query.length, query.length);
			});
			if (query.trim()) run(query);
		} else if (!palette.open && dialog.open) dialog.close();
	});

	// ---- searching: debounced, and an older answer never replaces a newer one ----
	let seq = 0;
	let timer: ReturnType<typeof setTimeout> | undefined;
	function oninput() {
		clearTimeout(timer);
		active = 0;
		if (!query.trim()) {
			groups = [];
			note = '';
			loading = false;
			return;
		}
		loading = true;
		timer = setTimeout(() => run(query), 160);
	}
	async function run(text: string) {
		const mine = ++seq;
		loading = true;
		try {
			const res = await api().search(text);
			if (mine !== seq) return;
			groups = res.groups;
			note = res.note ?? '';
			active = 0;
		} catch {
			if (mine === seq) groups = [];
		} finally {
			if (mine === seq) loading = false;
		}
	}

	// ---- what shows: results, or (nothing typed) recent items and places ----
	/** The app's pages; `keywords` are other words that should find them. */
	type Place = SearchHit & { keywords: string; admin?: boolean };
	const place = (id: string, title: string, snippet: string, link: string, icon: string, keywords: string, admin = false): Place => ({
		id, title, snippet, link, icon, keywords, admin, project: null, at: null
	});
	// Home, Board and Assets are the current project's (currentProject).
	let PLACES = $derived<Place[]>([
		place('home', 'Home', "This project's dashboard", currentProject.home, 'space_dashboard', 'dashboard cockpit start overview timeline feed attention notes'),
		place('board', 'Board', "This project's cards", currentProject.board, 'view_kanban', 'kanban cards columns tasks'),
		place('assets', 'Assets', 'What this project found', currentProject.assets, 'radar', 'hosts services urls credentials inventory triggers scope'),
		place('projects', 'Switch project', 'Choose another project, or start one', '/projects', 'swap_horiz', 'projects switch change engagements clients new'),
		place('playbooks', 'Playbooks', 'Run or edit a playbook', '/playbooks', 'account_tree', 'pipelines workflows runs graph dag automation'),
		place('commands', 'Commands', 'The command library', '/commands', 'menu_book', 'library templates scripts'),
		place('tools', 'Tools', 'Tool environments for commands', '/tools', 'handyman', 'images containers recipes docker install'),
		place('variables', 'Variables', 'Credentials, lists and settings for commands', '/variables', 'key', 'secrets credentials vault sets kinds lists targets'),
		place('parsers', 'Parsers', 'Turn tool output into results', '/parsers', 'data_object', 'outputs results nmap ffuf netexec parse'),
		place('agents', 'Agents', 'Machines that run jobs', '/agents', 'dns', 'runners workers machines hosts tokens'),
		place('people', 'People', 'Who is on the team and what they have on', '/people', 'groups', 'team people members workload assigned'),
		place('settings', 'Settings', 'Admin settings', '/settings', 'settings', 'admin configuration preferences', true),
		place('users', 'Users', 'Settings · accounts and roles', '/settings/users', 'group', 'people accounts roles admins members permissions passwords', true),
		place('activity', 'Activity log', 'Settings · everything, across projects', '/settings/activity', 'history', 'log audit events history', true)
	]);
	let typed = $derived(query.trim().length > 0);
	let places = $derived(PLACES.filter((p) => !p.admin || isAdmin()));
	/** Pages whose name (or one of their words) starts with every word typed; filters skip them. */
	let matchingPlaces = $derived.by(() => {
		if (/(?:^|\s)(in|type|by|tag|is|status):/i.test(query)) return [];
		const typedWords = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
		if (!typedWords.length) return [];
		return places.filter((p) => {
			const own = `${p.title} ${p.keywords}`.toLowerCase().split(/\s+/);
			return typedWords.every((w) => own.some((o) => o.startsWith(w)));
		});
	});
	let sections = $derived.by((): SearchGroup[] => {
		if (typed) return [...(matchingPlaces.length ? [{ type: 'places', label: 'Pages', hits: matchingPlaces, more: false }] : []), ...groups];
		const recent = palette.open ? palette.recent() : [];
		return [
			...(recent.length ? [{ type: 'recent', label: 'Recent', hits: recent, more: false }] : []),
			{ type: 'places', label: 'Go to', hits: places, more: false }
		];
	});
	let flat = $derived(sections.flatMap((g) => g.hits.map((h) => ({ g, h }))));

	/** The project on screen, for "only this project". */
	let here = $derived(
		(page.data as { project?: { codename?: string }; board?: { project?: { codename?: string } } })?.project?.codename ??
			(page.data as { board?: { project?: { codename?: string } } })?.board?.project?.codename
	);
	const FILTERS = [
		{ text: 'type:', hint: 'card, comment, log, file…' },
		{ text: 'by:', hint: 'someone' },
		{ text: 'tag:', hint: 'a tag' },
		{ text: 'is:mine', hint: 'yours' },
		{ text: 'status:failed', hint: 'failed runs' }
	];
	function addFilter(text: string) {
		query = `${query.trimEnd()}${query.trim() ? ' ' : ''}${text}${text.endsWith(':') ? '' : ' '}`;
		input?.focus();
		oninput();
	}

	// ---- highlighting the matched words ----
	let words = $derived(
		query
			.replace(/(?:^|\s)(in|type|by|tag|is|status):\S+/gi, ' ')
			.toLowerCase()
			.split(/\s+/)
			.map((w) => w.replace(/"/g, ''))
			.filter((w) => w.length > 1)
	);
	function parts(text: string): { t: string; hit: boolean }[] {
		if (!text || !words.length) return [{ t: text, hit: false }];
		const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
		return text.split(re).filter(Boolean).map((t) => ({ t, hit: words.includes(t.toLowerCase()) }));
	}

	// ---- keyboard and opening ----
	function choose(hit: SearchHit) {
		if (!hit.link) return;
		if (typed && !matchingPlaces.includes(hit as Place)) palette.remember(hit);
		palette.hide();
		void goto(hit.link);
	}
	function move(by: number) {
		if (!flat.length) return;
		active = (active + by + flat.length) % flat.length;
		queueMicrotask(() => listEl?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' }));
	}
	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			move(1);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			move(-1);
		} else if (e.key === 'Enter') {
			e.preventDefault();
			const item = flat[active];
			if (item) choose(item.h);
		} else if (e.key === 'Escape') {
			// Handled here so the browser doesn't also close a card dialog underneath.
			e.preventDefault();
			e.stopPropagation();
			palette.hide();
		}
	}
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<dialog
	bind:this={dialog}
	class="palette m-0 mx-auto mt-[10vh] max-h-[76vh] w-[min(720px,calc(100vw-2rem))] flex-col overflow-hidden rounded-xl bg-surface-container-high p-0 text-on-surface shadow-e3 outline-none open:flex"
	aria-label="Search"
	oncancel={(e) => {
		e.preventDefault();
		palette.hide();
	}}
	onclick={(e) => e.target === dialog && palette.hide()}
	{onkeydown}
>
	{#if palette.open}
		<div class="flex shrink-0 items-center gap-3 border-b border-outline-variant px-4">
			<Icon name="search" class="text-on-surface-variant" />
			<input
				bind:this={input}
				bind:value={query}
				{oninput}
				class="h-14 min-w-0 flex-1 bg-transparent body-large text-on-surface outline-none placeholder:text-on-surface-variant"
				placeholder="Search cards, comments, output, files, playbooks…"
				aria-label="Search everything"
				aria-controls="palette-results"
				aria-activedescendant={flat[active] ? `palette-hit-${active}` : undefined}
				autocomplete="off"
				spellcheck="false"
			/>
			{#if loading}<Icon name="progress_activity" size={20} class="animate-spin text-on-surface-variant" />{/if}
			<kbd class="rounded-xs border border-outline-variant px-1.5 py-0.5 font-mono text-[11px] text-on-surface-variant">Esc</kbd>
		</div>

		<div class="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-outline-variant/60 px-4 py-2">
			{#if here}
				<button type="button" class="state-layer rounded-full border border-outline-variant px-2.5 py-0.5 label-medium text-on-surface" onclick={() => addFilter(`in:${here}`)}>
					<span class="font-mono">in:{here}</span>
				</button>
			{/if}
			{#each FILTERS as f (f.text)}
				<button type="button" class="state-layer rounded-full border border-outline-variant px-2.5 py-0.5 label-medium text-on-surface-variant" title={f.hint} onclick={() => addFilter(f.text)}>
					<span class="font-mono">{f.text}</span>
				</button>
			{/each}
		</div>

		<div bind:this={listEl} id="palette-results" class="min-h-0 flex-1 overflow-y-auto py-1" role="listbox" aria-label="Results">
			{#if note}
				<p class="px-4 py-3 body-medium text-on-surface-variant">{note}.</p>
			{:else if typed && !loading && !groups.length && !matchingPlaces.length}
				<p class="px-4 py-8 text-center body-medium text-on-surface-variant">Nothing matches “{query.trim()}”.</p>
			{/if}
			{#each sections as g (g.type)}
				<p class="px-4 pt-3 pb-1 label-small tracking-wide text-on-surface-variant uppercase">{g.label}</p>
				{#each g.hits as h (h.link + h.id)}
					{@const index = flat.findIndex((x) => x.h === h)}
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<div
						id="palette-hit-{index}"
						data-index={index}
						role="option"
						tabindex="-1"
						aria-selected={index === active}
						aria-disabled={!h.link}
						class="mx-2 flex items-start gap-3 rounded-md px-3 py-2 {h.link ? 'cursor-pointer' : 'cursor-default opacity-80'} {index === active ? 'bg-secondary-container text-on-secondary-container' : ''}"
						onpointermove={() => (active = index)}
						onclick={() => choose(h)}
					>
						{#if h.avatar !== undefined}
							<Avatar name={h.title} src={h.avatar} size={24} />
						{:else}
							<Icon name={h.icon} size={20} class="mt-0.5 shrink-0 {index === active ? '' : 'text-on-surface-variant'}" />
						{/if}
						<span class="min-w-0 flex-1">
							<span class="block truncate body-medium">{#each parts(h.title) as p, i (i)}{#if p.hit}<mark class="hit">{p.t}</mark>{:else}{p.t}{/if}{/each}</span>
							{#if h.snippet}
								<span class="block truncate body-small text-on-surface-variant {h.mono ? 'font-mono text-[12px]' : ''}">{#each parts(h.snippet) as p, i (i)}{#if p.hit}<mark class="hit">{p.t}</mark>{:else}{p.t}{/if}{/each}</span>
							{/if}
						</span>
						<span class="flex shrink-0 flex-col items-end gap-0.5 pt-0.5">
							{#if h.project}<span class="rounded bg-surface-container-highest px-1.5 font-mono text-[11px] text-on-surface-variant">{h.project.codename}</span>{/if}
							{#if h.at}<span class="body-small text-on-surface-variant">{relativeTime(h.at)}</span>{/if}
						</span>
					</div>
				{/each}
				{#if g.more}
					<button type="button" class="mx-5 mb-1 label-medium text-primary hover:underline" onclick={() => addFilter(`type:${g.type}`)}>More {g.label.toLowerCase()}…</button>
				{/if}
			{/each}
		</div>

		<div class="flex shrink-0 items-center gap-4 border-t border-outline-variant/60 px-4 py-2 body-small text-on-surface-variant">
			<span><kbd class="kbd">↑</kbd> <kbd class="kbd">↓</kbd> to move</span>
			<span><kbd class="kbd">Enter</kbd> to open</span>
			<span class="ml-auto"><kbd class="kbd">Ctrl</kbd> <kbd class="kbd">K</kbd> anywhere</span>
		</div>
	{/if}
</dialog>

<style>
	.palette::backdrop {
		background: color-mix(in srgb, var(--md-sys-color-scrim) 45%, transparent);
	}
	.palette[open] {
		animation: palette-in 140ms ease-out;
	}
	@keyframes palette-in {
		from {
			opacity: 0;
			transform: translateY(-6px);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.palette[open] {
			animation: none;
		}
	}
	.palette :global(mark.hit) {
		border-radius: 2px;
		background: color-mix(in srgb, var(--md-sys-color-primary) 28%, transparent);
		color: inherit;
		font-weight: 600;
	}
	.kbd {
		border: 1px solid var(--md-sys-color-outline-variant);
		border-radius: 4px;
		padding: 0 0.3em;
		font-family: var(--md-sys-typescale-mono);
		font-size: 11px;
	}
</style>
