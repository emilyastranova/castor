<script lang="ts">
	import type { PlaybookRun, PlaybookStepSpec, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { relativeTime, absoluteTime, duration } from '$lib/format';
	import { STEP_STATUS } from '$lib/playbook';
	import PlaybookVisualizer from './PlaybookVisualizer.svelte';
	import StepDecision from './StepDecision.svelte';
	import TransformDetails from './TransformDetails.svelte';

	/** A playbook run inside its card (Phase 3h): progress, decisions, the live graph and the
	 *  steps. Opening a step shows its own card on top (onopenstep). */
	interface Props {
		runId: UUID;
		editable?: boolean;
		onopenstep: (taskId: UUID) => void;
	}
	let { runId, editable = false, onopenstep }: Props = $props();

	let run = $state<PlaybookRun | null>(null);
	let failed = $state<string | null>(null);
	$effect(() => {
		const id = runId;
		run = null;
		failed = null;
		api()
			.playbookRuns.get(id)
			.then((r) => (run = r))
			.catch((err) => (failed = err instanceof ApiError ? err.message : 'Could not load the run'));
	});
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name === 'playbook_run.updated' && (e.data as PlaybookRun).id === runId) run = e.data as PlaybookRun;
		})
	);

	let spec = $derived(run?.spec_snapshot ?? null);
	let statuses = $derived(run?.step_status ?? {});
	let values = $derived(Object.values(statuses));
	let done = $derived(values.filter((s) => s === 'succeeded' || s === 'skipped' || s === 'not_taken').length);
	let waiting = $derived(spec ? spec.steps.filter((s) => statuses[s.id] === 'attention') : []);
	let items = $derived.by(() => {
		const map: Record<string, Task[]> = {};
		for (const t of run?.tasks ?? []) if (t.playbook_item != null && t.playbook_step_id) (map[t.playbook_step_id] ??= []).push(t);
		for (const list of Object.values(map)) list.sort((a, b) => (a.playbook_item ?? 0) - (b.playbook_item ?? 0));
		return map;
	});
	const RUN_STATE: Record<string, { label: string; icon: string; tone: string }> = {
		running: { label: 'Running', icon: 'progress_activity', tone: 'bg-primary-container text-on-primary-container' },
		succeeded: { label: 'Succeeded', icon: 'check_circle', tone: 'bg-primary-container text-on-primary-container' },
		failed: { label: 'Failed', icon: 'error', tone: 'bg-error-container text-on-error-container' },
		cancelled: { label: 'Cancelled', icon: 'cancel', tone: 'bg-surface-container-highest text-on-surface-variant' }
	};
	let runState = $derived(run ? (waiting.length ? { label: 'Needs you', icon: 'front_hand', tone: 'bg-tertiary-container text-on-tertiary-container' } : RUN_STATE[run.status]) : null);

	// ---- selecting steps (graph or list): a command step opens its card; others expand here ----
	let selected = $state<string | null>(null);
	function select(step: PlaybookStepSpec, task?: Task) {
		selected = step.id;
		const taskId = task?.id ?? run?.step_task_ids[step.id];
		if (taskId && !step.for_each) onopenstep(taskId);
	}

	let cancelling = $state(false);
	async function cancel() {
		if (!run) return;
		const ok = await confirmAction({
			title: `Cancel “${run.playbook_name}”?`,
			message: 'Running steps are stopped and nothing else starts.',
			confirmLabel: 'Cancel run'
		});
		if (!ok) return;
		cancelling = true;
		try {
			run = await api().playbookRuns.cancel(run.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not cancel the run');
		} finally {
			cancelling = false;
		}
	}
</script>

{#if failed}
	<p class="body-medium text-error">{failed}</p>
{:else if !run || !spec}
	<p class="body-medium text-on-surface-variant">Loading the run…</p>
{:else}
	<div class="flex flex-col gap-4">
		<!-- where the run is -->
		<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
			{#if runState}
				<span class="inline-flex items-center gap-1.5 rounded-sm px-2 py-1 label-large {runState.tone}">
					<Icon name={runState.icon} size={18} class={run.status === 'running' && !waiting.length ? 'motion-safe:animate-spin' : ''} />{runState.label}
				</span>
			{/if}
			<span class="body-medium text-on-surface">{done} of {values.length} steps done</span>
			<span class="body-small text-on-surface-variant" title={absoluteTime(run.created_at)}>
				started {relativeTime(run.created_at)}{run.finished_at ? ` · took ${duration(run.created_at, run.finished_at)}` : ''}
			</span>
			<span class="flex-1"></span>
			<Button variant="text" icon="open_in_new" href="/playbooks?run={run.id}">Open in Playbooks</Button>
			{#if editable && run.status === 'running'}
				<Button variant="tonal" icon="stop_circle" loading={cancelling} onclick={cancel}>Cancel run</Button>
			{/if}
		</div>
		<span class="block h-1.5 overflow-hidden rounded-full bg-surface-container-highest">
			<span class="block h-full rounded-full bg-primary transition-all" style:width="{values.length ? (100 * done) / values.length : 0}%"></span>
		</span>
		{#if Object.keys(run.inputs ?? {}).length}
			<div class="flex flex-wrap gap-1.5">
				{#each Object.entries(run.inputs) as [k, v] (k)}
					<span class="rounded-xs border border-outline-variant px-2 py-0.5 body-small text-on-surface-variant"><span class="text-on-surface">{k}</span> = <span class="font-mono">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span></span>
				{/each}
			</div>
		{/if}

		<!-- decisions waiting on a person -->
		{#each waiting as step (step.id)}
			<StepDecision {run} {step} result={run.step_results[step.id] ?? null} onresolved={(r) => (run = r)} />
		{/each}

		<!-- the graph -->
		<div class="overflow-hidden rounded-md border border-outline-variant/60">
			<PlaybookVisualizer spec={spec} {run} selectedStepId={selected} onStepSelect={select} />
		</div>

		<!-- the steps -->
		<div>
			<p class="mb-1.5 label-large text-on-surface-variant">Steps</p>
			<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container">
				{#each spec.steps as step (step.id)}
					{@const st = STEP_STATUS[statuses[step.id] ?? 'pending']}
					{@const its = items[step.id] ?? []}
					<li>
						<button type="button" class="state-layer flex w-full items-center gap-3 px-3 py-2 text-left {selected === step.id ? 'bg-secondary-container/60' : ''}" onclick={() => select(step)}>
							<Icon name={st.icon} size={18} class="shrink-0 {st.text} {statuses[step.id] === 'running' ? 'motion-safe:animate-spin' : ''}" />
							<Icon name={step.kind === 'transform' ? 'filter_alt' : step.for_each ? 'dynamic_feed' : 'terminal'} size={16} class="shrink-0 text-on-surface-variant" />
							<span class="min-w-0 flex-1 truncate body-medium text-on-surface">{step.name}</span>
							{#if step.for_each}<span class="shrink-0 body-small text-on-surface-variant">{its.length} item{its.length === 1 ? '' : 's'}</span>{/if}
							<span class="shrink-0 body-small {st.text}">{st.label}</span>
							{#if step.kind === 'command' && !step.for_each && run.step_task_ids[step.id]}<Icon name="chevron_right" size={18} class="shrink-0 text-on-surface-variant" />{/if}
						</button>
						{#if selected === step.id && step.for_each && its.length}
							<ul class="mb-2 ml-10 mr-3 flex flex-col rounded-sm bg-surface-container-high">
								{#each its as item (item.id)}
									<li>
										<button type="button" class="state-layer flex w-full items-center gap-2 px-3 py-1.5 text-left body-small" onclick={() => onopenstep(item.id)}>
											<span class="min-w-0 flex-1 truncate text-on-surface">{item.title}</span>
											<span class="shrink-0 text-on-surface-variant">{item.latest_job?.status ?? 'waiting'}</span>
											<Icon name="chevron_right" size={16} class="text-on-surface-variant" />
										</button>
									</li>
								{/each}
							</ul>
						{/if}
						{#if selected === step.id && step.kind === 'transform'}
							<div class="mb-2 ml-10 mr-3">
								<TransformDetails {step} result={run.step_results[step.id] ?? null} status={statuses[step.id] ?? null} />
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		</div>
	</div>
{/if}
