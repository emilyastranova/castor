<script lang="ts">
	import type { Job, JobChanges, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Chip, Icon, Progress, Select } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { anyChange, countWords, toMarkdown } from '$lib/changes';
	import ResultChanges from './ResultChanges.svelte';

	/** A card's run against an earlier one: what's new, gone and changed in its results. By
	 *  default the run before it on the card (or of the same step or command); any other can be picked. */
	interface Props {
		job: Job;
		/** Called with the totals once compared, so the parent can show a count. */
		onload?: (c: JobChanges) => void;
	}
	let { job, onload }: Props = $props();

	let data = $state<JobChanges | null>(null);
	let failed = $state<string | null>(null);
	let base = $state<UUID | null>(null);
	let show = $state<'all' | 'added' | 'removed' | 'changed'>('all');
	let finished = $derived(!['queued', 'dispatched', 'running'].includes(job.status));

	async function load(id: UUID, chosen: UUID | null) {
		try {
			const c = await api().jobs.changes(id, chosen);
			if (id !== job.id) return;
			data = c;
			failed = null;
			onload?.(c);
		} catch (err) {
			failed = err instanceof ApiError ? err.message : 'Could not compare the runs';
		}
	}
	$effect(() => {
		const id = job.id;
		data = null;
		base = null;
		show = 'all';
		if (!finished) return;
		void load(id, null);
		// Results parsed again (here or for the earlier run): compare again.
		return realtime.subscribe((e) => {
			if (e.name !== 'job.outputs') return;
			const jid = (e.data as { job_id: UUID }).job_id;
			if (jid === id || jid === data?.base?.job_id) void load(id, base);
		});
	});

	const HOW: Record<string, string> = { card: 'this card', step: 'the same playbook step', command: 'the same command', 'same command line': 'the same command line', chosen: 'chosen' };
	let options = $derived.by(() => {
		const list = [...(data?.candidates ?? [])];
		if (data?.base && !list.some((c) => c.job_id === data!.base!.job_id)) list.unshift(data.base);
		return list.map((c) => ({
			value: c.job_id,
			label: `${c.task_id === job.task_id ? 'This card' : c.card_title} · ${relativeTime(c.queued_at)}`,
			description: `${absoluteTime(c.queued_at)} · ${c.status}${c.task_id === job.task_id ? '' : ` · ${HOW[c.how] ?? c.how}`}`
		}));
	});
	function pick(id: string) {
		base = id as UUID;
		data = data ? { ...data, outputs: [] } : data;
		void load(job.id, base);
	}
	async function copy() {
		if (!data?.base) return;
		const when = absoluteTime(data.base.queued_at);
		try {
			await navigator.clipboard.writeText(toMarkdown(data.outputs, `Changes since the run of ${when}`));
			snackbar.show('Copied the changes as Markdown');
		} catch {
			snackbar.error('Copying needs clipboard access');
		}
	}
	const FILTERS = [
		{ id: 'all', label: 'All', key: null },
		{ id: 'added', label: 'New', key: 'added' },
		{ id: 'removed', label: 'Gone', key: 'removed' },
		{ id: 'changed', label: 'Changed', key: 'changed' }
	] as const;
</script>

<div class="flex min-h-0 flex-1 flex-col gap-3 overflow-auto rounded-md bg-surface-container p-3">
	{#if !finished}
		<div class="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
			<Icon name="hourglass_top" size={36} class="text-on-surface-variant" />
			<p class="body-medium text-on-surface-variant">What changed shows here when the run finishes.</p>
		</div>
	{:else if failed}
		<p class="body-medium text-error">{failed}</p>
	{:else if !data}
		<div class="flex flex-1 items-center justify-center"><Progress variant="circular" size={24} /></div>
	{:else if !data.base}
		<div class="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
			<Icon name="difference" size={36} class="text-on-surface-variant" />
			<p class="title-small text-on-surface">Nothing earlier to compare with</p>
			<p class="max-w-md body-medium text-on-surface-variant">
				This is the first run of it with results. Run it again later to see which hosts, ports, URLs or findings are new, gone or changed.
			</p>
		</div>
	{:else}
		<div class="flex flex-wrap items-end gap-x-3 gap-y-2">
			<Select label="Compared with" value={data.base.job_id} {options} onchange={pick} class="w-full max-w-sm" />
			<div class="flex min-h-14 min-w-52 flex-1 flex-col justify-center">
				<span class="title-small {anyChange(data.totals) ? 'text-on-surface' : 'text-on-surface-variant'}">{countWords(data.totals)}</span>
				<span class="body-small text-on-surface-variant">since {data.base.task_id === job.task_id ? 'the run' : `“${data.base.card_title}”`} {relativeTime(data.base.queued_at)}</span>
			</div>
			{#if anyChange(data.totals)}
				<Button variant="text" icon="content_copy" onclick={copy}>Copy as Markdown</Button>
			{/if}
		</div>
		{#if anyChange(data.totals)}
			<div class="flex flex-wrap gap-2" role="group" aria-label="Show">
				{#each FILTERS as f (f.id)}
					{@const n = f.key ? data.totals[f.key] : null}
					{#if !f.key || n}
						<Chip variant="filter" selected={show === f.id} onclick={() => (show = f.id)}>{f.label}{#if n}<span class="ml-1 tabular-nums opacity-70">{n}</span>{/if}</Chip>
					{/if}
				{/each}
			</div>
		{/if}
		{#if data.outputs.length}
			<ResultChanges outputs={data.outputs} {show} />
		{:else}
			<p class="body-medium text-on-surface-variant">Neither run has parsed results.</p>
		{/if}
	{/if}
</div>
