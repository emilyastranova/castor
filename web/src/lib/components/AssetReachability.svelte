<script lang="ts">
	import type { Reachability, ReachCell, Scope, SegmentRule, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, IconButton, Select, TextField } from '$lib/m3';
	import { assetTypes, show } from '$lib/assetTypes.svelte';
	import { absoluteTime, relativeTime } from '$lib/format';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { canEdit } from '$lib/session';
	import Spoilers from './Spoilers.svelte';

	/** Which network segments reach each asset, and what each sees: the same scan from agents in
	 *  different segments (or ordinary runs over time), side by side. Segmentation rules say what a
	 *  segment must not reach; a run that does records a finding. */
	interface Props {
		projectId: UUID;
		scope: Scope;
		onopenasset: (id: UUID) => void;
		/** One fan-out run's results only (?batch=). */
		batch?: string | null;
		onclearbatch?: () => void;
	}
	let { projectId, scope, onopenasset, batch = null, onclearbatch }: Props = $props();

	let type = $state('');
	let where = $state('');
	let scopeFilter = $state('');
	let differs = $state(false);
	let data = $state<Reachability | null>(null);

	let seq = 0;
	async function load() {
		const mine = ++seq;
		try {
			const res = await api().assets.reachability(projectId, { type: type || undefined, where, scope: scopeFilter || undefined, batch: batch ?? undefined, differs });
			if (mine === seq) data = res;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load reachability');
		}
	}
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		void [type, where, scopeFilter, differs, batch];
		clearTimeout(timer);
		timer = setTimeout(() => void load(), 200);
	});
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.project_id !== projectId || e.name !== 'assets.changed') return;
			clearTimeout(timer);
			timer = setTimeout(() => void load(), 600);
		})
	);
	$effect(() => void assetTypes.load());

	const ORDER: Record<string, string[]> = {
		host: ['status', 'os', 'hostname'],
		service: ['state', 'name', 'product', 'version', 'tls'],
		url: ['status', 'title', 'webserver', 'cert_subject'],
		share: ['access']
	};
	function summary(type: string, cell: ReachCell, diff: string[]): { text: string; odd: boolean }[] {
		const names = ORDER[type] ?? Object.keys(cell.fields);
		const out = names.filter((n) => cell.fields[n] !== undefined).map((n) => ({ text: show(cell.fields[n]), odd: diff.includes(n) }));
		if (cell.fields.look !== undefined && diff.includes('look')) out.push({ text: 'looks different', odd: true });
		return out;
	}

	// ---- rules ----
	let rulesOpen = $state(false);
	let drafts = $state<SegmentRule[]>([]);
	let saving = $state(false);
	let checking = $state(false);
	let segmentChoices = $derived([...new Set([...(data?.agent_segments ?? []), ...(data?.segments ?? []), ...drafts.map((d) => d.from)].filter(Boolean))].sort());
	function editRules() {
		drafts = JSON.parse(JSON.stringify(data?.rules ?? []));
		if (!drafts.length) addRule();
		rulesOpen = true;
	}
	function addRule() {
		drafts = [...drafts, { name: '', from: segmentChoices[0] ?? '', type: 'service', where: '', scope: scope.groups[0]?.name ?? null, severity: 'medium' }];
	}
	async function saveRules() {
		saving = true;
		try {
			await api().assets.setSegmentation(projectId, drafts.filter((d) => d.name.trim() || d.from.trim()));
			rulesOpen = false;
			await load();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save the rules');
		} finally {
			saving = false;
		}
	}
	async function checkNow() {
		checking = true;
		try {
			const r = await api().assets.checkSegmentation(projectId);
			snackbar.show(r.reached ? `${r.reached} reached that shouldn't be${r.new ? `; ${r.new} new finding${r.new === 1 ? '' : 's'}` : ', all already findings'}` : 'Nothing a segment reaches breaks a rule');
			await load();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not check the rules');
		} finally {
			checking = false;
		}
	}
	const typeName = (t?: string | null) => (t ? (assetTypes.get(t)?.plural.toLowerCase() ?? t) : 'assets');
	const describe = (r: SegmentRule) =>
		`${r.from} must not reach ${typeName(r.type)}${r.scope ? (r.scope === '*' ? ' in scope' : ` in ${r.scope}`) : ''}${r.where ? ` where ${r.where}` : ''}`;
	const SEVERITY_TONE: Record<string, string> = { critical: 'bg-error text-on-error', high: 'bg-error-container text-on-error-container', medium: 'bg-tertiary-container text-on-tertiary-container', low: 'bg-secondary-container text-on-secondary-container', info: 'bg-surface-container-highest text-on-surface-variant' };
</script>

