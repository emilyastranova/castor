<script lang="ts">
	import type { Job } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { absoluteTime, relativeTime } from '$lib/format';

	/** A service (a job that runs until stopped: a listener, relay, capture or log tail) on its
	 *  card: whether it's up, the lines it was told to watch for, and stopping it the way it's
	 *  built to stop (its signal, a wait for it to exit and free its ports, then a kill). */
	interface Props {
		job: Job;
		editable?: boolean;
	}
	let { job, editable = false }: Props = $props();

	let spec = $derived(job.service!);
	let stopping = $derived(job.progress?.stopping ?? null);
	let ready = $derived(job.progress?.ready_at ?? null);
	let matches = $derived([...(job.progress?.matches ?? [])].reverse().slice(0, 10));
	let phase = $derived(
		stopping
			? { label: stopping.restart ? 'Restarting' : 'Stopping', icon: 'pending', tone: 'bg-tertiary-container text-on-tertiary-container' }
			: job.status === 'running'
				? ready
					? { label: 'Ready', icon: 'sensors', tone: 'bg-primary-container text-on-primary-container' }
					: { label: 'Running', icon: 'progress_activity', tone: 'bg-primary-container text-on-primary-container' }
				: { label: 'Starting', icon: 'schedule', tone: 'bg-surface-container-highest text-on-surface-variant' }
	);

	let busy = $state(false);
	async function stop(restart: boolean) {
		busy = true;
		try {
			await api().jobs.stop(job.id, restart);
			snackbar.show(restart ? 'Restarting: it stops, then runs again' : `Stopping: SIG${spec.stop_signal}, up to ${spec.stop_timeout} s to exit`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not stop it');
		} finally {
			busy = false;
		}
	}
	async function kill() {
		const ok = await confirmAction({
			title: 'Force it to quit?',
			message: `It gets SIGTERM and 5 seconds, then it's killed, and the run counts as cancelled. Stop asks it to quit the way it's built to (SIG${spec.stop_signal}, up to ${spec.stop_timeout} s).`,
			confirmLabel: 'Force quit',
			danger: true
		});
		if (!ok) return;
		try {
			await api().jobs.cancel(job.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not stop it');
		}
	}
</script>

<div class="flex flex-col gap-3 rounded-md bg-surface-container p-4">
	<div class="flex flex-wrap items-center gap-x-3 gap-y-2">
		<span class="inline-flex items-center gap-1.5 rounded-sm px-2 py-1 label-large {phase.tone}">
			<Icon name={phase.icon} size={18} class={phase.icon === 'progress_activity' || phase.icon === 'pending' ? 'motion-safe:animate-spin' : ''} />{phase.label}
		</span>
		<span class="body-medium text-on-surface">Runs until stopped</span>
		<span class="body-small text-on-surface-variant">
			{#if ready}up {relativeTime(ready)}{:else if job.started_at}started {relativeTime(job.started_at)}{/if}
			· stops with SIG{spec.stop_signal} (up to {spec.stop_timeout} s)
		</span>
		<span class="flex-1"></span>
		{#if editable && !stopping && (job.status === 'running' || job.status === 'dispatched')}
			<Button variant="text" icon="restart_alt" loading={busy} onclick={() => stop(true)}>Restart</Button>
			<Button variant="tonal" icon="stop_circle" loading={busy} onclick={() => stop(false)}>Stop</Button>
		{/if}
		{#if editable && (stopping || job.status === 'queued')}
			<Button variant="text" icon="cancel" onclick={kill}>{job.status === 'queued' ? 'Cancel' : 'Force quit'}</Button>
		{/if}
	</div>
	{#if stopping}
		<p class="body-small text-on-surface-variant" title={absoluteTime(stopping.at)}>
			Asked to stop {relativeTime(stopping.at)}: it has up to {spec.stop_timeout} s to exit and free its ports before it's killed{stopping.restart ? ', then it runs again' : ''}.
		</p>
	{/if}
	{#if matches.length}
		<div>
			<p class="mb-1 label-large text-on-surface-variant">Seen in its output</p>
			<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-sm bg-surface-container-high">
				{#each matches as m (m.at + m.line)}
					<li class="flex items-baseline gap-3 px-3 py-1.5">
						{#if m.say}<span class="shrink-0 label-medium text-on-surface">{m.say}</span>{/if}
						<code class="min-w-0 flex-1 truncate font-mono text-[12.5px] text-on-surface-variant" title={m.line}>{m.line}</code>
						<span class="shrink-0 body-small text-on-surface-variant" title={absoluteTime(m.at)}>{relativeTime(m.at)}</span>
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</div>
