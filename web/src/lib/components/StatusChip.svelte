<script lang="ts">
	import type { BatchSummary, Job, JobStatus } from '$lib/api';
	import { Icon } from '$lib/m3';

	interface Props {
		job: Job | null | undefined;
		compact?: boolean;
		/** The card's latest run went to several agents: show the whole batch. */
		batch?: BatchSummary | null;
	}
	let { job, compact = false, batch = null }: Props = $props();

	const meta: Record<JobStatus, { icon: string; label: string; cls: string; spin?: boolean }> = {
		queued: { icon: 'schedule', label: 'Queued', cls: 'bg-surface-container-highest text-on-surface-variant' },
		dispatched: { icon: 'send', label: 'Dispatched', cls: 'bg-secondary-container text-on-secondary-container' },
		running: { icon: 'progress_activity', label: 'Running', cls: 'bg-tertiary-container text-on-tertiary-container', spin: true },
		succeeded: { icon: 'check_circle', label: 'Succeeded', cls: 'bg-primary-container text-on-primary-container' },
		failed: { icon: 'error', label: 'Failed', cls: 'bg-error-container text-on-error-container' },
		cancelled: { icon: 'cancel', label: 'Cancelled', cls: 'bg-surface-container-highest text-on-surface-variant' },
		lost: { icon: 'signal_disconnected', label: 'Lost', cls: 'bg-error-container text-on-error-container' }
	};
	let m = $derived(job ? meta[job.status] : null);
	let b = $derived(batch ? meta[batch.status] : null);
	let batchLabel = $derived(
		!batch
			? ''
			: batch.status === 'running'
				? `${batch.succeeded + batch.failed}/${batch.total} agents`
				: batch.status === 'failed'
					? `${batch.failed} of ${batch.total} agents failed`
					: `Succeeded on ${batch.total} agents`
	);
</script>

{#if batch && b}
	<span
		class="inline-flex h-6 items-center gap-1 rounded-full pr-2 pl-1.5 label-small {b.cls}"
		title="Ran on {batch.total} agents: {batch.succeeded} succeeded, {batch.failed} failed, {batch.active} still going"
	>
		<Icon name={b.icon} size={14} filled class={b.spin ? 'animate-spin' : ''} />
		{#if !compact}{batchLabel}{:else}{batch.succeeded + batch.failed}/{batch.total}{/if}
	</span>
{:else if job && m}
	<span class="inline-flex h-6 items-center gap-1 rounded-full pr-2 pl-1.5 label-small {m.cls}" title={job.error ?? m.label}>
		<Icon name={m.icon} size={14} filled class={m.spin ? 'animate-spin' : ''} />
		{#if !compact}{m.label}{/if}
		{#if job.exit_code !== null && job.exit_code !== 0}<span class="font-mono">·{job.exit_code}</span>{/if}
	</span>
{/if}
