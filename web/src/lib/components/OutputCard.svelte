<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HostResult, ServiceResult, SmbHostResult } from '$lib/api';
	import { snackbar } from '$lib/snackbar.svelte';
	import { toCsv } from '$lib/csv';
	import { matches, parseFilter, sortRows, type ResultColumn, type Sort } from '$lib/resultFilter';
	import { Button, Icon, IconButton } from '$lib/m3';

	/** One parsed value: a job's output, or a live result on the Parsers page. */
	interface Props {
		name: string;
		type: string;
		value: unknown;
		error?: string | null;
		/** Small text on the right of the header (source files, timing...). */
		meta?: string;
		metaTitle?: string;
		/** Start with down hosts visible (they are hidden behind a toggle otherwise). */
		showDown?: boolean;
		/** Extra header buttons. */
		actions?: Snippet;
	}
	let { name, type, value, error = null, meta = '', metaTitle, showDown = $bindable(false), actions }: Props = $props();

	// ---- columns per type: raw keys (filter and sort use them) and how cells show ----
	const yes = (on: string, off: string) => (v: unknown) => (v === true ? on : v === false ? off : '');
	const COLUMNS: Record<string, ResultColumn[]> = {
		'list<host>': [
			{ key: 'ip', label: 'IP', mono: true },
			{ key: 'hostnames', label: 'Hostnames', mono: true },
			{ key: 'status', label: 'Status' }
		],
		'list<service>': [
			{ key: 'host', label: 'Host', mono: true },
			{ key: 'port', label: 'Port', mono: true },
			{ key: 'proto', label: 'Proto' },
			{ key: 'state', label: 'State' },
			{ key: 'name', label: 'Service' },
			{ key: 'product', label: 'Product' },
			{ key: 'version', label: 'Version' }
		],
		'list<smb_host>': [
			{ key: 'host', label: 'Host', mono: true },
			{ key: 'port', label: 'Port' },
			{ key: 'name', label: 'NetBIOS name', mono: true },
			{ key: 'domain', label: 'Domain', mono: true },
			{ key: 'os', label: 'OS' },
			{ key: 'signing', label: 'Signing', format: yes('Required', 'Not required (relayable)') },
			{ key: 'smbv1', label: 'SMBv1', format: yes('Enabled', 'Disabled') }
		],
		'list<url>': [
			{ key: 'url', label: 'URL', mono: true },
			{ key: 'status', label: 'Status' },
			{ key: 'length', label: 'Length' },
			{ key: 'words', label: 'Words' },
			{ key: 'title', label: 'Title' }
		]
	};
	const DIMMED: Record<string, (r: Record<string, unknown>) => boolean> = {
		'list<host>': (r) => r.status !== 'up',
		'list<service>': (r) => !String(r.state ?? '').startsWith('open'),
		'list<smb_host>': (r) => r.signing === true
	};
	const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
	/** A cell: the column's format, text as it is, lists joined, anything nested as compact JSON. */
	const cellOf = (c: ResultColumn, row: Record<string, unknown>): string => {
		const v = row[c.key];
		if (c.format) return c.format(v, row);
		if (v === null || v === undefined) return '';
		if (Array.isArray(v) && v.every((x) => typeof x !== 'object')) return v.join(', ');
		return typeof v === 'object' ? JSON.stringify(v) : String(v);
	};
	const MAX_COLUMNS = 24;

	/** Rows and their columns, before filtering: a table type, or any list of objects (list<object>). */
	let base = $derived.by((): { columns: ResultColumn[]; rows: Record<string, unknown>[] } | null => {
		if (error || !Array.isArray(value)) return null;
		const known = COLUMNS[type];
		if (known) {
			let rows = value.filter(isObject);
			if (type === 'list<host>' && !showDown) rows = rows.filter((h) => h.status === 'up');
			// Leave out columns no row has (e.g. a URL title only httpx gives).
			const used = known.filter((c) => c.key !== 'title' || rows.some((r) => r[c.key]));
			return { columns: used, rows };
		}
		if (type === 'list<object>' || (value.length > 0 && value.every(isObject))) {
			const rows = value.filter(isObject);
			const keys: string[] = [];
			for (const r of rows) for (const k of Object.keys(r)) if (!keys.includes(k) && keys.length < MAX_COLUMNS) keys.push(k);
			return { columns: keys.map((k) => ({ key: k, label: k })), rows };
		}
		return null;
	});
	/** A list of plain values (list<string>…) as one-column rows, so it filters and sorts too. */
	let plain = $derived(!error && Array.isArray(value) && !base ? value.map((v) => ({ value: v })) : null);
	const VALUE_COLUMNS: ResultColumn[] = [{ key: 'value', label: 'value', mono: true }];

	// ---- filter and sort (per card, not kept) ----
	let query = $state('');
	let sort = $state<Sort>(null);
	let columns = $derived(base?.columns ?? VALUE_COLUMNS);
	let filter = $derived(parseFilter(query, columns));
	let all = $derived(base?.rows ?? plain ?? []);
	let visible = $derived(sortRows(all.filter((r) => matches(r, filter, columns)), sort));
	let filtering = $derived(query.trim().length > 0);
	function sortBy(key: string) {
		sort = sort?.key !== key ? { key, dir: 1 } : sort.dir === 1 ? { key, dir: -1 } : null;
	}

	let downCount = $derived(
		type === 'list<host>' && Array.isArray(value) ? (value as HostResult[]).filter((h) => h.status !== 'up').length : 0
	);

	const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
	let summary = $derived.by(() => {
		if (error || value === null || value === undefined) return '';
		if (type === 'list<host>') {
			const hosts = value as HostResult[];
			const up = hosts.filter((h) => h.status === 'up').length;
			return `${up} up${hosts.length > up ? ` of ${hosts.length}` : ''}`;
		}
		if (type === 'list<service>') {
			const services = value as ServiceResult[];
			const open = services.filter((s) => s.state.startsWith('open')).length;
			const hosts = new Set(services.map((s) => s.host)).size;
			return `${plural(open, 'open port')}${services.length > open ? ` (${services.length} listed)` : ''} on ${plural(hosts, 'host')}`;
		}
		if (type === 'list<smb_host>') {
			const smbHosts = value as SmbHostResult[];
			const notSigning = smbHosts.filter((s) => !s.signing).length;
			return `${smbHosts.length} SMB host${smbHosts.length === 1 ? '' : 's'}${notSigning ? ` (${notSigning} signing disabled)` : ''}`;
		}
		if (type === 'list<object>' && Array.isArray(value)) return plural(value.length, 'row');
		if (Array.isArray(value)) return plural(value.length, 'item');
		return '';
	});

	// ---- copying: what's shown (filtered and sorted) ----
	function csv(): string {
		if (Array.isArray(value)) return toCsv(columns.map((c) => c.label), visible.map((r) => columns.map((c) => cellOf(c, r))));
		if (isObject(value)) return toCsv(['field', 'value'], Object.entries(value).map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : v]));
		return toCsv([name], [[value]]);
	}
	async function copyCsv() {
		await navigator.clipboard.writeText(csv());
		snackbar.show(`Copied ${filtering ? `${visible.length} filtered ` : ''}${name} as CSV`);
	}
	/** The first column's values (or the sorted column's), one per line, without repeats. */
	let listColumn = $derived(columns.find((c) => c.key === sort?.key && c.mono) ?? columns[0]);
	async function copyList() {
		const unique = [...new Set(visible.map((r) => cellOf(listColumn, r)).filter(Boolean))];
		await navigator.clipboard.writeText(unique.join('\n'));
		snackbar.show(`Copied ${plural(unique.length, 'value')}`);
	}
