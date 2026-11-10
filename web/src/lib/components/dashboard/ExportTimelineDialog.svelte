<script lang="ts">
	import type { FeedKind } from '$lib/api';
	import { saveBlob } from '$lib/api';
	import { Button, Dialog, Icon } from '$lib/m3';
	import { isAdmin } from '$lib/session';
	import { snackbar } from '$lib/snackbar.svelte';
	import { ALL_KINDS } from './kinds';

	/** Export the project's timeline as a PDF or a self-contained HTML file: what's in it is
	 *  picked like Home's filters (it opens with the ones in use), plus a date range. */
	interface Props {
		open: boolean;
		projectId: string;
		kinds: Set<FeedKind>;
		mine: boolean;
		tag: string | null;
	}
	let { open = $bindable(false), projectId, kinds, mine, tag }: Props = $props();

	let format = $state<'pdf' | 'html'>('pdf');
	let chosen = $state<Set<FeedKind>>(new Set());
	let onlyMine = $state(false);
	let byTag = $state<string | null>(null);
	let since = $state('');
	let until = $state('');
	let order = $state<'newest' | 'oldest'>('newest');
	let working = $state(false);
	let kindOptions = $derived(ALL_KINDS.filter((k) => k.kind !== 'admin' || isAdmin()));

	// Each time it opens, start from what the timeline is showing.
	let openedFor = false;
	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (!open) return;
		chosen = new Set(kinds);
		onlyMine = mine;
		byTag = tag;
		since = '';
		until = '';
	});

	const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
	const ago = (days: number) => iso(new Date(Date.now() - days * 86_400_000));
	const RANGES = [
		{ label: 'All time', since: () => '', until: () => '' },
		{ label: 'Today', since: () => ago(0), until: () => ago(0) },
		{ label: 'Last 7 days', since: () => ago(6), until: () => ago(0) },
		{ label: 'Last 30 days', since: () => ago(29), until: () => ago(0) },
		{ label: 'This month', since: () => iso(new Date(new Date().getFullYear(), new Date().getMonth(), 1)), until: () => ago(0) }
	];
	let rangeIs = (r: (typeof RANGES)[number]) => since === r.since() && until === r.until();
	let badRange = $derived(!!since && !!until && until < since);
	let nothing = $derived(!byTag && chosen.size === 0);

	function toggle(k: FeedKind) {
		const next = new Set(chosen);
		if (next.has(k)) next.delete(k);
		else next.add(k);
		chosen = next;
	}

	async function download() {
		if (badRange || nothing) return;
		working = true;
		const q = new URLSearchParams({ format, tz: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' });
		if (byTag) q.set('tag', byTag);
		else if (chosen.size < kindOptions.length) q.set('kinds', [...chosen].join(','));
		if (onlyMine) q.set('mine', 'true');
		if (since) q.set('since', since);
		if (until) q.set('until', until);
		if (order === 'oldest') q.set('order', 'oldest');
		try {
			const res = await fetch(`/api/projects/${projectId}/timeline/export?${q}`);
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(typeof detail?.detail === 'string' ? detail.detail : `The export failed (${res.status})`);
			}
			const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? `timeline.${format}`;
			saveBlob(await res.blob(), name);
			snackbar.show(`Exported ${name}`);
			open = false;
		} catch (err) {
			snackbar.error(err instanceof Error ? err.message : 'The export failed');
		} finally {
			working = false;
		}
	}
	const chip = (on: boolean) =>
		`touch-target flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 label-medium whitespace-nowrap transition-colors ${on ? 'border-transparent bg-secondary-container text-on-secondary-container' : 'border-outline-variant text-on-surface-variant hover:bg-surface-container-high'}`;
</script>

<Dialog bind:open title="Export the timeline" icon="download" size="md">
	<div class="flex flex-col gap-5 pt-1 pb-2">
		<section>
			<h3 class="mb-2 label-large text-on-surface">Format</h3>
			<div class="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Format">
				{#each [{ value: 'pdf', label: 'PDF', icon: 'picture_as_pdf', note: 'Pages to share, print or attach' }, { value: 'html', label: 'HTML', icon: 'web', note: 'One file that opens in any browser' }] as f (f.value)}
					<button
						type="button"
						role="radio"
						aria-checked={format === f.value}
						class="state-layer focus-ring flex items-center gap-3 rounded-md border px-3 py-2.5 text-left {format === f.value ? 'border-primary bg-primary-container/40 text-on-surface' : 'border-outline-variant text-on-surface'}"
						onclick={() => (format = f.value as 'pdf' | 'html')}
					>
						<Icon name={f.icon} size={24} class={format === f.value ? 'text-primary' : 'text-on-surface-variant'} />
						<span class="min-w-0">
							<span class="block label-large">{f.label}</span>
							<span class="block body-small text-on-surface-variant">{f.note}</span>
						</span>
					</button>
				{/each}
			</div>
		</section>

		<section>
			<div class="mb-2 flex items-center gap-2">
				<h3 class="flex-1 label-large text-on-surface">What to include</h3>
				{#if !byTag}
					<button type="button" class="label-medium text-primary hover:underline" onclick={() => (chosen = new Set(kindOptions.map((k) => k.kind)))}>Everything</button>
				{/if}
			</div>
			{#if byTag}
				<div class="flex flex-wrap items-center gap-2 rounded-md bg-secondary-container/50 px-3 py-2">
					<span class="body-medium text-on-surface">Only posts tagged</span>
					<span class="rounded-full bg-secondary-container px-2.5 py-0.5 label-large text-on-secondary-container">#{byTag}</span>
					<button type="button" class="ml-auto label-medium text-primary hover:underline" onclick={() => (byTag = null)}>Pick kinds instead</button>
				</div>
			{:else}
				<div class="flex flex-wrap gap-2">
					{#each kindOptions as k (k.kind)}
						<button type="button" class={chip(chosen.has(k.kind))} aria-pressed={chosen.has(k.kind)} onclick={() => toggle(k.kind)}>
							<Icon name={chosen.has(k.kind) ? 'check' : k.icon} size={14} />{k.label}
						</button>
					{/each}
				</div>
				{#if nothing}<p class="mt-1.5 body-small text-error">Pick at least one kind.</p>{/if}
			{/if}
			<button type="button" class="mt-2 {chip(onlyMine)}" aria-pressed={onlyMine} onclick={() => (onlyMine = !onlyMine)}>
				<Icon name={onlyMine ? 'check' : 'person'} size={14} />Only what's about me
			</button>
		</section>

		<section>
			<h3 class="mb-2 label-large text-on-surface">When</h3>
			<div class="mb-3 flex flex-wrap gap-2">
				{#each RANGES as r (r.label)}
					<button type="button" class={chip(rangeIs(r))} aria-pressed={rangeIs(r)} onclick={() => ((since = r.since()), (until = r.until()))}>{r.label}</button>
				{/each}
			</div>
			<div class="grid grid-cols-2 gap-3">
				<label class="flex flex-col gap-1">
					<span class="body-small text-on-surface-variant">From</span>
					<input type="date" bind:value={since} max={until || undefined} class="h-10 rounded-xs border border-outline bg-transparent px-3 body-medium text-on-surface outline-none focus:border-2 focus:border-primary [color-scheme:light_dark]" />
				</label>
				<label class="flex flex-col gap-1">
					<span class="body-small text-on-surface-variant">To (including)</span>
					<input type="date" bind:value={until} min={since || undefined} class="h-10 rounded-xs border border-outline bg-transparent px-3 body-medium text-on-surface outline-none focus:border-2 focus:border-primary [color-scheme:light_dark]" />
				</label>
			</div>
			{#if badRange}<p class="mt-1.5 body-small text-error">The end comes before the start.</p>{/if}
			<div class="mt-3 flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Order">
				<span class="mr-1 body-small text-on-surface-variant">Order</span>
				{#each [{ value: 'newest', label: 'Newest first', icon: 'arrow_downward' }, { value: 'oldest', label: 'Oldest first', icon: 'arrow_upward' }] as o (o.value)}
					<button type="button" role="radio" aria-checked={order === o.value} class={chip(order === o.value)} onclick={() => (order = o.value as 'newest' | 'oldest')}>
						<Icon name={order === o.value ? 'check' : o.icon} size={14} />{o.label}
					</button>
				{/each}
			</div>
			<p class="mt-2 body-small text-on-surface-variant">Times are in your time zone. Pictures stored in Castor are included; other links stay links. The newest 5,000 entries at most.</p>
		</section>
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="download" loading={working} disabled={badRange || nothing} onclick={download}>Export {format.toUpperCase()}</Button>
	{/snippet}
</Dialog>
