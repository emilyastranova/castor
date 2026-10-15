<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HostResult, ServiceResult, UrlResult } from '$lib/api';
	import { snackbar } from '$lib/snackbar.svelte';
	import { toCsv } from '$lib/csv';
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

	interface Table {
		header: string[];
		rows: unknown[][];
		dimmed: boolean[];
		mono: boolean[];
	}

	let table = $derived.by((): Table | null => {
		if (error || !Array.isArray(value)) return null;
		if (type === 'list<host>') {
			const all = value as HostResult[];
			const hosts = showDown ? all : all.filter((h) => h.status === 'up');
			return {
				header: ['IP', 'Hostnames', 'Status'],
				rows: hosts.map((h) => [h.ip, h.hostnames.join(', '), h.status]),
				dimmed: hosts.map((h) => h.status !== 'up'),
				mono: [true, true, false]
			};
		}
		if (type === 'list<service>') {
			const services = value as ServiceResult[];
			return {
				header: ['Host', 'Port', 'Proto', 'State', 'Service', 'Product', 'Version'],
				rows: services.map((s) => [s.host, s.port, s.proto, s.state, s.name, s.product, s.version]),
				dimmed: services.map((s) => !s.state.startsWith('open')),
				mono: [true, true, false, false, false, false, false]
			};
		}
		if (type === 'list<url>') {
			const urls = value as UrlResult[];
			return {
				header: ['URL', 'Status', 'Length', 'Words'],
				rows: urls.map((u) => [u.url, u.status, u.length, u.words]),
				dimmed: urls.map(() => false),
				mono: [true, false, false, false]
			};
		}
		return null;
	});

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
		if (Array.isArray(value)) return plural(value.length, 'item');
		return '';
	});

	function csv(): string {
		if (table) return toCsv(table.header, table.rows);
		if (Array.isArray(value)) return toCsv([name], value.map((v) => [v]));
		return toCsv([name], [[value]]);
	}

	async function copyCsv() {
		await navigator.clipboard.writeText(csv());
		snackbar.show(`Copied ${name} as CSV`);
	}

	async function copyList() {
		const values = table ? table.rows.map((r) => String(r[0])) : (value as unknown[]).map(String);
		const unique = [...new Set(values)];
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
				<IconButton icon="format_list_bulleted" label="Copy {table ? table.header[0] : 'values'} list" size="sm" onclick={copyList} />
			{/if}
			<IconButton icon="csv" label="Copy as CSV" size="sm" onclick={copyCsv} />
		{/if}
		{@render actions?.()}
	</header>

	{#if error}
		<div class="flex items-start gap-2 bg-error-container/40 px-3 py-2.5 text-on-error-container">
			<Icon name="error" size={20} class="mt-px shrink-0 text-error" />
			<p class="body-medium">Couldn't parse: {error.replace(/\.+$/, '')}.</p>
		</div>
	{:else if table}
		{#if table.rows.length === 0}
			<p class="px-3 py-3 body-medium text-on-surface-variant italic">Nothing found.</p>
		{:else}
			<div class="max-h-96 overflow-auto">
				<table class="w-full border-collapse text-left">
					<thead class="sticky top-0 bg-surface-container-low">
						<tr>
							{#each table.header as h (h)}<th class="px-3 py-2 label-medium whitespace-nowrap text-on-surface-variant">{h}</th>{/each}
						</tr>
					</thead>
					<tbody>
						{#each table.rows as row, i (i)}
							<tr class="border-t border-outline-variant/60 hover:bg-on-surface/4 {table.dimmed[i] ? 'opacity-55' : ''}">
								{#each row as cell, c (c)}
									<td class="px-3 py-1.5 align-top body-medium text-on-surface {table.mono[c] ? 'font-mono text-[12.5px]' : ''} {c < row.length - 1 ? 'whitespace-nowrap' : 'min-w-48 [overflow-wrap:anywhere]'}">
										{cell ?? ''}
									</td>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{:else if Array.isArray(value)}
		<div class="flex max-h-72 flex-wrap gap-1.5 overflow-auto p-3">
			{#each value as item, i (i)}
				<span class="rounded-sm border border-outline-variant px-2 py-0.5 font-mono text-[12.5px] break-all text-on-surface">{item}</span>
			{:else}
				<p class="body-medium text-on-surface-variant italic">Nothing found.</p>
			{/each}
		</div>
	{:else}
		<pre class="max-h-72 overflow-auto px-3 py-2.5 font-mono text-[12.5px] whitespace-pre-wrap text-on-surface">{String(value ?? '')}</pre>
	{/if}
</section>
