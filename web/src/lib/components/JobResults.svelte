<script lang="ts">
	import type { HostResult, Job, JobOutput, ServiceResult, UrlResult, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { toCsv } from '$lib/csv';
	import { Button, Icon, IconButton, Progress } from '$lib/m3';

	interface Props {
		job: Job;
		/** Called whenever the outputs (re)load, so the parent can show a count. */
		onload?: (outputs: JobOutput[]) => void;
	}
	let { job, onload }: Props = $props();

	let outputs = $state<JobOutput[]>([]);
	let loading = $state(true);
	let parsing = $state(false);
	let showDown = $state(false);
	let finished = $derived(!['queued', 'dispatched', 'running'].includes(job.status));

	$effect(() => {
		const id: UUID = job.id;
		let cancelled = false;
		loading = true;
		const unsubscribe = realtime.subscribe((e) => {
			if (e.name !== 'job.outputs') return;
			const d = e.data as { job_id: UUID; outputs: JobOutput[] };
			if (d.job_id !== id) return;
			outputs = d.outputs;
			loading = false;
			onload?.(outputs);
		});
		api()
			.jobs.outputs(id)
			.then((o) => {
				if (cancelled) return;
				outputs = o;
				onload?.(o);
			})
			.finally(() => {
				if (!cancelled) loading = false;
			});
		return () => {
			cancelled = true;
			unsubscribe();
		};
	});

	async function reparse() {
		parsing = true;
		try {
			outputs = await api().jobs.parse(job.id);
			onload?.(outputs);
			snackbar.show('Results parsed again');
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not parse results');
		} finally {
			parsing = false;
		}
	}

	// ---- table shapes per output type ----
	interface Table {
		header: string[];
		rows: unknown[][];
		dimmed: boolean[];
		mono: boolean[];
	}

	function table(o: JobOutput): Table | null {
		if (o.type === 'list<host>') {
			const all = o.value as HostResult[];
			const hosts = showDown ? all : all.filter((h) => h.status === 'up');
			return {
				header: ['IP', 'Hostnames', 'Status'],
				rows: hosts.map((h) => [h.ip, h.hostnames.join(', '), h.status]),
				dimmed: hosts.map((h) => h.status !== 'up'),
				mono: [true, true, false]
			};
		}
		if (o.type === 'list<service>') {
			const services = o.value as ServiceResult[];
			return {
				header: ['Host', 'Port', 'Proto', 'Service', 'Product', 'Version'],
				rows: services.map((s) => [s.host, s.port, s.proto, s.name, s.product, s.version]),
				dimmed: services.map((s) => s.state !== 'open'),
				mono: [true, true, false, false, false, false]
			};
		}
		if (o.type === 'list<url>') {
			const urls = o.value as UrlResult[];
			return {
				header: ['URL', 'Status', 'Length', 'Words'],
				rows: urls.map((u) => [u.url, u.status, u.length, u.words]),
				dimmed: urls.map(() => false),
				mono: [true, false, false, false]
			};
		}
		return null;
	}

	const downCount = (o: JobOutput) =>
		o.type === 'list<host>' && Array.isArray(o.value) ? (o.value as HostResult[]).filter((h) => h.status !== 'up').length : 0;

	function summary(o: JobOutput): string {
		if (o.error || o.value === null) return '';
		if (o.type === 'list<host>') {
			const hosts = o.value as HostResult[];
			const up = hosts.filter((h) => h.status === 'up').length;
			return `${up} up${hosts.length > up ? ` of ${hosts.length}` : ''}`;
		}
		if (o.type === 'list<service>') {
			const services = o.value as ServiceResult[];
			const hosts = new Set(services.map((s) => s.host)).size;
			return `${services.length} open port${services.length === 1 ? '' : 's'} on ${hosts} host${hosts === 1 ? '' : 's'}`;
		}
		if (Array.isArray(o.value)) return `${o.value.length} item${o.value.length === 1 ? '' : 's'}`;
		return '';
	}

	function csvFor(o: JobOutput): string {
		const t = table(o);
		if (t) return toCsv(t.header, t.rows);
		if (Array.isArray(o.value)) return toCsv([o.name], o.value.map((v) => [v]));
		return toCsv([o.name], [[o.value]]);
	}

	async function copyCsv(o: JobOutput) {
		await navigator.clipboard.writeText(csvFor(o));
		snackbar.show(`Copied ${o.name} as CSV`);
	}

	async function copyList(o: JobOutput, column = 0) {
		const t = table(o);
		const values = t ? t.rows.map((r) => String(r[column])) : (o.value as unknown[]).map(String);
		await navigator.clipboard.writeText([...new Set(values)].join('\n'));
		snackbar.show(`Copied ${new Set(values).size} value${values.length === 1 ? '' : 's'}`);
	}
</script>

<div class="flex min-h-0 flex-1 flex-col gap-3 overflow-auto rounded-md bg-surface-container p-3">
	{#if loading}
		<div class="flex flex-1 items-center justify-center"><Progress variant="circular" size={24} /></div>
	{:else if !finished}
		<div class="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
			<Icon name="hourglass_top" size={36} class="text-on-surface-variant" />
			<p class="body-medium text-on-surface-variant">Results appear here when the run finishes.</p>
		</div>
	{:else if outputs.length === 0}
		<div class="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
			<Icon name="table_view" size={36} class="text-on-surface-variant" />
			<p class="body-medium text-on-surface-variant">
				{job.status === 'cancelled' ? 'The run was cancelled, so nothing was parsed.' : 'No results were parsed for this run yet.'}
			</p>
			{#if job.status !== 'cancelled'}
				<Button variant="tonal" icon="refresh" disabled={parsing} onclick={reparse}>Parse results</Button>
			{/if}
		</div>
	{:else}
		{#each outputs as o (o.name)}
			{@const t = o.error ? null : table(o)}
			<section class="flex flex-col overflow-hidden rounded-md border border-outline-variant bg-surface">
				<header class="flex min-h-11 items-center gap-2 border-b border-outline-variant py-1 pr-1 pl-3">
					<span class="title-small text-on-surface">{o.name}</span>
					<span class="rounded-xs bg-surface-container-highest px-1.5 py-0.5 font-mono text-[11px] text-on-surface-variant">{o.type}</span>
					{#if summary(o)}<span class="body-small text-on-surface-variant">{summary(o)}</span>{/if}
					<span class="flex-1"></span>
					<span class="hidden truncate body-small text-on-surface-variant sm:inline" title="Parsed by {o.parser}">{o.sources.join(', ')}</span>
					{#if !o.error && o.value !== null}
						{#if downCount(o)}
							<Button variant="text" onclick={() => (showDown = !showDown)}>{showDown ? 'Hide' : 'Show'} {downCount(o)} down</Button>
						{/if}
						{#if Array.isArray(o.value)}
							<IconButton icon="format_list_bulleted" label="Copy {t ? t.header[0] : 'values'} list" size="sm" onclick={() => copyList(o)} />
						{/if}
						<IconButton icon="csv" label="Copy as CSV" size="sm" onclick={() => copyCsv(o)} />
					{/if}
					<IconButton icon="refresh" label="Parse again" size="sm" disabled={parsing} onclick={reparse} />
				</header>

				{#if o.error}
					<div class="flex items-start gap-2 bg-error-container/40 px-3 py-2.5 text-on-error-container">
						<Icon name="error" size={20} class="mt-px shrink-0 text-error" />
						<p class="body-medium">Couldn't parse: {o.error.replace(/\.+$/, '')}.</p>
					</div>
				{:else if t}
					{#if t.rows.length === 0}
						<p class="px-3 py-3 body-medium text-on-surface-variant italic">Nothing found.</p>
					{:else}
						<div class="max-h-96 overflow-auto">
							<table class="w-full border-collapse text-left">
								<thead class="sticky top-0 bg-surface-container-low">
									<tr>
										{#each t.header as h (h)}<th class="px-3 py-2 label-medium whitespace-nowrap text-on-surface-variant">{h}</th>{/each}
									</tr>
								</thead>
								<tbody>
									{#each t.rows as row, i (i)}
										<tr class="border-t border-outline-variant/60 hover:bg-on-surface/4 {t.dimmed[i] ? 'opacity-55' : ''}">
											{#each row as value, c (c)}
												<td class="px-3 py-1.5 align-top body-medium text-on-surface {t.mono[c] ? 'font-mono text-[12.5px]' : ''} {c < row.length - 1 ? 'whitespace-nowrap' : 'min-w-48 [overflow-wrap:anywhere]'}">
													{value ?? ''}
												</td>
											{/each}
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				{:else if Array.isArray(o.value)}
					<div class="flex max-h-72 flex-wrap gap-1.5 overflow-auto p-3">
						{#each o.value as item, i (i)}
							<span class="rounded-sm border border-outline-variant px-2 py-0.5 font-mono text-[12.5px] break-all text-on-surface">{item}</span>
						{:else}
							<p class="body-medium text-on-surface-variant italic">Nothing found.</p>
						{/each}
					</div>
				{:else}
					<pre class="max-h-72 overflow-auto px-3 py-2.5 font-mono text-[12.5px] whitespace-pre-wrap text-on-surface">{String(o.value)}</pre>
				{/if}
			</section>
		{/each}
	{/if}
</div>
