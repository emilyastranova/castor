<script lang="ts">
	import Spoilers from '$lib/components/Spoilers.svelte';
	import type { AssetChange, Scope, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Chip, Icon, Select, TextField } from '$lib/m3';
	import { assetTypes } from '$lib/assetTypes.svelte';
	import { CHANGE_KIND, dayLabel, describeChange } from '$lib/assetChanges';
	import { toCsv } from '$lib/csv';
	import { absoluteTime } from '$lib/format';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { enter } from '$lib/motion';

	/** The project's assets over a date range, from what its runs saw: new hosts and services,
	 *  values that changed (a title, a version, a status), what a scan covered and didn't find
	 *  any more, and what came back. Every run's results together, wherever they ran. */
	interface Props {
		projectId: UUID;
		scope: Scope;
		onopenasset: (id: UUID) => void;
	}
	let { projectId, scope, onopenasset }: Props = $props();

	const RANGES = [
		{ value: '1', label: 'Last 24 hours' },
		{ value: '7', label: 'Last 7 days' },
		{ value: '30', label: 'Last 30 days' },
		{ value: '90', label: 'Last 90 days' },
		{ value: 'custom', label: 'Between dates…' }
	];
	const KINDS = ['new', 'changed', 'gone', 'back'] as const;

	let range = $state('7');
	let from = $state('');
	let to = $state('');
	let type = $state('');
	let scopeFilter = $state('');
	let command = $state('');
	let agent = $state('');
	let kinds = $state<Set<string>>(new Set());

	let items = $state<AssetChange[]>([]);
	let truncated = $state(false);
	let loading = $state(true);
	let live = $state(false);

	function span(): { since: string; until?: string } | null {
		if (range !== 'custom') return { since: new Date(Date.now() - Number(range) * 86_400_000).toISOString() };
		if (!from) return null;
		const since = new Date(`${from}T00:00:00`);
		const until = to ? new Date(`${to}T23:59:59.999`) : undefined;
		return { since: since.toISOString(), until: until?.toISOString() };
	}

	async function load(quiet = false) {
		const w = span();
		if (!w) return;
		if (!quiet) loading = true;
		try {
			const res = await api().assets.changes(projectId, { ...w, type: type || undefined, scope: scopeFilter || undefined });
			live = quiet;
			items = res.items;
			truncated = res.truncated;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load the changes');
		} finally {
			loading = false;
		}
	}
	$effect(() => {
		void [range, from, to, type, scopeFilter];
		void load();
	});
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.project_id !== projectId || e.name !== 'assets.changed') return;
			clearTimeout(timer);
			timer = setTimeout(() => void load(true), 600);
		})
	);
	$effect(() => void assetTypes.load());

	// The command and agent filters narrow what's loaded (their choices come from it).
	let commands = $derived([...new Set(items.map((i) => i.source.command).filter((c): c is string => !!c))].sort());
	let agents = $derived(
		[...new Map(items.filter((i) => i.source.agent_id).map((i) => [i.source.agent_id!, i.source.agent ?? 'a forgotten agent'])).entries()].sort((a, b) =>
			a[1].localeCompare(b[1])
		)
	);
	let shown = $derived(
		items.filter((i) => (!kinds.size || kinds.has(i.kind)) && (!command || i.source.command === command) && (!agent || i.source.agent_id === agent))
	);
	let tally = $derived(
		Object.fromEntries(KINDS.map((k) => [k, items.filter((i) => i.kind === k && (!command || i.source.command === command) && (!agent || i.source.agent_id === agent)).length]))
	);
	let days = $derived.by(() => {
		const out: { day: string; items: AssetChange[] }[] = [];
		for (const i of shown) {
			const day = dayLabel(i.at);
			if (out.at(-1)?.day !== day) out.push({ day, items: [] });
			out.at(-1)!.items.push(i);
		}
		return out;
	});

	function toggle(k: string) {
		const next = new Set(kinds);
		if (next.has(k)) next.delete(k);
		else next.add(k);
		kinds = next;
	}

	const typeLabel = (t: string) => assetTypes.get(t)?.label ?? t;
	function what(i: AssetChange): string {
		if (i.kind === 'new') return `New ${typeLabel(i.asset.type).toLowerCase()}`;
		if (i.kind === 'gone') return 'Not seen';
		if (i.kind === 'back') return 'Seen again';
		return i.changes.map(describeChange).join(' · ') || 'Changed';
	}

	function exportCsv() {
		const csv = toCsv(
			['when', 'change', 'type', 'asset', 'details', 'card', 'command', 'agent'],
			shown.map((i) => [
				i.at,
				CHANGE_KIND[i.kind].label,
				typeLabel(i.asset.type),
				i.asset.label,
				i.kind === 'changed' || i.kind === 'back' ? i.changes.map(describeChange).join('; ') : '',
				i.source.task?.title ?? '',
				i.source.command ?? '',
				i.source.agent ?? ''
			])
		);
		const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
		const a = document.createElement('a');
		a.href = url;
		a.download = `asset-changes-${new Date().toISOString().slice(0, 10)}.csv`;
		a.click();
		URL.revokeObjectURL(url);
	}
</script>

