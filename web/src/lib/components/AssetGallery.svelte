<script lang="ts">
	import type { AssetGallery, Scope, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon, IconButton, Select } from '$lib/m3';
	import { assetTypes } from '$lib/assetTypes.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { pop } from '$lib/motion';
	import Spoilers from './Spoilers.svelte';

	/** Web assets as a grid of their latest screenshots, grouped by what they look like: the same
	 *  login page on 40 hosts is one group, so the odd ones out stand out. */
	interface Props {
		projectId: UUID;
		scope: Scope;
		onopenasset: (id: UUID) => void;
	}
	let { projectId, scope, onopenasset }: Props = $props();

	const SHOWN = 8; // of a group, before "Show all"
	let where = $state('');
	let scopeFilter = $state('');
	let notSeen = $state(false);
	let data = $state<AssetGallery | null>(null);
	let open = $state<Set<number>>(new Set());
	let live = $state(false);

	let seq = 0;
	async function load(quiet = false) {
		const mine = ++seq;
		try {
			const res = await api().assets.gallery(projectId, { where, scope: scopeFilter || undefined, seen: notSeen ? 'missing' : undefined });
			if (mine !== seq) return;
			live = quiet;
			if (!quiet) open = new Set();
			data = res;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load the gallery');
		}
	}
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		void [where, scopeFilter, notSeen];
		clearTimeout(timer);
		timer = setTimeout(() => void load(), 200);
	});
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.project_id !== projectId || e.name !== 'assets.changed') return;
			clearTimeout(timer);
			timer = setTimeout(() => void load(true), 600);
		})
	);

	/** Groups of two or more first, then everything that looks like nothing else, together. */
	let alike = $derived((data?.groups ?? []).filter((g) => g.size > 1));
	let unique = $derived((data?.groups ?? []).filter((g) => g.size === 1).flatMap((g) => g.items));

	function toggle(i: number) {
		const next = new Set(open);
		if (next.has(i)) next.delete(i);
		else next.add(i);
		open = next;
	}
	const statusTone = (s: unknown) =>
		typeof s !== 'number' ? 'bg-surface-container-highest text-on-surface-variant' : s >= 500 ? 'bg-error-container text-on-error-container' : s >= 400 ? 'bg-tertiary-container text-on-tertiary-container' : 'bg-secondary-container text-on-secondary-container';
</script>

