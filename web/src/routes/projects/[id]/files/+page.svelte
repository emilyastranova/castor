<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { FileCategory, FileFacet, FileFilter, FileList, ProjectFile, UUID } from '$lib/api';
	import { api, ApiError, saveBlob } from '$lib/api';
	import { Button, Chip, Icon, IconButton, Select, TextField, TopAppBar } from '$lib/m3';
	import ProjectSwitcher from '$lib/components/ProjectSwitcher.svelte';
	import FilePreview from '$lib/components/FilePreview.svelte';
	import { CATEGORY, SOURCE, baseName, cardHref, extension, groupFiles, isPicture, placeOf, type Grouping } from '$lib/files';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { absoluteTime, bytes, relativeTime } from '$lib/format';
	import { canEdit } from '$lib/session';
	import { pop } from '$lib/motion';

	let { data } = $props();
	let project = $derived(data.project);

	// ---- what's asked for, kept in the address so a view can be shared ----
	const q0 = page.url.searchParams;
	type Sort = 'newest' | 'oldest' | 'largest' | 'smallest' | 'name';
	type Added = '' | 'today' | '7d' | '30d' | 'custom';
	let categories = $state<FileCategory[]>((q0.get('category')?.split(',').filter((c) => c in CATEGORY) ?? []) as FileCategory[]);
	let source = $state(q0.get('source') ?? '');
	let card = $state(q0.get('card') ?? '');
	let run = $state(q0.get('run') ?? '');
	let agent = $state(q0.get('agent') ?? '');
	let added = $state<Added>((q0.get('added') as Added) ?? (q0.get('since') || q0.get('until') ? 'custom' : ''));
	let since = $state(q0.get('since') ?? '');
	let until = $state(q0.get('until') ?? '');
	let search = $state(q0.get('q') ?? '');
	let sort = $state<Sort>((q0.get('sort') as Sort) ?? 'newest');
	let view = $state<'gallery' | 'list'>(q0.get('view') === 'list' ? 'list' : 'gallery');
	let grouping = $state<Grouping>((q0.get('group') as Grouping) ?? 'day');
	/** Grouping by day only makes sense while the newest or oldest come first. */
	let groupBy = $derived<Grouping>(grouping === 'day' && sort !== 'newest' && sort !== 'oldest' ? 'none' : grouping);

	const day = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
	function filter(): FileFilter {
		const f: FileFilter = { tz: Intl.DateTimeFormat().resolvedOptions().timeZone || undefined };
		if (categories.length) f.category = categories.join(',');
		if (source) f.source = source;
		if (card) f.card = card;
		if (run) f.run = run;
		if (agent) f.agent = agent;
		if (search.trim()) f.q = search.trim();
		const back = (n: number) => day(new Date(Date.now() - n * 86_400_000));
		if (added === 'today') f.since = day(new Date());
		else if (added === '7d') f.since = back(6);
		else if (added === '30d') f.since = back(29);
		else if (added === 'custom') {
			if (since) f.since = since;
			if (until) f.until = until;
		}
		return f;
	}
	let filtered = $derived(categories.length > 0 || !!source || !!card || !!run || !!agent || !!added || !!search.trim());
	function clearFilters() {
		categories = [];
		source = card = run = agent = search = since = until = '';
		added = '';
	}

	// Keep the address in step (without a navigation, so nothing reloads).
	$effect(() => {
		const url = new URL(page.url);
		const set = (k: string, v: string, dflt = '') => (v && v !== dflt ? url.searchParams.set(k, v) : url.searchParams.delete(k));
		set('category', categories.join(','));
		set('source', source);
		set('card', card);
		set('run', run);
		set('agent', agent);
		set('added', added === 'custom' ? '' : added);
		set('since', added === 'custom' ? since : '');
		set('until', added === 'custom' ? until : '');
		set('q', search.trim());
		set('sort', sort, 'newest');
		set('view', view, 'gallery');
		set('group', grouping, 'day');
		if (url.search !== page.url.search) void goto(url, { replaceState: true, noScroll: true, keepFocus: true });
	});

	// ---- the files ----
	const PAGE = 120;
	let res = $state<FileList | null>(null);
	let items = $state<ProjectFile[]>([]);
	let loading = $state(false);
	let animate = $state(false);
	let seq = 0;
	async function load(opts: { more?: boolean; keep?: boolean } = {}) {
		const mine = ++seq;
		loading = true;
		try {
			const offset = opts.more ? items.length : 0;
			const limit = opts.keep ? Math.max(items.length, PAGE) : PAGE;
			const r = await api().files.list(project.id, { ...filter(), sort, limit: Math.min(limit, 500), offset });
			if (mine !== seq) return;
			animate = !!opts.keep;
			res = r;
			items = opts.more ? [...items, ...r.items] : r.items;
			if (!opts.more && !opts.keep) selected = new Set([...selected].filter((id) => r.items.some((f) => f.id === id)));
		} catch (err) {
			if (mine === seq) snackbar.error(err instanceof ApiError ? err.message : 'Could not load the files');
		} finally {
			if (mine === seq) loading = false;
		}
	}
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		void project.id;
		void JSON.stringify(filter());
		void sort;
		clearTimeout(timer);
		// untrack: reading `res` here would make every load start the next one.
		timer = setTimeout(() => void load(), untrack(() => res) ? 180 : 0);
	});
	// New, changed and deleted files show up as they happen.
	let liveTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.project_id !== project.id) return;
			if (e.name.startsWith('attachment.') || e.name === 'task.deleted') {
				clearTimeout(liveTimer);
				liveTimer = setTimeout(() => void load({ keep: true }), 400);
			}
		})
	);
	let more = $derived(!!res && items.length < res.total);
	let groups = $derived(groupFiles(items, groupBy));

	// Load the next page when the end of the list comes into view.
	function sentinel(node: HTMLElement) {
		const io = new IntersectionObserver((entries) => {
			if (entries[0]?.isIntersecting && more && !loading) void load({ more: true });
		}, { rootMargin: '600px' });
		io.observe(node);
		return { destroy: () => io.disconnect() };
	}

	// ---- filter menus ----
	const ANY = (label: string) => ({ value: '', label });
	const facetOptions = (fs: FileFacet[] | undefined, current: string, label: (f: FileFacet) => string = (f) => f.label) => {
		const opts = (fs ?? []).map((f) => ({ value: f.value, label: label(f), description: `${f.count} file${f.count === 1 ? '' : 's'}` }));
		// Keep a chosen value in the menu even when nothing matches it any more.
		if (current && !opts.some((o) => o.value === current)) opts.unshift({ value: current, label: 'The one chosen', description: 'No files with the other filters' });
		return opts;
	};
	let cardOptions = $derived([ANY('Every card'), ...facetOptions(res?.facets.card, card)]);
	let runOptions = $derived([ANY('Every run'), ...facetOptions(res?.facets.run, run, (f) => (f.when ? `${f.label} · ${new Date(f.when).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}` : f.label))]);
	let agentOptions = $derived([ANY('Every agent'), ...facetOptions(res?.facets.agent, agent)]);
	let sourceOptions = $derived([ANY('Anywhere'), ...facetOptions(res?.facets.source, source)]);
	let categoryCounts = $derived(new Map((res?.facets.category ?? []).map((f) => [f.value, f.count])));
	let allCount = $derived([...categoryCounts.values()].reduce((a, b) => a + b, 0));
	/** Phones: the filters other than search and kind sit behind a button, which counts those set. */
	let filtersOpen = $state(false);
	let fold = $derived(filtersOpen ? '' : 'max-sm:hidden');
	let hiddenFilters = $derived([card, run, agent, source, added].filter(Boolean).length);
	function toggleCategory(c: FileCategory) {
		categories = categories.includes(c) ? categories.filter((x) => x !== c) : [...categories, c];
	}

	// ---- choosing files ----
	let selected = $state(new Set<UUID>());
	let anchor = $state<UUID | null>(null);
	let choosing = $derived(selected.size > 0);
	let chosen = $derived(items.filter((f) => selected.has(f.id)));
	let chosenSize = $derived(chosen.reduce((a, f) => a + f.size, 0));
	function toggle(f: ProjectFile, range = false) {
		const next = new Set(selected);
		if (range && anchor) {
			const a = items.findIndex((x) => x.id === anchor);
			const b = items.findIndex((x) => x.id === f.id);
			if (a >= 0 && b >= 0) {
				const on = !selected.has(f.id);
				for (const x of items.slice(Math.min(a, b), Math.max(a, b) + 1)) on ? next.add(x.id) : next.delete(x.id);
				selected = next;
				anchor = f.id;
				return;
			}
		}
		next.has(f.id) ? next.delete(f.id) : next.add(f.id);
		selected = next;
		anchor = f.id;
	}
	function selectAll() {
		selected = new Set(items.map((f) => f.id));
	}
	function clearSelection() {
		selected = new Set();
		anchor = null;
	}
	/** A click on a file: while choosing it chooses (Shift for a range); otherwise it opens. */
	function activate(f: ProjectFile, e: MouseEvent) {
		// The tile is a link to the file so "open in a new tab" (middle click, its menu) works.
		e.preventDefault();
		if (choosing || e.shiftKey || e.ctrlKey || e.metaKey) {
			toggle(f, e.shiftKey);
			return;
		}
		previewOfChosen = false;
		previewIndex = items.indexOf(f);
		previewOpen = true;
	}
	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && choosing && !previewOpen && !(e.target as HTMLElement).closest('dialog')) {
			e.preventDefault();
			clearSelection();
		}
	}

	// ---- doing things with them ----
	let zipping = $state(false);
	async function downloadZip(body: { ids?: UUID[]; filter?: FileFilter }) {
		zipping = true;
		try {
			const { blob, name } = await api().files.zip(project.id, body);
			saveBlob(blob, name);
		} catch (err) {
			snackbar.error(err instanceof Error ? err.message : 'The download failed');
		} finally {
			zipping = false;
		}
	}
	function downloadChosen() {
		if (chosen.length === 1) {
			window.location.href = api().attachments.url(chosen[0].id);
			return;
		}
		void downloadZip({ ids: chosen.map((f) => f.id) });
	}
	let chosenPictures = $derived(chosen.filter(isPicture));
	function viewChosen() {
		if (!chosen.length) return;
		previewOfChosen = true;
		previewIndex = 0;
		previewOpen = true;
	}
	async function remove(files: ProjectFile[]) {
		const one = files.length === 1;
		const ok = await confirmAction({
			title: one ? `Delete ${files[0].filename}?` : `Delete ${files.length} files?`,
			message: `${one ? 'It goes' : 'They go'} from ${one ? 'its card' : 'their cards'}, and from any comment or note that shows ${one ? 'it' : 'them'}. This can’t be undone.`,
			confirmLabel: 'Delete'
		});
		if (!ok) return false;
		const results = await Promise.allSettled(files.map((f) => api().attachments.remove(f.id)));
		const failed = results.filter((r) => r.status === 'rejected').length;
		if (failed) snackbar.error(`${failed} couldn’t be deleted`);
		else snackbar.show(one ? `Deleted ${files[0].filename}` : `Deleted ${files.length} files`);
		const gone = new Set(files.filter((_, i) => results[i].status === 'fulfilled').map((f) => f.id));
		items = items.filter((f) => !gone.has(f.id));
		selected = new Set([...selected].filter((id) => !gone.has(id)));
		void load({ keep: true });
		return true;
	}

	// ---- looking at one ----
	let previewOpen = $state(false);
	let previewIndex = $state(0);
	/** The preview steps through the chosen files (View) or through everything listed. */
	let previewOfChosen = $state(false);
	let previewFiles = $derived(previewOfChosen ? chosen : items);
	async function removeFromPreview(f: ProjectFile) {
		const list = previewFiles;
		const at = list.indexOf(f);
		if (!(await remove([f]))) return;
		if (list.length <= 1) previewOpen = false;
		else previewIndex = Math.min(at, list.length - 2);
	}
	/** Pictures that couldn't be shrunk (or are SVG) show as themselves, or else their icon. */
	let noThumb = $state(new Set<UUID>());
	const isSvg = (f: ProjectFile) => f.content_type.includes('svg');
	const editable = $derived(canEdit());
	const SORTS: { value: Sort; label: string }[] = [
		{ value: 'newest', label: 'Newest first' },
		{ value: 'oldest', label: 'Oldest first' },
		{ value: 'largest', label: 'Largest first' },
		{ value: 'smallest', label: 'Smallest first' },
		{ value: 'name', label: 'By name' }
	];
	function sortBy(col: 'name' | 'size' | 'added') {
		if (col === 'name') sort = 'name';
		else if (col === 'size') sort = sort === 'largest' ? 'smallest' : 'largest';
		else sort = sort === 'newest' ? 'oldest' : 'newest';
	}
	const sortIcon = (col: 'name' | 'size' | 'added') =>
		col === 'name' ? (sort === 'name' ? 'arrow_downward' : '') : col === 'size' ? (sort === 'largest' ? 'arrow_downward' : sort === 'smallest' ? 'arrow_upward' : '') : sort === 'newest' ? 'arrow_downward' : sort === 'oldest' ? 'arrow_upward' : '';
