<script lang="ts">
	import type { AssetLink, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Chip, Icon } from '$lib/m3';
	import { assetTypes, linkDetails as details } from '$lib/assetTypes.svelte';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import AssetGraph from './AssetGraph.svelte';

	/** How a project's assets are connected: which credentials work where, who is admin on what,
	 *  where files came from. A list to read and search, or a graph to explore. */
	interface Props {
		projectId: UUID;
		view: 'list' | 'graph';
		/** Select this asset in the graph. */
		focus?: UUID | null;
		onopenasset: (id: UUID) => void;
	}
	let { projectId, view = $bindable('list'), focus = null, onopenasset }: Props = $props();

	let q = $state('');
	let kinds = $state<string[]>([]);
	let items = $state<AssetLink[]>([]);
	let total = $state(0);
	let counts = $state<Record<string, number>>({});
	let loaded = $state(false);
	let seq = 0;
	async function load() {
		const mine = ++seq;
		try {
			const res = await api().assets.links(projectId, { q: q.trim(), kind: kinds.join(','), limit: 3000 });
			if (mine !== seq) return;
			items = res.items;
			total = res.total;
			counts = res.counts;
			loaded = true;
		} catch (err) {
			if (mine === seq) snackbar.error(err instanceof ApiError ? err.message : 'Could not load the relationships');
		}
	}
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		void q;
		void kinds;
		void projectId;
		clearTimeout(timer);
		timer = setTimeout(() => void load(), 200);
	});
	let liveTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.project_id === projectId && e.name === 'assets.changed') {
				clearTimeout(liveTimer);
				liveTimer = setTimeout(() => void load(), 500);
			}
		})
	);

	let kindChips = $derived(assetTypes.kinds.filter((k) => counts[k.name] || kinds.includes(k.name)));
	const toggle = (k: string) => (kinds = kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k]);

	const source = (l: AssetLink) => (l.source === 'manual' ? `noted by ${l.by ?? 'someone'}` : l.source === 'parser' ? 'from a tool’s results' : `from ${l.where}`);
	const LIST_MAX = 500;
</script>

<div class="flex flex-col gap-3">
	<div class="flex flex-wrap items-center gap-2">
		<div class="flex h-11 min-w-64 flex-1 items-center gap-2 rounded-full bg-surface-container-high px-4">
			<Icon name="search" size={20} class="text-on-surface-variant" />
			<input
				class="h-11 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant"
				placeholder="Search: an asset, a kind (admin, works on), a note…"
				aria-label="Search relationships"
				bind:value={q}
				autocomplete="off"
				spellcheck="false"
			/>
		</div>
		<div class="flex rounded-full border border-outline-variant p-0.5" role="group" aria-label="View">
			{#each [['list', 'list', 'List'], ['graph', 'hub', 'Graph']] as [id, icon, label] (id)}
				<button
					type="button"
					class="state-layer focus-ring flex h-9 items-center gap-1.5 rounded-full px-3.5 label-large {view === id ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface-variant'}"
					aria-pressed={view === id}
					onclick={() => (view = id as 'list' | 'graph')}
				>
					<Icon name={icon} size={18} />{label}
				</button>
			{/each}
		</div>
	</div>
	{#if kindChips.length}
		<div class="flex flex-wrap gap-1.5" aria-label="Kinds of relationship">
			{#each kindChips as k (k.name)}
				<Chip variant="filter" selected={kinds.includes(k.name)} onclick={() => toggle(k.name)} title={k.description}>{k.forward} <span class="text-on-surface-variant">{counts[k.name] ?? 0}</span></Chip>
			{/each}
		</div>
	{/if}

	{#if !loaded}
		<p class="py-6 body-medium text-on-surface-variant">Loading…</p>
	{:else if !items.length}
		<div class="flex flex-col items-center gap-2 rounded-lg border border-dashed border-outline-variant px-6 py-10 text-center">
			<Icon name="hub" size={32} class="text-on-surface-variant" />
			{#if q.trim() || kinds.length}
				<p class="body-medium text-on-surface-variant">No relationship matches.</p>
			{:else}
				<p class="title-small text-on-surface">No relationships yet</p>
				<p class="max-w-md body-medium text-on-surface-variant">
					They come from tools’ results (a login that worked, admin rights, a file taken from a share, where a vulnerability was found), or open an asset and add one.
				</p>
			{/if}
		</div>
	{:else if view === 'graph'}
		<AssetGraph links={items} {focus} {onopenasset} />
		<p class="body-small text-on-surface-variant">Drag to move around, scroll to zoom, click an asset to see what it’s connected to, double-click to open it.</p>
	{:else}
		<ul class="flex flex-col divide-y divide-outline-variant/60 rounded-lg border border-outline-variant">
			{#each items.slice(0, LIST_MAX) as l (l.id)}
				<li class="flex flex-col gap-0.5 px-4 py-2.5">
					<div class="flex flex-wrap items-center gap-x-2 gap-y-1 body-medium">
						<button type="button" class="flex min-w-0 items-center gap-1.5 text-left break-all text-on-surface hover:text-primary hover:underline" onclick={() => onopenasset(l.from.id)}>
							<Icon name={assetTypes.get(l.from.type)?.icon ?? 'radar'} size={18} class="shrink-0 text-on-surface-variant" />{l.from.label}
						</button>
						<span class="flex shrink-0 items-center gap-1 rounded-full bg-secondary-container px-2.5 py-0.5 label-medium text-on-secondary-container">{l.forward}<Icon name="arrow_forward" size={14} /></span>
						<button type="button" class="flex min-w-0 items-center gap-1.5 text-left break-all text-on-surface hover:text-primary hover:underline" onclick={() => onopenasset(l.to.id)}>
							<Icon name={assetTypes.get(l.to.type)?.icon ?? 'radar'} size={18} class="shrink-0 text-on-surface-variant" />{l.to.label}
						</button>
					</div>
					<p class="body-small text-on-surface-variant">
						{#if details(l.fields)}<span class="font-medium text-on-surface">{details(l.fields)}</span> · {/if}{source(l)}{#if l.task}{' '}on <a href={l.task.link} class="text-primary hover:underline">{l.task.title}</a>{/if}
						· <span title={absoluteTime(l.first_seen)}>{relativeTime(l.first_seen)}</span>
					</p>
					{#if l.note}<p class="body-small text-on-surface italic">{l.note}</p>{/if}
				</li>
			{/each}
		</ul>
		{#if total > LIST_MAX}<p class="body-small text-on-surface-variant">Showing {LIST_MAX} of {total}; search or pick a kind to narrow it down.</p>{/if}
	{/if}
</div>
