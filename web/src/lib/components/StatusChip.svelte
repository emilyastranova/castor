<script lang="ts">
	import type { Job, JobStatus } from '$lib/api';
	import { Icon } from '$lib/m3';

	interface Props {
		job: Job | null | undefined;
		compact?: boolean;
	}
	let { job, compact = false }: Props = $props();

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
</script>

{#if job && m}
	<span class="inline-flex h-6 items-center gap-1 rounded-full pr-2 pl-1.5 label-small {m.cls}" title={job.error ?? m.label}>
		<Icon name={m.icon} size={14} filled class={m.spin ? 'animate-spin' : ''} />
		{#if !compact}{m.label}{/if}
		{#if job.exit_code !== null && job.exit_code !== 0}<span class="font-mono">·{job.exit_code}</span>{/if}
	</span>
{/if}