<div class="flex flex-col gap-4">
	<section class="flex flex-col gap-2 rounded-md bg-surface-container-low px-4 py-3" aria-label="Segmentation rules">
		<div class="flex flex-wrap items-center gap-2">
			<Icon name="lan" size={18} class="text-on-surface-variant" />
			<h3 class="flex-1 label-large text-on-surface">Segmentation rules</h3>
			{#if canEdit()}
				{#if data?.rules.length}<Button variant="text" icon="rule" loading={checking} onclick={checkNow}>Check now</Button>{/if}
				<Button variant="text" icon={data?.rules.length ? 'edit' : 'add'} onclick={editRules}>{data?.rules.length ? 'Edit rules' : 'Add a rule'}</Button>
			{/if}
		</div>
		{#if data?.rules.length}
			<ul class="flex flex-col gap-1">
				{#each data.rules as r (r.id)}
					<li class="flex flex-wrap items-center gap-2 body-medium">
						<span class="rounded-xs px-1.5 py-px label-small {SEVERITY_TONE[r.severity]}">{r.severity}</span>
						<span class="text-on-surface">{r.name}</span>
						<span class="body-small text-on-surface-variant">· {describe(r)}</span>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="body-small text-on-surface-variant">Say what a segment must not reach (“Guest must not reach services in Management”): a run from there that does records a vulnerability finding, which triggers and reports pick up.</p>
		{/if}
	</section>

	<div class="flex flex-wrap items-center gap-2">
		<label class="flex min-w-[14rem] flex-1 items-center gap-2 rounded-full bg-surface-container-high px-4">
			<Icon name="filter_list" size={20} class="text-on-surface-variant" />
			<input class="h-11 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter: words, port:22, status=200" bind:value={where} autocomplete="off" spellcheck="false" />
			{#if where}<IconButton icon="close" label="Clear" size="sm" onclick={() => (where = '')} />{/if}
		</label>
		<Select label="Type" bind:value={type} class="w-40" options={[{ value: '', label: 'Every type' }, ...assetTypes.list.filter((t) => t.name !== 'vulnerability').map((t) => ({ value: t.name, label: t.label }))]} />
		{#if scope.groups.length}
			<Select label="Scope" bind:value={scopeFilter} class="w-40" options={[{ value: '', label: 'Anywhere' }, { value: '*', label: 'In scope' }, ...scope.groups.map((g) => ({ value: g.name, label: g.name })), { value: '-', label: 'Outside scope' }]} />
		{/if}
		<button type="button" class="flex items-center gap-1.5 rounded-full border px-3 py-1 label-large {differs ? 'border-transparent bg-secondary-container text-on-secondary-container' : 'border-outline-variant text-on-surface-variant'}" aria-pressed={differs} onclick={() => (differs = !differs)}>
			<Icon name="difference" size={16} />Only differences
		</button>
		{#if batch}
			<span class="flex items-center gap-1 rounded-full bg-primary-container py-1 pr-1 pl-3 label-large text-on-primary-container">
				One fan-out run
				{#if onclearbatch}<IconButton icon="close" label="Show every run" size="sm" onclick={onclearbatch} />{/if}
			</span>
		{/if}
	</div>

	{#if !data}
		<p class="py-10 text-center body-medium text-on-surface-variant">Loading…</p>
	{:else if !data.segments.length}
		<div class="flex flex-col items-center gap-2 py-16 text-center text-on-surface-variant">
			<Icon name="lan" size={40} />
			<p class="body-large">No runs from a network segment yet.</p>
			<p class="max-w-lg body-medium">
				Give agents their <strong>network segment</strong> on the Agents page (“VLAN 20 / user LAN”). Every run on them records it, so the same scan from several segments (a card run on every agent with some tags) shows here side by side: what each reaches and what each sees.
			</p>
		</div>
	{:else if !data.rows.length}
		<p class="py-10 text-center body-medium text-on-surface-variant">{differs ? 'The segments agree on everything shown.' : 'Nothing matches.'}</p>
	{:else}
		<div class="overflow-x-auto rounded-md border border-outline-variant/60">
			<table class="w-full body-medium">
				<thead class="bg-surface-container-low text-left body-small text-on-surface-variant">
					<tr>
						<th class="sticky left-0 z-[1] bg-surface-container-low px-3 py-2 font-medium">Asset</th>
						{#each data.segments as seg (seg)}<th class="min-w-40 px-3 py-2 font-medium"><span class="flex items-center gap-1"><Icon name="lan" size={14} />{seg}</span></th>{/each}
					</tr>
				</thead>
				<tbody>
					{#each data.rows as row (row.asset.id)}
						<tr class="border-t border-outline-variant/50 align-top">
							<td class="sticky left-0 z-[1] bg-surface px-3 py-2">
								<button type="button" class="flex items-center gap-1.5 text-left font-mono text-[13px] break-all text-on-surface hover:text-primary hover:underline" onclick={() => onopenasset(row.asset.id)}>
									<Icon name={assetTypes.get(row.asset.type)?.icon ?? 'radar'} size={15} class="shrink-0 text-on-surface-variant" />{row.asset.label}
								</button>
								<span class="mt-0.5 flex flex-wrap gap-1">
									{#each row.asset.scope_groups as g (g)}<span class="rounded-xs bg-primary-container px-1.5 py-px label-small text-on-primary-container">{g}</span>{/each}
									{#each row.rules as r (r)}<span class="inline-flex items-center gap-0.5 rounded-xs bg-error-container px-1.5 py-px label-small text-on-error-container" title="Breaks this segmentation rule"><Icon name="gpp_bad" size={12} />{r}</span>{/each}
									{#if row.differs.length && !row.rules.length}<span class="rounded-xs border border-outline-variant px-1.5 py-px label-small text-on-surface-variant" title="What the segments disagree on">differs: {row.differs.join(', ')}</span>{/if}
								</span>
							</td>
							{#each data.segments as seg (seg)}
								{@const c = row.cells[seg]}
								<td class="px-3 py-2">
									{#if !c}
										<span class="body-small text-on-surface-variant/70" title="No run from here covered it">—</span>
									{:else if c.state === 'seen'}
										<span class="flex flex-col gap-0.5 rounded-sm bg-primary-container/40 px-2 py-1" title="{c.task ? c.task.title : ''} {c.agent ? `· ${c.agent}` : ''} · {absoluteTime(c.at)}">
											<span class="flex items-center gap-1 label-medium text-on-surface"><Icon name="check_circle" size={14} class="text-primary" />Reached</span>
											{#if summary(row.asset.type, c, row.differs).length}
												<span class="body-small text-on-surface-variant">
													{#each summary(row.asset.type, c, row.differs) as part, i (i)}{i ? ' · ' : ''}<span class={part.odd ? 'font-medium text-tertiary' : ''}><Spoilers text={part.text} /></span>{/each}
												</span>
											{/if}
											<span class="body-small text-on-surface-variant/80">{relativeTime(c.at)}</span>
										</span>
									{:else}
										<span class="flex flex-col gap-0.5 rounded-sm border border-dashed border-outline-variant px-2 py-1" title="A run from here covered it and didn't find it · {absoluteTime(c.at)}">
											<span class="flex items-center gap-1 label-medium text-on-surface-variant"><Icon name="block" size={14} />Not reached</span>
											<span class="body-small text-on-surface-variant/80">{relativeTime(c.at)}</span>
										</span>
									{/if}
								</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if data.truncated}<p class="body-small text-on-surface-variant">Showing the first 2000; filter to narrow it down.</p>{/if}
	{/if}
</div>

<Dialog bind:open={rulesOpen} title="Segmentation rules" icon="lan" size="lg">
	<div class="flex flex-col gap-4 pt-1 pb-2">
		<p class="body-medium text-on-surface-variant">Each rule says a segment must not reach some assets. A run from that segment that reaches one records a vulnerability finding (tool “segmentation”) on it.</p>
		{#each drafts as d, i (i)}
			<fieldset class="flex flex-col gap-2 rounded-md border border-outline-variant px-3 py-3">
				<div class="flex flex-wrap items-end gap-2">
					<TextField label="Name" bind:value={d.name} class="min-w-48 flex-[2]" placeholder="e.g. Management from the guest network" />
					<Select label="Severity" value={d.severity} onchange={(v) => (d.severity = v as SegmentRule['severity'])} class="w-32" options={['info', 'low', 'medium', 'high', 'critical'].map((s) => ({ value: s, label: s }))} />
					<IconButton icon="delete" label="Remove this rule" onclick={() => (drafts = drafts.filter((_, j) => j !== i))} />
				</div>
				<div class="flex flex-wrap items-end gap-2">
					{#if segmentChoices.length}
						<Select label="The segment" value={d.from} onchange={(v) => (d.from = v)} class="min-w-44 flex-1" options={segmentChoices.map((s) => ({ value: s, label: s }))} />
					{:else}
						<TextField label="The segment" bind:value={d.from} class="min-w-44 flex-1" placeholder="An agent's segment" />
					{/if}
					<span class="pb-3 body-medium text-on-surface-variant">must not reach</span>
					<Select label="Type" value={d.type ?? ''} onchange={(v) => (d.type = v || null)} class="w-40" options={[{ value: '', label: 'Anything' }, ...assetTypes.list.filter((t) => t.name !== 'vulnerability').map((t) => ({ value: t.name, label: t.plural }))]} />
					<Select label="In" value={d.scope ?? ''} onchange={(v) => (d.scope = v || null)} class="w-40" options={[{ value: '', label: 'Anywhere' }, { value: '*', label: 'Any scope group' }, ...scope.groups.map((g) => ({ value: g.name, label: g.name }))]} />
				</div>
				<TextField label="Only where (optional)" bind:value={d.where} placeholder="e.g. port:22, or name:ssh port:3389" />
			</fieldset>
		{/each}
		<div><Button variant="text" icon="add" onclick={addRule}>Add a rule</Button></div>
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (rulesOpen = false)}>Cancel</Button>
		<Button loading={saving} onclick={saveRules}>Save</Button>
	{/snippet}
</Dialog>