</script>

<section class="flex flex-col overflow-hidden rounded-md border border-outline-variant bg-surface">
	<header class="flex min-h-11 items-center gap-2 border-b border-outline-variant py-1 pr-1 pl-3">
		<span class="title-small text-on-surface">{name}</span>
		<span class="rounded-xs bg-surface-container-highest px-1.5 py-0.5 font-mono text-[11px] text-on-surface-variant">{type}</span>
		{#if summary}<span class="body-small text-on-surface-variant">{summary}</span>{/if}
		<span class="flex-1"></span>
		{#if meta}<span class="hidden truncate body-small text-on-surface-variant sm:inline" title={metaTitle}>{meta}</span>{/if}
		{#if !error && value !== null && value !== undefined}
			{#if downCount}
				<Button variant="text" onclick={() => (showDown = !showDown)}>{showDown ? 'Hide' : 'Show'} {downCount} down</Button>
			{/if}
			{#if Array.isArray(value)}
				<IconButton icon="format_list_bulleted" label="Copy the {listColumn.label} list{filtering ? ' (filtered)' : ''}" size="sm" onclick={copyList} />
			{/if}
			<IconButton icon="csv" label="Copy as CSV{filtering ? ' (filtered)' : ''}" size="sm" onclick={copyCsv} />
		{/if}
		{@render actions?.()}
	</header>

	{#if !error && Array.isArray(value) && all.length > 1}
		<div class="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-outline-variant/60 px-3 py-1.5">
			<label class="flex min-w-48 flex-1 items-center gap-2">
				<Icon name="filter_list" size={18} class="shrink-0 text-on-surface-variant" />
				<input
					class="h-8 min-w-0 flex-1 bg-transparent font-mono text-[12.5px] text-on-surface outline-none placeholder:font-sans placeholder:text-on-surface-variant"
					placeholder={base ? `Filter: words, or ${columns.find((c) => c.key === 'signing') ? 'signing:false' : `${columns[0].key}:…`}, port>1000, name!=x` : 'Filter'}
					aria-label="Filter {name}"
					autocomplete="off"
					spellcheck="false"
					bind:value={query}
					onkeydown={(e) => e.key === 'Escape' && (query = '')}
				/>
				{#if query}<button type="button" class="state-layer flex size-7 items-center justify-center rounded-full text-on-surface-variant" aria-label="Clear the filter" onclick={() => (query = '')}><Icon name="close" size={16} /></button>{/if}
			</label>
			{#if filtering}<span class="label-medium text-on-surface-variant tabular-nums">{visible.length} of {all.length}</span>{/if}
			{#if !base}
				<button type="button" class="state-layer flex items-center gap-1 rounded-sm px-1.5 py-0.5 label-medium text-on-surface-variant" onclick={() => sortBy('value')} aria-label="Sort the values">
					<Icon name={sort?.dir === -1 ? 'arrow_downward' : 'arrow_upward'} size={14} class={sort ? 'text-primary' : ''} />{sort ? (sort.dir === 1 ? 'A to Z' : 'Z to A') : 'Sort'}
				</button>
			{/if}
			{#if filter.unknown.length}
				<span class="w-full body-small text-error">No column {filter.unknown.join(', ')}: try {columns.map((c) => c.key).join(', ')}</span>
			{/if}
		</div>
	{/if}

	{#if error}
		<div class="flex items-start gap-2 bg-error-container/40 px-3 py-2.5 text-on-error-container">
			<Icon name="error" size={20} class="mt-px shrink-0 text-error" />
			<p class="body-medium">Couldn't parse: {error.replace(/\.+$/, '')}.</p>
		</div>
	{:else if base}
		{#if base.rows.length === 0}
			<p class="px-3 py-3 body-medium text-on-surface-variant italic">Nothing found.</p>
		{:else}
			<div class="max-h-96 overflow-auto">
				<table class="w-full border-collapse text-left">
					<thead class="sticky top-0 bg-surface-container-low">
						<tr>
							{#each base.columns as c (c.key)}
								{@const on = sort?.key === c.key}
								<th class="px-1.5 py-1 label-medium whitespace-nowrap text-on-surface-variant" aria-sort={on ? (sort?.dir === 1 ? 'ascending' : 'descending') : undefined}>
									<button type="button" class="group/sort state-layer inline-flex items-center gap-1 rounded-sm px-1.5 py-1 {on ? 'text-on-surface' : ''}" onclick={() => sortBy(c.key)} title={on ? (sort?.dir === 1 ? 'Click to reverse' : 'Click for the original order') : `Sort by ${c.label}`}>
										{c.label}
										<Icon name={on && sort?.dir === -1 ? 'arrow_downward' : 'arrow_upward'} size={14} class={on ? 'text-primary' : 'opacity-0 group-hover/sort:opacity-60'} />
									</button>
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each visible as row, i (i)}
							<tr class="border-t border-outline-variant/60 hover:bg-on-surface/4 {DIMMED[type]?.(row) ? 'opacity-55' : ''}">
								{#each base.columns as c, ci (c.key)}
									<td class="px-3 py-1.5 align-top body-medium text-on-surface {c.mono ? 'font-mono text-[12.5px]' : ''} {ci < base.columns.length - 1 ? 'whitespace-nowrap' : 'min-w-48 [overflow-wrap:anywhere]'}">
										{cellOf(c, row)}
									</td>
								{/each}
							</tr>
						{:else}
							<tr><td colspan={base.columns.length} class="px-3 py-3 body-medium text-on-surface-variant italic">Nothing matches the filter.</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{:else if Array.isArray(value)}
		<div class="flex max-h-72 flex-wrap gap-1.5 overflow-auto p-3">
			{#each visible as item, i (i)}
				<span class="rounded-sm border border-outline-variant px-2 py-0.5 font-mono text-[12.5px] break-all text-on-surface">{item.value}</span>
			{:else}
				<p class="body-medium text-on-surface-variant italic">{filtering ? 'Nothing matches the filter.' : 'Nothing found.'}</p>
			{/each}
		</div>
	{:else if isObject(value)}
		<table class="w-full border-collapse text-left">
			<tbody>
				{#each Object.entries(value) as [k, v] (k)}
					<tr class="border-t border-outline-variant/60 first:border-t-0">
						<td class="w-1/3 px-3 py-1.5 align-top font-mono text-[12.5px] text-on-surface-variant">{k}</td>
						<td class="px-3 py-1.5 align-top body-medium [overflow-wrap:anywhere] text-on-surface">{typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? '')}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{:else}
		<pre class="max-h-72 overflow-auto px-3 py-2.5 font-mono text-[12.5px] whitespace-pre-wrap text-on-surface">{String(value ?? '')}</pre>
	{/if}
</section>
