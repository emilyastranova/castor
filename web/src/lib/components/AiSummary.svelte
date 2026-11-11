<script lang="ts">
	import type { AiSummary, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { aiStatus } from '$lib/ai.svelte';
	import { relativeTime } from '$lib/format';
	import { Icon, IconButton } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import RichText from './RichText.svelte';

	/** The AI's few lines about a finished job or playbook run, or a manual card (server
	 *  summaries.py): what was found, what failed, what next. Written by itself in projects that
	 *  turn summaries on; anything finished can be summarised (again) from here. */
	interface Props {
		jobId?: UUID;
		runId?: UUID;
		/** A manual card: its own summary, from what people wrote on it. */
		taskId?: UUID;
		finished: boolean;
		projectId: UUID;
		editable?: boolean;
	}
	let { jobId, runId, taskId, finished, projectId, editable = false }: Props = $props();

	let summary = $state<AiSummary | null>(null);
	let asking = $state(false);
	$effect(() => void aiStatus.load());
	$effect(() => {
		const job = jobId;
		const run = runId;
		const card = taskId;
		summary = null;
		const load = job ? api().jobs.summary(job) : run ? api().playbookRuns.summary(run) : card ? api().tasks.summary(card) : null;
		load?.then((s) => {
			if (job === jobId && run === runId && card === taskId) summary = s;
		}).catch(() => {});
	});
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name !== 'summary.updated') return;
			const s = e.data as AiSummary;
			if ((jobId && s.job_id === jobId) || (runId && s.run_id === runId) || (taskId && !s.job_id && !s.run_id && s.task_id === taskId)) summary = s;
		})
	);

	async function write() {
		asking = true;
		try {
			summary = jobId ? await api().jobs.summarize(jobId) : runId ? await api().playbookRuns.summarize(runId) : await api().tasks.summarize(taskId!);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not write a summary');
		} finally {
			asking = false;
		}
	}
	let writing = $derived(asking || summary?.status === 'writing');
	let canWrite = $derived(editable && aiStatus.enabled && finished);
</script>

{#if summary && (summary.status !== 'failed' || canWrite)}
	<section class="rounded-md border border-outline-variant bg-surface-container px-4 py-3" aria-label="AI summary" aria-busy={writing}>
		<div class="flex items-center gap-1.5">
			<Icon name="auto_awesome" size={16} class="text-primary" />
			<h3 class="label-large text-on-surface">Summary</h3>
			{#if summary.status === 'done' && !writing}
				<span class="min-w-0 truncate body-small text-on-surface-variant" title={summary.model}>
					· {summary.automatic ? (taskId ? 'written when it was done' : 'written when it finished') : 'asked for'}{summary.updated_at ? `, ${relativeTime(summary.updated_at)}` : ''}
				</span>
			{/if}
			{#if canWrite && !writing}
				<IconButton icon="refresh" label="Write the summary again" size="sm" class="ml-auto" onclick={write} />
			{/if}
		</div>
		{#if writing}
			<p class="mt-1.5 flex items-center gap-2 body-medium text-on-surface-variant">
				<span class="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden="true"></span>{taskId ? 'Reading the card and its comments…' : 'Reading the results and the output…'}
			</p>
		{:else if summary.status === 'failed'}
			<p class="mt-1 body-small text-error">The AI couldn't write one: {summary.error}</p>
		{:else}
			<RichText text={summary.text} {projectId} class="mt-1 body-medium text-on-surface" />
			<p class="mt-1.5 body-small text-on-surface-variant">By {summary.model}. It can be wrong: check {taskId ? 'the card' : 'the results'} before acting on it.</p>
		{/if}
	</section>
{:else if canWrite}
	<button type="button" class="flex w-fit items-center gap-1 label-large text-primary hover:underline disabled:opacity-60" disabled={writing} onclick={write}>
		<Icon name="auto_awesome" size={16} />{writing ? 'Summarising…' : 'Summarise with AI'}
	</button>
{/if}