</script>

<svelte:head><title>{project.codename} · Files · Castor</title></svelte:head>
<svelte:window {onkeydown} />

<TopAppBar>
	<ProjectSwitcher {project} />
</TopAppBar>

{#snippet checkbox(f: ProjectFile, extra = '')}
	<input
		type="checkbox"
		class="size-[18px] cursor-pointer accent-[var(--md-sys-color-primary)] {extra}"
		checked={selected.has(f.id)}
		aria-label="Choose {f.filename}"
		onclick={(e) => {
			// No preventDefault: the browser would undo the tick after Svelte sets it.
			e.stopPropagation();
			toggle(f, e.shiftKey);
		}}
	/>
{/snippet}

{#snippet thumb(f: ProjectFile, size: number, cls: string)}
	{#if isPicture(f) && !noThumb.has(f.id)}
		<img
			src={isSvg(f) ? api().attachments.url(f.id, true) : api().attachments.thumbnail(f.id, size, f.size)}
			alt=""
			loading="lazy"
			decoding="async"
			class="{cls} {isSvg(f) ? 'object-contain p-2' : 'object-cover'}"
			onerror={() => (noThumb = new Set([...noThumb, f.id]))}
		/>
	{:else}
		<span class="{cls} flex flex-col items-center justify-center gap-1 text-on-surface-variant">
			<Icon name={CATEGORY[f.category].icon} size={size > 200 ? 40 : 22} class="opacity-80" />
			{#if size > 200 && extension(f.filename)}<span class="rounded-full bg-surface-container-highest px-2 py-0.5 label-small tracking-wide">{extension(f.filename)}</span>{/if}
		</span>
	{/if}
{/snippet}

<div class="flex-1 overflow-auto">
	<div class="mx-auto flex w-full max-w-7xl flex-col gap-3 px-4 pt-2 pb-28 sm:px-6">
		<!-- Search, order and view -->
		<div class="flex flex-wrap items-end gap-3">
			<TextField label="Search" placeholder="File, card, run, step or agent" leadingIcon="search" bind:value={search} class="min-w-0 flex-1 sm:min-w-[14rem] sm:max-w-md" trailingIcon={search ? 'close' : undefined} trailingLabel="Clear the search" ontrailing={() => (search = '')} />
			<!-- On a phone the other filters fold away behind this. -->
			<button type="button" class="state-layer relative flex h-14 items-center gap-1.5 rounded-xs border border-outline px-3 label-large text-on-surface-variant sm:hidden {filtersOpen ? 'border-2 border-primary text-primary' : ''}" aria-expanded={filtersOpen} onclick={() => (filtersOpen = !filtersOpen)}>
				<Icon name="tune" size={20} />Filters{#if hiddenFilters}<span class="flex size-5 items-center justify-center rounded-full bg-primary label-small text-on-primary">{hiddenFilters}</span>{/if}
			</button>
			<Select label="Added" class="w-40 max-sm:w-full {fold}" bind:value={added} options={[{ value: '', label: 'Any time' }, { value: 'today', label: 'Today' }, { value: '7d', label: 'Last 7 days' }, { value: '30d', label: 'Last 30 days' }, { value: 'custom', label: 'Between days…' }]} />
			{#if added === 'custom'}
				<TextField label="From" type="date" bind:value={since} class="w-40 max-sm:flex-1 {fold}" />
				<TextField label="To" type="date" bind:value={until} class="w-40 max-sm:flex-1 {fold}" />
			{/if}
			<div class="ml-auto flex items-center gap-1 {filtersOpen ? 'max-sm:w-full' : ''}">
				<Select label="Order" class="w-40 max-sm:w-auto max-sm:flex-1 {fold}" bind:value={sort} options={SORTS} />
				{#if view === 'gallery'}
					<Select label="Group" class="w-36 max-sm:w-auto max-sm:flex-1 {fold}" bind:value={grouping} options={[{ value: 'day', label: 'By day' }, { value: 'card', label: 'By card' }, { value: 'none', label: 'None' }]} />
				{/if}
				<div class="flex h-14 items-center gap-0.5 self-end rounded-full pl-1" role="group" aria-label="View">
					<IconButton icon="grid_view" label="Gallery" selected={view === 'gallery'} onclick={() => (view = 'gallery')} />
					<IconButton icon="view_list" label="List" selected={view === 'list'} onclick={() => (view = 'list')} />
				</div>
			</div>
		</div>

		<!-- What sort of file -->
		<div class="-mx-1 flex gap-2 overflow-x-auto px-1 py-0.5" role="group" aria-label="Kind of file">
			<Chip variant="filter" selected={!categories.length} onclick={() => (categories = [])}>All <span class="tabular-nums opacity-70">{allCount}</span></Chip>
			{#each Object.entries(CATEGORY) as [c, meta] (c)}
				{@const n = categoryCounts.get(c) ?? 0}
				{#if n || categories.includes(c as FileCategory)}
					<Chip variant="filter" icon={meta.icon} selected={categories.includes(c as FileCategory)} onclick={() => toggleCategory(c as FileCategory)}>
						{meta.label} <span class="tabular-nums opacity-70">{n}</span>
					</Chip>
				{/if}
			{/each}
		</div>

		<!-- Where it came from -->
		{#if res && res.everything}
			<div class="flex-wrap items-end gap-3 {filtersOpen ? 'flex' : 'hidden sm:flex'}">
				<Select label="Card" class="w-full sm:w-60" bind:value={card} options={cardOptions} />
				{#if runOptions.length > 1 || run}
					<Select label="Playbook run" class="w-full sm:w-60" bind:value={run} options={runOptions} />
				{/if}
				{#if agentOptions.length > 1 || agent}
					<Select label="Agent" class="w-full sm:w-48" bind:value={agent} options={agentOptions} />
				{/if}
				<Select label="From" class="w-full sm:w-52" bind:value={source} options={sourceOptions} />
			</div>
		{/if}

		<!-- How many, and doing things with all of them -->
		{#if res && res.everything}
			<div class="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-1">
				<p class="body-medium text-on-surface-variant" aria-live="polite">
					<span class="text-on-surface tabular-nums">{res.total.toLocaleString()}</span> file{res.total === 1 ? '' : 's'}
					<span class="tabular-nums">· {bytes(res.total_size)}</span>
					{#if filtered}<span> of {res.everything.toLocaleString()}</span>{/if}
				</p>
				{#if filtered}
					<Button variant="text" icon="filter_alt_off" onclick={clearFilters}>Clear filters</Button>
				{/if}
				<div class="ml-auto flex items-center gap-1">
					{#if items.length && !choosing}
						<Button variant="text" icon="check_box" onclick={selectAll}>Choose all{more ? ` ${items.length} shown` : ''}</Button>
					{/if}
					{#if res.total > 0}
						<Button variant="tonal" icon="folder_zip" loading={zipping && !choosing} disabled={zipping} onclick={() => downloadZip({ filter: filter() })}>
							Download {filtered ? 'these' : 'all'} as zip
						</Button>
					{/if}
				</div>
			</div>
		{/if}

		<!-- The files -->
		{#if !res}
			<div class="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-3" aria-hidden="true">
				{#each Array(12) as _, i (i)}
					<div class="aspect-[4/4.2] animate-pulse rounded-lg bg-surface-container"></div>
				{/each}
			</div>
		{:else if !res.everything}
			<div class="mx-auto flex max-w-md flex-col items-center gap-3 py-20 text-center">
				<span class="flex size-16 items-center justify-center rounded-full bg-secondary-container text-on-secondary-container"><Icon name="folder_open" size={32} /></span>
				<h2 class="headline-small text-on-surface">No files yet</h2>
				<p class="body-medium text-on-surface-variant">
					Every file in {project.codename} gathers here: what people attach to cards, pictures pasted into notes and posts, snapshots, and the artifacts jobs collect.
				</p>
				<Button variant="tonal" icon="view_kanban" href="/projects/{project.id}">Go to the board</Button>
			</div>
		{:else if !items.length}
			<div class="mx-auto flex max-w-md flex-col items-center gap-3 py-16 text-center">
				<Icon name="search_off" size={40} class="text-on-surface-variant" />
				<h2 class="title-large text-on-surface">No files match</h2>
				<p class="body-medium text-on-surface-variant">Try fewer filters, or other words.</p>
				<Button variant="tonal" icon="filter_alt_off" onclick={clearFilters}>Clear filters</Button>
			</div>
		{:else if view === 'gallery'}
			{#each groups as g (g.key)}
				<section class="flex flex-col gap-2">
					{#if g.label}
						<h2 class="sticky top-0 z-[1] -mx-1 flex items-baseline gap-2 bg-surface/95 px-1 py-1.5 title-small text-on-surface backdrop-blur-sm">
							{g.label}<span class="label-medium text-on-surface-variant tabular-nums">{g.items.length}</span>
						</h2>
					{/if}
					<ul class="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-3 sm:grid-cols-[repeat(auto-fill,minmax(12rem,1fr))]">
						{#each g.items as f (f.id)}
							{@const on = selected.has(f.id)}
							<li in:pop={{ enabled: animate }} class="group relative">
								<a
									href={api().attachments.url(f.id, true)}
									class="focus-ring flex h-full flex-col overflow-hidden rounded-lg border transition-[border-color,box-shadow,background-color] {on ? 'border-primary bg-secondary-container text-on-secondary-container ring-2 ring-primary' : 'border-outline-variant/60 bg-surface-container-low hover:border-outline-variant hover:shadow-e1'}"
									onclick={(e) => activate(f, e)}
									title={f.filename}
								>
									<span class="relative block aspect-[4/3] overflow-hidden bg-surface-container">
										{@render thumb(f, 360, `size-full transition-transform duration-200 ${on ? 'scale-[0.94] rounded-md' : ''}`)}
										{#if f.source === 'artifact' || f.source === 'snapshot'}
											<span class="absolute right-2 bottom-2 flex items-center gap-1 rounded-full bg-surface-container-high/90 px-2 py-0.5 label-small text-on-surface shadow-e1" title={SOURCE[f.source].label}>
												<Icon name={SOURCE[f.source].icon} size={14} />{SOURCE[f.source].short}
											</span>
										{/if}
									</span>
									<span class="flex min-w-0 flex-col gap-0.5 px-3 py-2.5">
										<span class="truncate body-medium text-on-surface">{baseName(f.filename)}</span>
										<span class="truncate body-small text-on-surface-variant">{placeOf(f)}</span>
										<span class="body-small text-on-surface-variant/80 tabular-nums" title={absoluteTime(f.created_at)}>{relativeTime(f.created_at)} · {bytes(f.size)}</span>
									</span>
								</a>
								<label class="absolute top-1.5 left-1.5 flex size-9 items-center justify-center rounded-full bg-surface-container-high/90 shadow-e1 transition-opacity {on || choosing ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}">
									{@render checkbox(f)}
								</label>
							</li>
						{/each}
					</ul>
				</section>
			{/each}
		{:else}
			<div class="overflow-x-auto rounded-lg border border-outline-variant/60">
				<table class="w-full border-collapse body-medium">
					<thead class="bg-surface-container-low text-left label-large text-on-surface-variant">
						<tr>
							<th class="w-10 py-2 pl-3"><span class="sr-only">Chosen</span></th>
							<th class="py-2 pr-3" colspan="2">
								<button type="button" class="inline-flex items-center gap-1 hover:text-on-surface" onclick={() => sortBy('name')}>Name{#if sortIcon('name')}<Icon name={sortIcon('name')} size={16} />{/if}</button>
							</th>
							<th class="hidden py-2 pr-3 md:table-cell">On</th>
							<th class="hidden py-2 pr-3 lg:table-cell">From</th>
							<th class="py-2 pr-3 text-right">
								<button type="button" class="inline-flex items-center gap-1 hover:text-on-surface" onclick={() => sortBy('size')}>Size{#if sortIcon('size')}<Icon name={sortIcon('size')} size={16} />{/if}</button>
							</th>
							<th class="py-2 pr-3 text-right">
								<button type="button" class="inline-flex items-center gap-1 hover:text-on-surface" onclick={() => sortBy('added')}>Added{#if sortIcon('added')}<Icon name={sortIcon('added')} size={16} />{/if}</button>
							</th>
						</tr>
					</thead>
					<tbody>
						{#each items as f (f.id)}
							{@const on = selected.has(f.id)}
							<tr class="cursor-pointer border-t border-outline-variant/40 transition-colors {on ? 'bg-secondary-container/60' : 'hover:bg-surface-container-low'}" onclick={(e) => activate(f, e)}>
								<td class="py-1.5 pl-3">{@render checkbox(f)}</td>
								<td class="w-12 py-1.5 pr-2">
									<span class="block size-10 overflow-hidden rounded-sm bg-surface-container">{@render thumb(f, 160, 'size-10')}</span>
								</td>
								<td class="w-1/2 max-w-0 py-1.5 pr-3">
									<a href={api().attachments.url(f.id, true)} class="block truncate text-on-surface hover:underline" onclick={(e) => { e.stopPropagation(); activate(f, e); }}>{f.filename}</a>
									<span class="block truncate body-small text-on-surface-variant md:hidden">{placeOf(f)}</span>
								</td>
								<td class="hidden w-1/4 max-w-0 py-1.5 pr-3 md:table-cell">
									{#if f.task_id}
										<a href={cardHref(project.id, f)} class="block truncate text-on-surface-variant hover:text-primary hover:underline" onclick={(e) => e.stopPropagation()}>{placeOf(f)}</a>
									{:else}
										<span class="text-on-surface-variant">Notes and posts</span>
									{/if}
								</td>
								<td class="hidden py-1.5 pr-3 whitespace-nowrap text-on-surface-variant lg:table-cell">
									<span class="inline-flex items-center gap-1.5"><Icon name={SOURCE[f.source].icon} size={16} />{f.agent_name ?? SOURCE[f.source].short}</span>
								</td>
								<td class="py-1.5 pr-3 text-right whitespace-nowrap text-on-surface-variant tabular-nums">{bytes(f.size)}</td>
								<td class="py-1.5 pr-3 text-right whitespace-nowrap text-on-surface-variant" title={absoluteTime(f.created_at)}>{relativeTime(f.created_at)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}

		{#if more}
			<div use:sentinel class="flex justify-center py-4">
				<Button variant="text" loading={loading} onclick={() => load({ more: true })}>Show more</Button>
			</div>
		{/if}
	</div>
</div>

<!-- What's chosen, and what to do with it -->
{#if choosing}
	<div class="pointer-events-none fixed inset-x-0 bottom-[calc(env(safe-area-inset-bottom)+4.5rem)] z-20 flex justify-center px-4 md:bottom-6" role="region" aria-label="Chosen files">
		<div in:pop class="pointer-events-auto flex max-w-full flex-wrap items-center gap-1 rounded-xl bg-inverse-surface py-1.5 pr-2 pl-4 text-inverse-on-surface shadow-e3">
			<p class="mr-2 label-large whitespace-nowrap"><span class="tabular-nums">{selected.size}</span> chosen <span class="opacity-70 tabular-nums">· {bytes(chosenSize)}</span></p>
			<button type="button" class="state-layer flex h-10 items-center gap-1.5 rounded-full px-3 label-large" onclick={viewChosen}>
				<Icon name={chosenPictures.length === chosen.length ? 'photo_library' : 'visibility'} size={18} />View
			</button>
			<button type="button" class="state-layer flex h-10 items-center gap-1.5 rounded-full px-3 label-large disabled:opacity-50" disabled={zipping} onclick={downloadChosen}>
				<Icon name="download" size={18} />{zipping ? 'Preparing…' : chosen.length === 1 ? 'Download' : 'Download zip'}
			</button>
			{#if editable}
				<button type="button" class="state-layer flex h-10 items-center gap-1.5 rounded-full px-3 label-large" onclick={() => remove(chosen)}>
					<Icon name="delete" size={18} />Delete
				</button>
			{/if}
			{#if selected.size < items.length}
				<button type="button" class="state-layer flex h-10 items-center rounded-full px-3 label-large" onclick={selectAll}>All {items.length}</button>
			{/if}
			<button type="button" class="state-layer flex size-10 items-center justify-center rounded-full" aria-label="Clear the choice (Esc)" title="Clear the choice (Esc)" onclick={clearSelection}>
				<Icon name="close" size={20} />
			</button>
		</div>
	</div>
{/if}

<FilePreview bind:open={previewOpen} bind:index={previewIndex} projectId={project.id} files={previewFiles} canDelete={editable} ondelete={removeFromPreview} />