<div class="flex flex-col gap-4">
	<div class="flex flex-wrap items-end gap-2">
		<Select label="When" bind:value={range} options={RANGES} class="w-48" />
		{#if range === 'custom'}
			<TextField label="From" type="date" bind:value={from} class="w-44" />
			<TextField label="To" type="date" bind:value={to} class="w-44" />
		{/if}
		<Select
			label="Type"
			bind:value={type}
			class="w-44"
			options={[{ value: '', label: 'Every type' }, ...assetTypes.list.map((t) => ({ value: t.name, label: t.label }))]}
		/>
		{#if scope.groups.length}
			<Select
				label="Scope"
				bind:value={scopeFilter}
				class="w-44"
				options={[
					{ value: '', label: 'Anywhere' },
					{ value: '*', label: 'In scope' },
					...scope.groups.map((g) => ({ value: g.name, label: g.name })),
					{ value: '-', label: 'Outside scope' }
				]}
			/>
		{/if}
		{#if commands.length > 1}
			<Select label="Seen by" bind:value={command} class="w-52" options={[{ value: '', label: 'Any command' }, ...commands.map((c) => ({ value: c, label: c }))]} />
		{/if}
		{#if agents.length > 1}
			<Select label="From agent" bind:value={agent} class="w-48" options={[{ value: '', label: 'Any agent' }, ...agents.map(([id, name]) => ({ value: id, label: name }))]} />
		{/if}
		<span class="flex-1"></span>
		<Button variant="text" icon="download" disabled={!shown.length} onclick={exportCsv}>CSV</Button>
	</div>

	<div class="flex flex-wrap gap-2" role="group" aria-label="Kinds of change">
		{#each KINDS as k (k)}
			<Chip variant="filter" selected={kinds.has(k)} onclick={() => toggle(k)}>
				{CHANGE_KIND[k].label}<span class="ml-1.5 tabular-nums opacity-80">{tally[k] ?? 0}</span>
			</Chip>
		{/each}
	</div>

	{#if range === 'custom' && !from}
		<p class="py-10 text-center body-medium text-on-surface-variant">Pick the first day to look from.</p>
	{:else if loading}
		<p class="py-10 text-center body-medium text-on-surface-variant">Loading…</p>
	{:else if !shown.length}
		<div class="flex flex-col items-center gap-2 py-16 text-center text-on-surface-variant">
			<Icon name="difference" size={40} />
			<p class="body-large">{items.length ? 'Nothing matches.' : 'No changes in this range.'}</p>
			<p class="max-w-md body-medium">
				Every run that reports assets adds to one picture: new hosts and ports, values that changed, and what a scan covered and no longer found (a ping sweep, a port scan of those ports, an httpx probe). Scans of other ranges or other ports don’t count against anything.
			</p>
		</div>
	{:else}
		<div class="flex flex-col gap-5">
			{#each days as d (d.day)}
				<section aria-label={d.day}>
					<h3 class="mb-1.5 label-large text-on-surface-variant">{d.day}</h3>
					<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md border border-outline-variant/60">
						{#each d.items as i (`${i.asset.id}:${i.at}:${i.kind}`)}
							{@const k = CHANGE_KIND[i.kind]}
							<li class="flex flex-wrap items-start gap-x-3 gap-y-1 px-3 py-2" in:enter={{ enabled: live }}>
								<Icon name={k.icon} size={18} class="mt-0.5 shrink-0 {k.tone}" />
								<div class="min-w-0 flex-1">
									<p class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
										<button type="button" class="font-mono text-[13px] break-all text-on-surface hover:text-primary hover:underline" onclick={() => onopenasset(i.asset.id)}>{i.asset.label}</button>
										<span class="body-small text-on-surface-variant">{typeLabel(i.asset.type)}</span>
										{#each i.asset.scope_groups as g (g)}<span class="rounded-xs bg-primary-container px-1.5 py-px label-small text-on-primary-container">{g}</span>{/each}
									</p>
									{#if i.pictures}
										<span class="mt-1 mb-0.5 flex items-center gap-1.5">
											{#if i.pictures[0]}<img src={api().attachments.thumbnail(i.pictures[0], 160)} alt="Before" title="Before" class="h-14 w-24 rounded-xs border border-outline-variant/60 object-cover object-top" loading="lazy" /><Icon name="arrow_forward" size={14} class="text-on-surface-variant" />{/if}
											<img src={api().attachments.thumbnail(i.pictures[1], 160)} alt="After" title="After" class="h-14 w-24 rounded-xs border border-outline-variant/60 object-cover object-top" loading="lazy" />
										</span>
									{/if}
									<p class="body-small {i.kind === 'changed' || i.kind === 'back' ? 'font-mono text-[12px] text-on-surface' : `font-medium ${k.tone}`}">
										{#if i.kind === 'back' && i.changes.some((c) => c.before !== null)}Seen again · {i.changes.filter((c) => c.before !== null).map(describeChange).join(' · ')}{:else}{what(i)}{/if}
									</p>
								</div>
								<p class="shrink-0 text-right body-small text-on-surface-variant">
									{#if i.source.task}<a href={i.source.task.link} class="text-primary hover:underline"><Spoilers text={i.source.task.title} /></a>{:else}{i.source.kind === 'manual' ? 'by hand' : i.source.kind}{/if}
									{#if i.source.agent}<span class="ml-1 inline-flex items-center gap-0.5 align-bottom"><Icon name="dns" size={12} />{i.source.agent}</span>{/if}
									<br /><span title={absoluteTime(i.at)}>{new Date(i.at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}</span>
								</p>
							</li>
						{/each}
					</ul>
				</section>
			{/each}
			{#if truncated}<p class="body-small text-on-surface-variant">Showing the newest 2000; narrow the range or filter by type.</p>{/if}
		</div>
	{/if}
</div>
