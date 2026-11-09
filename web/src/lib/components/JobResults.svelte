<script lang="ts">
	import type { Job, JobOutput, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import OutputCard from './OutputCard.svelte';
	import ParserFix from './ParserFix.svelte';

	// Errors that mean there was nothing to read (outputs.NO_INPUT), not a format the parser
	// doesn't know: a new parser can't help with these.
	const NO_INPUT = ['no file matched', 'is too large to parse', 'the tool wrote nothing', 'could not read the artifact'];
	const mismatch = (error: string | null) => !!error && !NO_INPUT.some((m) => error.includes(m));
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
			<OutputCard name={o.name} type={o.type} value={o.value} error={o.error} meta={o.sources.join(', ')} metaTitle="Parsed by {o.parser}">
				{#snippet actions()}
					<IconButton icon="refresh" label="Parse again" size="sm" disabled={parsing} onclick={reparse} />
				{/snippet}
			</OutputCard>
			{#if mismatch(o.error)}<ParserFix jobId={job.id} output={o.name} parser={o.parser} />{/if}
		{/each}
	{/if}
</div>