{#snippet card(a: AssetGallery['groups'][number]['items'][number])}
	<li in:pop={{ enabled: live }}>
		<button type="button" class="state-layer focus-ring group flex w-full flex-col overflow-hidden rounded-md border border-outline-variant/60 bg-surface-container-low text-left" onclick={() => onopenasset(a.id)}>
			<span class="relative block aspect-[16/10] w-full overflow-hidden bg-surface-container-high">
				<img src={api().attachments.thumbnail(a.screenshot.attachment_id, 360)} alt="Screenshot of {a.label}" class="h-full w-full object-cover object-top" loading="lazy" />
				{#if a.missing_since}<span class="absolute top-1.5 left-1.5 inline-flex items-center gap-1 rounded-xs bg-error-container px-1.5 py-px label-small text-on-error-container"><Icon name="visibility_off" size={12} />not seen</span>{/if}
				{#if a.fields.status !== undefined}<span class="absolute top-1.5 right-1.5 rounded-xs px-1.5 py-px label-small {statusTone(a.fields.status)}">{a.fields.status}</span>{/if}
			</span>
			<span class="flex min-w-0 flex-col gap-0.5 px-2.5 py-2">
				<span class="truncate font-mono text-[12.5px] text-on-surface" title={a.label}>{a.label}</span>
				<span class="truncate body-small text-on-surface-variant">{#if a.fields.title}<Spoilers text={String(a.fields.title)} />{:else}<span class="italic">No title</span>{/if}</span>
				<span class="body-small text-on-surface-variant/80" title={absoluteTime(a.screenshot.at)}>{relativeTime(a.screenshot.at)}</span>
			</span>
		</button>
	</li>
{/snippet}

<div class="flex flex-col gap-4">
	<div class="flex flex-wrap items-center gap-2">
		<label class="flex min-w-[16rem] flex-1 items-center gap-2 rounded-full bg-surface-container-high px-4">
			<Icon name="filter_list" size={20} class="text-on-surface-variant" />
			<input class="h-11 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter: words, status=200, title:login, tech:wordpress" bind:value={where} autocomplete="off" spellcheck="false" />
			{#if where}<IconButton icon="close" label="Clear" size="sm" onclick={() => (where = '')} />{/if}
		</label>
		{#if scope.groups.length}
			<Select label="Scope" bind:value={scopeFilter} class="w-44" options={[{ value: '', label: 'Anywhere' }, { value: '*', label: 'In scope' }, ...scope.groups.map((g) => ({ value: g.name, label: g.name })), { value: '-', label: 'Outside scope' }]} />
		{/if}
		<button type="button" class="flex items-center gap-1.5 rounded-full border px-3 py-1 label-large {notSeen ? 'border-transparent bg-error-container text-on-error-container' : 'border-outline-variant text-on-surface-variant'}" aria-pressed={notSeen} onclick={() => (notSeen = !notSeen)}>
			<Icon name="visibility_off" size={16} />Not seen
		</button>
	</div>

	{#if !data}
		<p class="py-10 text-center body-medium text-on-surface-variant">Loading…</p>
	{:else if !data.total}
		<div class="flex flex-col items-center gap-2 py-16 text-center text-on-surface-variant">
			<Icon name="photo_library" size={40} />
			<p class="body-large">{where || scopeFilter || notSeen ? 'Nothing with a screenshot matches.' : 'No screenshots yet.'}</p>
			<p class="max-w-md body-medium">
				Run <strong>gowitness screenshots</strong> on web hosts or URLs (an httpx probe’s results work as targets): each page’s screenshot lands on its URL asset, and pages that look alike are grouped here.
				{#if data.without_screenshot}{data.without_screenshot} URL{data.without_screenshot === 1 ? '' : 's'} have none yet.{/if}
			</p>
		</div>
	{:else}
		<p class="body-small text-on-surface-variant">
			{data.total} with a screenshot, in {alike.length} group{alike.length === 1 ? '' : 's'} that look alike and {unique.length} on {unique.length === 1 ? 'its' : 'their'} own{#if data.without_screenshot}; {data.without_screenshot} URL{data.without_screenshot === 1 ? '' : 's'} without one{/if}.
		</p>
		{#each alike as g, i (g.items[0].id)}
			{@const all = open.has(i)}
			<section aria-label="{g.size} that look alike" class="flex flex-col gap-2">
				<div class="flex items-center gap-2">
					<h3 class="flex-1 title-small text-on-surface">
						{g.size} look alike
						<span class="body-small font-normal text-on-surface-variant">· like <Spoilers text={String(g.items[0].fields.title || g.items[0].label)} /></span>
					</h3>
					{#if g.size > SHOWN}<Button variant="text" icon={all ? 'expand_less' : 'expand_more'} onclick={() => toggle(i)}>{all ? 'Show fewer' : `Show all ${g.size}`}</Button>{/if}
				</div>
				<ul class="grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-3">
					{#each all ? g.items : g.items.slice(0, SHOWN) as a (a.id)}{@render card(a)}{/each}
				</ul>
			</section>
		{/each}
		{#if unique.length}
			<section aria-label="On their own" class="flex flex-col gap-2">
				<h3 class="title-small text-on-surface">{alike.length ? 'On their own' : 'Screenshots'} <span class="body-small font-normal text-on-surface-variant">· {unique.length}</span></h3>
				<ul class="grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-3">
					{#each unique as a (a.id)}{@render card(a)}{/each}
				</ul>
			</section>
		{/if}
	{/if}
</div>
