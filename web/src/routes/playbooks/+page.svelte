<script lang="ts">
	import { onMount } from 'svelte';
	import type { PlaybookDefinition, PlaybookRun, PlaybookStepSpec, Project, Task, JobOutput, CommandDefinition } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Chip, Dialog, Icon, IconButton, Tabs, TextField, TopAppBar } from '$lib/m3';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { realtime } from '$lib/realtime.svelte';
	import PlaybookVisualizer from '$lib/components/PlaybookVisualizer.svelte';
	import RunPlaybookDialog from '$lib/components/RunPlaybookDialog.svelte';
	import OutputCard from '$lib/components/OutputCard.svelte';
	import TransformDetails from '$lib/components/TransformDetails.svelte';
	import StepConditions from '$lib/components/StepConditions.svelte';
	import StepDecision from '$lib/components/StepDecision.svelte';
	import { page } from '$app/state';
	import { STEP_STATUS } from '$lib/playbook';

	let { data } = $props();

	// svelte-ignore state_referenced_locally
	let playbooks = $state<PlaybookDefinition[]>(data.playbooks);
	// svelte-ignore state_referenced_locally
	let runs = $state<PlaybookRun[]>(data.runs);
	// svelte-ignore state_referenced_locally
	let projects = $state<Project[]>(data.projects);
	// svelte-ignore state_referenced_locally
	let commands = $state<CommandDefinition[]>(data.commands || []);

	let activeTab = $state('library'); // 'library' | 'runs'
	let filter = $state('');

	// Library selection
	// svelte-ignore state_referenced_locally
	let selectedPlaybookId = $state<string>(data.playbooks[0]?.id || '');
	let selectedPlaybook = $derived(playbooks.find((p) => p.id === selectedPlaybookId) || playbooks[0] || null);

	// Runs selection
	// svelte-ignore state_referenced_locally
	let selectedRunId = $state<string>(data.runs[0]?.id || '');
	let selectedRun = $derived(runs.find((r) => r.id === selectedRunId) || null);

	// Run Dialog
	let runDialogOpen = $state(false);
	let playbookToRun = $state<PlaybookDefinition | null>(null);

	// Step Inspector State
	let visualizerStepId = $state<string | null>(null);
	let selectedStep = $state<PlaybookStepSpec | null>(null);
	let selectedTask = $state<Task | null>(null);
	let selectedStepCommand = $derived(commands.find((c) => c.id === selectedStep?.command_ref) || null);
	let stepOutputs = $state<JobOutput[]>([]);
	let loadingOutputs = $state(false);
	let selectedResult = $derived(selectedStep ? (selectedRun?.step_results?.[selectedStep.id] ?? null) : null);
	let selectedStatus = $derived(selectedStep ? (selectedRun?.step_status?.[selectedStep.id] ?? 'pending') : 'pending');

	const taskFor = (run: PlaybookRun, stepId: string) => run.tasks.find((t) => t.playbook_step_id === stepId);
	const waiting = (run: PlaybookRun) => Object.values(run.step_status ?? {}).filter((s) => s === 'attention').length;

	// Notifications link to /playbooks?run=<id>&step=<step>: open that run and step.
	let linkedTo = '';
	$effect(() => {
		const runId = page.url.searchParams.get('run');
		const stepId = page.url.searchParams.get('step');
		const key = `${runId}:${stepId}`;
		if (!runId || key === linkedTo) return;
		linkedTo = key;
		void openLinked(runId, stepId);
	});

	async function openLinked(runId: string, stepId: string | null) {
		let run = runs.find((r) => r.id === runId);
		try {
			run = await api().playbookRuns.get(runId);
			runs = [run, ...runs.filter((r) => r.id !== runId)];
		} catch {
			if (!run) return snackbar.error('That playbook run no longer exists');
		}
		activeTab = 'runs';
		selectedRunId = run.id;
		const step = run.spec_snapshot.steps.find((st) => st.id === stepId);
		if (step) handleStepSelect(step, taskFor(run, step.id));
		else selectFirstStep(run);
	}

	function applyRun(updated: PlaybookRun) {
		runs = runs.map((r) => (r.id === updated.id ? updated : r));
		if (selectedStep) {
			const fresh = taskFor(updated, selectedStep.id);
			if (fresh) selectedTask = fresh;
		}
	}

	function selectFirstStep(run: PlaybookRun) {
		const first = run.spec_snapshot.steps[0];
		if (first) handleStepSelect(first, taskFor(run, first.id));
	}

	// Step Command Editor State
	let editingTaskCommand = $state('');
	let taskCommandDirty = $state(false);
	let savingTaskCommand = $state(false);
	let reRunningStep = $state(false);

	// Editor Dialog State
	let editorOpen = $state(false);
	let editing = $state<PlaybookDefinition | null>(null);
	let editorYaml = $state('');
	let editorError = $state<string | null>(null);
	let savingYaml = $state(false);

	const DEFAULT_PLAYBOOK_TEMPLATE = `id: custom-pipeline-flow
name: Custom Pipeline Flow
schema_version: 1
description: Example two-step automated workflow
tags: [automation, pipeline]
inputs:
  - name: targets
    type: string
    description: Target IP or subnet
    required: true
    example: 192.168.1.0/24
steps:
  - id: ping_sweep
    name: Ping Sweep
    command_ref: nmap-ping-sweep
    args:
      targets: "{{ inputs.targets }}"
  # A transform runs on the server between commands (no card): filter, pluck, format,
  # unique, sort, limit. Referencing a step adds it to depends_on automatically.
  - id: live_hosts
    name: Live hosts
    transform:
      input: "{{ steps.ping_sweep.outputs.hosts }}"
      ops:
        - filter: { field: status, equals: up }
        - pluck: ip
      output: { name: ips }
  - id: port_scan
    name: Service Scan
    command_ref: nmap-service-scan
    skip_if_empty: true
    args:
      targets: "{{ steps.live_hosts.outputs.ips }}"
`;

	onMount(() => {
		// Subscribe to realtime updates for live playbook progression
		const cleanup = realtime.subscribe(async (ev) => {
			if (ev.name === 'playbook_run.updated' || ev.name === 'playbook_run.started') {
				const updatedRun = ev.data as PlaybookRun;
				if (updatedRun && updatedRun.id) {
					runs = [updatedRun, ...runs.filter((r) => r.id !== updatedRun.id)];
					if (selectedRunId === updatedRun.id) {
						if (selectedStep) {
							const fresh = taskFor(updatedRun, selectedStep.id);
							if (fresh) {
								selectedTask = fresh;
								if (!taskCommandDirty) editingTaskCommand = fresh.command;
							}
						}
						// refresh selected step outputs if step is active
						if (selectedTask?.latest_job?.id) {
							loadOutputsForJob(selectedTask.latest_job.id);
						}
					}
				}
			} else if (ev.name === 'job.outputs' || ev.name === 'task.updated') {
				// Only events about the selected run's cards; playbook_run.updated covers the rest.
				const data = ev.data as { id?: string; task_id?: string };
				const taskId = ev.name === 'task.updated' ? data.id : data.task_id;
				if (selectedRun?.tasks.some((t) => t.id === taskId)) scheduleRefresh();
			}
		});
		return () => {
			clearTimeout(refreshTimer);
			cleanup();
		};
	});

	// A finishing job emits several events at once; refetch the run once for all of them.
	let refreshTimer: ReturnType<typeof setTimeout> | undefined;
	function scheduleRefresh() {
		clearTimeout(refreshTimer);
		refreshTimer = setTimeout(refreshSelectedRun, 150);
	}

	async function refreshSelectedRun() {
		if (!selectedRunId) return;
		try {
			const refreshed = await api().playbookRuns.get(selectedRunId);
			runs = runs.map((r) => (r.id === refreshed.id ? refreshed : r));
			if (selectedTask) {
				const fresh = refreshed.tasks.find((t) => t.id === selectedTask?.id);
				if (fresh) {
					selectedTask = fresh;
					if (!taskCommandDirty) editingTaskCommand = fresh.command;
				}
			}
			if (selectedTask?.latest_job?.id) loadOutputsForJob(selectedTask.latest_job.id);
		} catch {
			// run not found or error
		}
	}

	async function loadOutputsForJob(jobId: string) {
		loadingOutputs = true;
		try {
			stepOutputs = await api().jobs.outputs(jobId);
		} catch {
			stepOutputs = [];
		} finally {
			loadingOutputs = false;
		}
	}

	function handleStepSelect(step: PlaybookStepSpec, task?: Task) {
		visualizerStepId = step.id;
		selectedStep = step;
		selectedTask = task || null;
		editingTaskCommand = task?.command || '';
		taskCommandDirty = false;
		stepOutputs = [];
		if (task?.latest_job?.id) {
			loadOutputsForJob(task.latest_job.id);
		}
	}

	async function saveStepCommand() {
		if (!selectedTask) return;
		savingTaskCommand = true;
		try {
			const updated = await api().tasks.update(selectedTask.id, { command: editingTaskCommand });
			selectedTask = updated;
			if (selectedRun) {
				selectedRun.tasks = selectedRun.tasks.map((t) => (t.id === updated.id ? updated : t));
			}
			taskCommandDirty = false;
			snackbar.show('Task command saved');
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Failed to update command');
		} finally {
			savingTaskCommand = false;
		}
	}

	async function reRunStepTask() {
		if (!selectedTask) return;
		reRunningStep = true;
		try {
			if (taskCommandDirty && editingTaskCommand !== selectedTask.command) {
				const updated = await api().tasks.update(selectedTask.id, { command: editingTaskCommand });
				selectedTask = updated;
				if (selectedRun) {
					selectedRun.tasks = selectedRun.tasks.map((t) => (t.id === updated.id ? updated : t));
				}
				taskCommandDirty = false;
			}
			await api().tasks.run(selectedTask.id);
			if (selectedRunId) {
				const refreshed = await api().playbookRuns.get(selectedRunId);
				runs = runs.map((r) => (r.id === refreshed.id ? refreshed : r));
				const fresh = refreshed.tasks.find((t) => t.id === selectedTask?.id);
				if (fresh) selectedTask = fresh;
			}
			snackbar.show('Step re-run started');
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not re-run step');
		} finally {
			reRunningStep = false;
		}
	}

	function triggerRunPlaybook(pb: PlaybookDefinition) {
		playbookToRun = pb;
		runDialogOpen = true;
	}

	function handleRunStarted(newRun: PlaybookRun) {
		runs = [newRun, ...runs.filter((r) => r.id !== newRun.id)];
		selectedRunId = newRun.id;
		activeTab = 'runs';
		selectFirstStep(newRun);
	}

	async function cancelActiveRun(runId: string) {
		const ok = await confirmAction({
			title: 'Cancel Playbook Run?',
			message: 'This will stop in-progress jobs and prevent subsequent steps from starting.',
			confirmLabel: 'Cancel run'
		});
		if (!ok) return;

		try {
			const updated = await api().playbookRuns.cancel(runId);
			runs = runs.map((r) => (r.id === updated.id ? updated : r));
			snackbar.show('Playbook run cancelled');
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Failed to cancel run');
		}
	}

	function openNewEditor() {
		editing = null;
		editorYaml = DEFAULT_PLAYBOOK_TEMPLATE;
		editorError = null;
		editorOpen = true;
	}

	function openEditEditor(pb: PlaybookDefinition) {
		editing = pb;
		editorYaml = pb.source_yaml;
		editorError = null;
		editorOpen = true;
	}

	function duplicatePlaybook(pb: PlaybookDefinition) {
		editing = null;
		editorYaml = pb.source_yaml
			.replace(/^id:\s*.+$/m, `id: ${pb.id}-custom`)
			.replace(/^name:\s*(.+)$/m, 'name: $1 (Custom)');
		editorError = null;
		editorOpen = true;
	}

	async function savePlaybook() {
		savingYaml = true;
		editorError = null;
		try {
			const saved = editing
				? await api().playbooks.update(editing.id, editorYaml)
				: await api().playbooks.create(editorYaml);

			playbooks = [...playbooks.filter((p) => p.id !== saved.id), saved].sort((a, b) =>
				a.name.localeCompare(b.name)
			);
			selectedPlaybookId = saved.id;
			editorOpen = false;
			snackbar.show(editing ? `Saved “${saved.name}”` : `Created “${saved.name}”`);
		} catch (err) {
			editorError = err instanceof ApiError ? err.message : 'Could not save playbook';
		} finally {
			savingYaml = false;
		}
	}

	async function deletePlaybook(pb: PlaybookDefinition) {
		const ok = await confirmAction({
			title: `Delete Playbook “${pb.name}”?`,
			message: 'This playbook definition will be permanently deleted.',
			confirmLabel: 'Delete playbook'
		});
		if (!ok) return;

		try {
			await api().playbooks.remove(pb.id);
			playbooks = playbooks.filter((p) => p.id !== pb.id);
			if (selectedPlaybookId === pb.id) {
				selectedPlaybookId = playbooks[0]?.id || '';
			}
			editorOpen = false;
			snackbar.show(`Deleted playbook “${pb.name}”`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete playbook');
		}
	}

	let visiblePlaybooks = $derived(
		playbooks.filter((p) => {
			const q = filter.trim().toLowerCase();
			return (
				!q ||
				p.name.toLowerCase().includes(q) ||
				p.id.toLowerCase().includes(q) ||
				p.tags.some((t) => t.toLowerCase().includes(q))
			);
		})
	);

	const tabs = $derived([
		{ id: 'library', label: 'Playbook Library', icon: 'auto_mode', badge: playbooks.length },
		{ id: 'runs', label: 'Runs & Visualizer', icon: 'account_tree', badge: runs.length }
	]);
</script>

<svelte:head><title>Playbooks · Castor</title></svelte:head>

<TopAppBar
	title="Playbooks"
	subtitle="Multi-step command automation · dependency chaining & parallel execution"
>
	{#snippet actions()}
		<Button variant="filled" icon="add" onclick={openNewEditor}>
			New Playbook
		</Button>
	{/snippet}
</TopAppBar>

<div class="flex flex-1 flex-col overflow-hidden bg-surface">
	<!-- Tab Bar -->
	<div class="px-6 pt-1 bg-surface border-b border-outline-variant/60">
		<Tabs {tabs} bind:value={activeTab} />
	</div>

	<!-- TAB 1: Playbook Library -->
	{#if activeTab === 'library'}
		<div class="flex flex-1 overflow-hidden p-6 gap-6">
			<!-- Left Column: Playbook List & Search -->
			<div class="flex w-96 flex-col gap-4 shrink-0 pt-1">
				<TextField
					label="Filter playbooks"
					placeholder="Search by name or tag..."
					bind:value={filter}
					leadingIcon="search"
				/>

				<div class="flex flex-1 flex-col gap-3 overflow-y-auto pr-1">
					{#each visiblePlaybooks as pb (pb.id)}
						{@const isSelected = selectedPlaybookId === pb.id}
						<Card
							variant={isSelected ? 'filled' : 'outlined'}
							class="cursor-pointer transition-all duration-150 p-4 border {isSelected ? 'border-primary ring-2 ring-primary/20 bg-primary-container/20' : 'hover:border-outline'}"
							onclick={() => {
								selectedPlaybookId = pb.id;
								if (pb.spec.steps[0]) handleStepSelect(pb.spec.steps[0]);
							}}
						>
							<div class="flex items-start justify-between gap-2">
								<h3 class="font-medium title-medium text-on-surface leading-tight">
									{pb.name}
								</h3>
								<span class="rounded px-1.5 py-0.5 font-mono text-[10.5px] uppercase {pb.origin === 'builtin' ? 'bg-secondary-container text-on-secondary-container' : 'bg-tertiary-container text-on-tertiary-container'}">
									{pb.origin}
								</span>
							</div>

							<p class="mt-1 line-clamp-2 body-small text-on-surface-variant">
								{pb.description}
							</p>

							<div class="mt-3 flex flex-wrap gap-1">
								{#each pb.tags as tag (tag)}
									<span class="rounded-full bg-surface-container-highest px-2 py-0.5 text-[11px] text-on-surface-variant">
										#{tag}
									</span>
								{/each}
							</div>

							<div class="mt-4 flex items-center justify-between border-t border-outline-variant/50 pt-2 text-[12px] text-on-surface-variant">
								<span>
									{pb.spec.steps.filter((st) => st.kind !== 'transform').length} commands
									{#if pb.spec.steps.some((st) => st.kind === 'transform')}
										· {pb.spec.steps.filter((st) => st.kind === 'transform').length} transforms
									{/if}
								</span>
								<!-- svelte-ignore a11y_click_events_have_key_events -->
								<!-- svelte-ignore a11y_no_static_element_interactions -->
								<div class="flex items-center gap-1" onclick={(e) => e.stopPropagation()}>
									<IconButton
										icon="play_arrow"
										label="Run Playbook"
										size="sm"
										class="text-primary"
										onclick={() => triggerRunPlaybook(pb)}
									/>
									<IconButton
										icon="code"
										label="View / Edit YAML"
										size="sm"
										onclick={() => openEditEditor(pb)}
									/>
								</div>
							</div>
						</Card>
					{:else}
						<div class="py-12 text-center text-on-surface-variant body-medium">
							No playbooks match your filter.
						</div>
					{/each}
				</div>
			</div>

			<!-- Right Column: Visualizer Preview & Details -->
			<div class="flex flex-1 flex-col overflow-y-auto gap-5 rounded-2xl bg-surface-container-lowest p-6 border border-outline-variant/60">
				{#if selectedPlaybook}
					<div class="flex items-start justify-between gap-4">
						<div>
							<div class="flex items-center gap-3">
								<h2 class="title-large font-bold text-on-surface">
									{selectedPlaybook.name}
								</h2>
								<span class="rounded px-2 py-0.5 font-mono text-xs text-on-surface-variant bg-surface-container-highest">
									v{selectedPlaybook.version}
								</span>
							</div>
							<p class="mt-1 body-medium text-on-surface-variant max-w-2xl">
								{selectedPlaybook.description}
							</p>
						</div>

						<div class="flex items-center gap-2 shrink-0">
							<Button
								variant="outlined"
								icon="code"
								onclick={() => openEditEditor(selectedPlaybook)}
							>
								{selectedPlaybook.origin === 'builtin' ? 'View YAML' : 'Edit YAML'}
							</Button>
							<Button
								variant="filled"
								icon="play_arrow"
								onclick={() => triggerRunPlaybook(selectedPlaybook)}
							>
								Run Playbook
							</Button>
						</div>
					</div>

					<!-- Graphical DAG Visualizer -->
					<div class="flex flex-col gap-2">
						<div class="flex items-center justify-between">
							<span class="label-large text-on-surface">Execution Graph</span>
							<span class="body-small text-on-surface-variant">
								Zoomable & draggable canvas · Drag cards to reposition · Click to inspect
							</span>
						</div>
						<PlaybookVisualizer
							spec={selectedPlaybook.spec}
							commands={commands}
							bind:selectedStepId={visualizerStepId}
							onStepSelect={(s) => handleStepSelect(s)}
						/>
					</div>

					<!-- Step Inspector Panel -->
					{#if selectedStep}
						<div class="rounded-xl border border-outline-variant bg-surface p-5">
							<div class="flex items-center justify-between">
								<div class="flex items-center gap-2">
									<Icon
										name={selectedStep.kind === 'transform' ? 'filter_alt' : 'terminal'}
										size={20}
										class={selectedStep.kind === 'transform' ? 'text-tertiary' : 'text-primary'}
									/>
									<h4 class="title-medium font-semibold text-on-surface">
										{selectedStep.kind === 'transform' ? 'Transform' : 'Step'}: {selectedStep.name}
									</h4>
									<span class="rounded bg-surface-container-highest px-2 py-0.5 font-mono text-xs text-on-surface-variant">
										{selectedStep.command_ref ?? selectedStep.id}
									</span>
								</div>
								{#if selectedStep.depends_on && selectedStep.depends_on.length > 0}
									<div class="flex items-center gap-1.5 body-small text-on-surface-variant">
										<Icon name="link" size={16} />
										<span>Depends on: {selectedStep.depends_on.join(', ')}</span>
									</div>
								{:else}
									<span class="rounded bg-primary-container px-2 py-0.5 label-small text-on-primary-container">
										Entrypoint (Parallel ready)
									</span>
								{/if}
							</div>

							{#if selectedStep.description}
								<p class="mt-2 body-small text-on-surface-variant">
									{selectedStep.description}
								</p>
							{/if}

							{#if selectedStep.kind === 'transform'}
								<div class="mt-4">
									<TransformDetails step={selectedStep} />
								</div>
							{:else}
							<!-- Argument bindings -->
							<div class="mt-4">
								<span class="label-medium text-on-surface">Argument Templates</span>
								<div class="mt-2 grid grid-cols-2 gap-2">
									{#each Object.entries(selectedStep.args || {}) as [k, v] (k)}
										<div class="flex flex-col rounded-lg border border-outline-variant/60 bg-surface-container-low p-2">
											<span class="font-mono text-[11px] font-semibold text-on-surface">{k}</span>
											<span class="font-mono text-[12px] text-primary break-all">{String(v)}</span>
										</div>
									{/each}
									{#each Object.entries(selectedStep.vars || {}) as [k, v] (`var-${k}`)}
										<div class="flex flex-col rounded-lg border border-outline-variant/60 bg-surface-container-low p-2">
											<span class="flex items-center gap-1 font-mono text-[11px] font-semibold text-on-surface"><Icon name="key" size={12} />{k}</span>
											<span class="font-mono text-[12px] text-primary break-all">{String(v)}</span>
										</div>
									{/each}
								</div>
							</div>

							<!-- Parsed Outputs produced by this step's command -->
							{#if selectedStepCommand && selectedStepCommand.spec.outputs && selectedStepCommand.spec.outputs.length > 0}
								<div class="mt-4">
									<span class="label-medium text-on-surface">Parsed Outputs Produced</span>
									<div class="mt-2 flex flex-wrap gap-2">
										{#each selectedStepCommand.spec.outputs as out (out.name)}
											<div class="flex items-center gap-2 rounded-lg border border-tertiary/30 bg-tertiary-container/15 px-3 py-1.5">
												<Icon name="bolt" size={16} class="text-tertiary" />
												<div class="flex flex-col">
													<div class="flex items-center gap-1.5 font-mono text-xs font-semibold text-on-surface">
														<span>{out.name}</span>
														{#if out.type}
															<span class="font-normal text-on-surface-variant">({out.type})</span>
														{/if}
													</div>
													<div class="flex items-center gap-1 font-mono text-[10.5px] text-tertiary">
														<span>parser:</span>
														<span class="font-semibold">{out.parser || 'stdout'}</span>
													</div>
												</div>
											</div>
										{/each}
									</div>
								</div>
							{/if}
							{/if}
						</div>
					{/if}
				{/if}
			</div>
		</div>
	{/if}

	<!-- TAB 2: Runs & Visualizer -->
	{#if activeTab === 'runs'}
		<div class="flex flex-1 overflow-hidden p-6 gap-6">
			<!-- Left Column: Runs History List -->
			<div class="flex w-96 flex-col gap-3 overflow-hidden shrink-0">
				<div class="flex items-center justify-between px-1">
					<span class="title-small font-medium text-on-surface">Execution History</span>
					<span class="body-small text-on-surface-variant">{runs.length} runs</span>
				</div>

				<div class="flex flex-1 flex-col gap-2.5 overflow-y-auto pr-1">
					{#each runs as run (run.id)}
						{@const isSelected = selectedRunId === run.id}
						{@const project = projects.find((p) => p.id === run.project_id)}
						<Card
							variant={isSelected ? 'filled' : 'outlined'}
							class="cursor-pointer transition-all duration-150 p-3.5 border {isSelected ? 'border-primary ring-2 ring-primary/20 bg-primary-container/20' : 'hover:border-outline'}"
							onclick={() => {
								selectedRunId = run.id;
								selectFirstStep(run);
							}}
						>
							<div class="flex items-center justify-between gap-2">
								<h4 class="truncate font-medium title-small text-on-surface" title={run.playbook_name}>
									{run.playbook_name}
								</h4>
								{#if run.status === 'running' && waiting(run)}
									<span class="flex items-center gap-1 rounded-full bg-tertiary-container px-2 py-0.5 font-semibold text-on-tertiary-container label-small">
										<Icon name="front_hand" size={14} />
										Needs you{waiting(run) > 1 ? ` (${waiting(run)})` : ''}
									</span>
								{:else if run.status === 'running'}
									<span class="flex items-center gap-1 font-semibold text-primary label-small">
										<span class="size-2 animate-ping rounded-full bg-primary"></span>
										Running
									</span>
								{:else if run.status === 'succeeded'}
									<span class="flex items-center gap-1 font-medium text-primary label-small">
										<Icon name="check_circle" size={14} class="text-primary" />
										Done
									</span>
								{:else if run.status === 'failed'}
									<span class="flex items-center gap-1 font-medium text-error label-small">
										<Icon name="error" size={14} class="text-error" />
										Failed
									</span>
								{:else}
									<span class="flex items-center gap-1 text-on-surface-variant label-small">
										<Icon name="cancel" size={14} />
										Cancelled
									</span>
								{/if}
							</div>

							<div class="mt-2 flex items-center justify-between text-[11.5px] text-on-surface-variant">
								<span class="font-medium text-on-surface">
									{project ? project.codename : 'Project'}
								</span>
								<span>
									{new Date(run.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
								</span>
							</div>
						</Card>
					{:else}
						<div class="py-12 text-center text-on-surface-variant body-medium">
							No playbook runs yet. Launch one from the Library!
						</div>
					{/each}
				</div>
			</div>

			<!-- Right Column: Live Run Graphical Visualizer -->
			<div class="flex flex-1 flex-col overflow-y-auto gap-5 rounded-2xl bg-surface-container-lowest p-6 border border-outline-variant/60">
				{#if selectedRun}
					{@const project = projects.find((p) => p.id === selectedRun.project_id)}
					<!-- Run Header Banner -->
					<div class="flex items-start justify-between gap-4 border-b border-outline-variant/60 pb-4">
						<div>
							<div class="flex items-center gap-3">
								<h2 class="title-large font-bold text-on-surface">
									{selectedRun.playbook_name}
								</h2>
								{#if selectedRun.status === 'running'}
									<span class="rounded-full bg-primary/15 px-3 py-0.5 font-medium label-medium text-primary flex items-center gap-1.5">
										<span class="size-2 animate-ping rounded-full bg-primary"></span>
										Running
									</span>
								{:else if selectedRun.status === 'succeeded'}
									<span class="rounded-full bg-primary/15 px-3 py-0.5 font-medium label-medium text-primary flex items-center gap-1">
										<Icon name="check_circle" size={16} />
										Succeeded
									</span>
								{:else if selectedRun.status === 'failed'}
									<span class="rounded-full bg-error-container px-3 py-0.5 font-medium label-medium text-on-error-container flex items-center gap-1">
										<Icon name="error" size={16} />
										Failed
									</span>
								{:else}
									<span class="rounded-full bg-surface-container-highest px-3 py-0.5 label-medium text-on-surface-variant flex items-center gap-1">
										<Icon name="cancel" size={16} />
										Cancelled
									</span>
								{/if}
							</div>

							<div class="mt-1 flex items-center gap-3 text-on-surface-variant body-small">
								{#if project}
									<a href="/projects/{project.id}" class="text-primary hover:underline font-medium">
										Board: {project.codename} ({project.name})
									</a>
									<span>·</span>
								{/if}
								<span>Started {new Date(selectedRun.created_at).toLocaleTimeString()}</span>
								{#if selectedRun.finished_at}
									<span>·</span>
									<span>Finished {new Date(selectedRun.finished_at).toLocaleTimeString()}</span>
								{/if}
							</div>
						</div>

						<div class="flex items-center gap-2">
							{#if project}
								<Button
									variant="outlined"
									icon="view_kanban"
									href="/projects/{project.id}"
								>
									Open Board
								</Button>
							{/if}
							{#if selectedRun.status === 'running'}
								<Button
									variant="tonal"
									icon="cancel"
									class="text-error"
									onclick={() => cancelActiveRun(selectedRun.id)}
								>
									Cancel Run
								</Button>
							{/if}
						</div>
					</div>

					<!-- Graphical DAG Visualizer (Live Run Mode) -->
					<div class="flex flex-col gap-2">
						<div class="flex items-center justify-between">
							<span class="label-large text-on-surface">DAG Execution Visualizer</span>
							<span class="body-small text-on-surface-variant">
								Live DAG canvas · Pan & zoom · Drag cards to reposition · Click to inspect outputs
							</span>
						</div>
						<PlaybookVisualizer
							spec={selectedRun.spec_snapshot}
							run={selectedRun}
							commands={commands}
							bind:selectedStepId={visualizerStepId}
							onStepSelect={(s, t) => handleStepSelect(s, t)}
						/>
					</div>

					<!-- Selected Step Live Card & Parsed Outputs -->
					{#if selectedStep}
						<div class="flex flex-col gap-4">
							<!-- Step Header Banner -->
							<div class="flex items-center justify-between rounded-xl border border-outline-variant/60 bg-surface p-4">
								<div class="flex items-center gap-3">
									<Icon
										name={selectedStep.kind === 'transform' ? 'filter_alt' : 'terminal'}
										size={22}
										class={selectedStep.kind === 'transform' ? 'text-tertiary' : 'text-primary'}
									/>
									<div>
										<h4 class="title-medium font-semibold text-on-surface">
											{selectedStep.kind === 'transform' ? 'Transform' : 'Step'}: {selectedStep.name}
										</h4>
										<div class="flex items-center gap-2 text-on-surface-variant font-mono text-xs">
											{#if selectedStep.kind === 'transform'}
												<span>runs on the server · no card</span>
											{:else}
												<span>command: {selectedStep.command_ref}</span>
											{/if}
										</div>
									</div>
								</div>

								<span class="ml-auto mr-2 flex items-center gap-1 rounded bg-surface-container-highest px-2.5 py-1 label-small {STEP_STATUS[selectedStatus].text}">
									<Icon name={STEP_STATUS[selectedStatus].icon} size={14} />
									{STEP_STATUS[selectedStatus].label}
								</span>

								{#if selectedTask}
									<div class="flex items-center gap-2">
										{#if selectedRun}
											<a
												href="/projects/{selectedRun.project_id}?task={selectedTask.id}"
												class="inline-flex items-center gap-1 rounded bg-secondary-container px-2.5 py-1 label-small text-on-secondary-container hover:underline"
												title="Open full task card on board"
											>
												<Icon name="open_in_new" size={14} />
												Open Card
											</a>
										{/if}
									</div>
								{/if}
							</div>

							{#if selectedStatus === 'attention' && selectedRun}
								<StepDecision run={selectedRun} step={selectedStep} result={selectedResult} onresolved={applyRun} />
							{:else if selectedStatus === 'not_taken'}
								<div class="flex items-start gap-2 rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 body-medium text-on-surface">
									<Icon name="alt_route" size={18} class="text-on-surface-variant" />
									<div>Not taken: {selectedResult?.reason ?? 'a branch it needs was not taken'}.</div>
								</div>
							{/if}
							<StepConditions step={selectedStep} result={selectedResult} status={selectedStatus} />

							{#if selectedStep.kind === 'transform'}
								<div class="rounded-xl border border-outline-variant/60 bg-surface p-4">
									<TransformDetails step={selectedStep} result={selectedResult} status={selectedStatus} />
								</div>
							{:else}
							{#if (selectedResult?.error || selectedStatus === 'blocked' || selectedStatus === 'skipped') && selectedStatus !== 'attention'}
								<div class="flex items-start gap-2 rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 body-medium text-on-surface">
									<Icon name={STEP_STATUS[selectedStatus].icon} size={18} class={STEP_STATUS[selectedStatus].text} />
									<div>
										{#if selectedStatus === 'blocked'}
											An upstream step failed, so this step will not run. Fix and re-run the failed step to continue.
										{:else if selectedStatus === 'skipped'}
											Skipped: {selectedResult?.reason}.
										{:else}
											<span class="font-mono text-[12px] break-words">{selectedResult?.error}</span>
										{/if}
									</div>
								</div>
							{/if}

							<!-- Task Command Editor & Re-run Section -->
							{#if selectedTask}
								<div class="flex flex-col gap-3 rounded-xl border border-outline-variant/60 bg-surface p-4">
									<div class="flex flex-wrap items-center justify-between gap-2">
										<div class="flex items-center gap-2">
											<span class="label-large font-medium text-on-surface">Task Command</span>
											{#if selectedTask.latest_job?.status === 'failed'}
												<span class="rounded-full bg-error-container px-2 py-0.5 label-small text-on-error-container font-semibold">
													Failed {selectedTask.latest_job.exit_code != null ? `(exit code ${selectedTask.latest_job.exit_code})` : ''}
												</span>
											{:else if selectedTask.latest_job?.status === 'succeeded'}
												<span class="rounded-full bg-success/20 px-2 py-0.5 label-small text-success font-semibold">
													Succeeded
												</span>
											{:else if selectedTask.latest_job?.status === 'running'}
												<span class="rounded-full bg-primary/20 px-2 py-0.5 label-small text-primary font-semibold animate-pulse">
													Running...
												</span>
											{/if}
										</div>

										<div class="flex items-center gap-2">
											{#if taskCommandDirty}
												<Button
													variant="text"
													onclick={() => {
														editingTaskCommand = selectedTask?.command || '';
														taskCommandDirty = false;
													}}
												>
													Discard
												</Button>
												<Button
													icon="save"
													loading={savingTaskCommand}
													onclick={saveStepCommand}
												>
													Save
												</Button>
											{/if}
											<Button
												icon="play_arrow"
												loading={reRunningStep}
												disabled={!editingTaskCommand.trim() || selectedTask.latest_job?.status === 'running'}
												onclick={reRunStepTask}
											>
												{selectedTask.latest_job ? 'Re-run Step' : 'Run Step'}
											</Button>
										</div>
									</div>

									<TextField
										label="Command string"
										bind:value={editingTaskCommand}
										multiline
										rows={2}
										mono
										supporting="You can modify flags, arguments or targets here and re-run the step."
										oninput={() => (taskCommandDirty = true)}
									/>
								</div>
							{/if}

							<!-- Parsed Outputs Section -->
							<div class="flex flex-col gap-2">
								<div class="flex items-center justify-between">
									<span class="label-large text-on-surface">Parsed Step Outputs</span>
									{#if loadingOutputs}
										<span class="body-small text-primary animate-pulse">Loading outputs...</span>
									{/if}
								</div>

								{#if stepOutputs && stepOutputs.length > 0}
									<div class="flex flex-col gap-4">
										{#each stepOutputs as output (output.name)}
											<OutputCard
												name={output.name}
												type={output.type}
												value={output.value}
												error={output.error}
											/>
										{/each}
									</div>
								{:else if selectedTask?.latest_job?.status === 'running'}
									<div class="rounded-xl border border-dashed border-outline-variant p-8 text-center text-on-surface-variant">
										<span class="flex items-center justify-center gap-2 title-small text-primary">
											<span class="size-2.5 animate-ping rounded-full bg-primary"></span>
											Executing command on agent...
										</span>
										<p class="mt-1 body-small">Parsed results will appear automatically when the job completes.</p>
									</div>
								{:else if selectedTask?.latest_job?.status === 'failed'}
									<div class="rounded-xl border border-dashed border-error/50 bg-error-container/20 p-6 text-center text-on-surface body-medium">
										<p class="font-semibold text-error">Command execution failed (exit code {selectedTask.latest_job.exit_code ?? 'non-zero'}).</p>
										<p class="mt-1 text-xs text-on-surface-variant">
											Edit the command in the editor above and click <strong>Re-run Step</strong> to try again.
										</p>
									</div>
								{:else if selectedTask?.latest_job?.status === 'succeeded'}
									<div class="rounded-xl border border-dashed border-outline-variant p-6 text-center text-on-surface-variant body-medium">
										Command succeeded without structured parser outputs.
									</div>
								{:else if selectedStatus === 'pending'}
									<div class="rounded-xl border border-dashed border-outline-variant p-6 text-center text-on-surface-variant body-medium">
										Waiting for upstream dependencies before running.
									</div>
								{/if}
							</div>
							{/if}
						</div>
					{/if}
				{:else}
					<div class="flex flex-1 flex-col items-center justify-center py-20 text-on-surface-variant">
						<Icon name="schema" size={48} class="opacity-40" />
						<p class="mt-3 title-medium text-on-surface">No Run Selected</p>
						<p class="body-small">Select a past run from the list or launch a new playbook from the library.</p>
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>

<!-- Run Playbook Dialog -->
<RunPlaybookDialog
	bind:open={runDialogOpen}
	spec={playbookToRun ? playbookToRun.spec : null}
	{projects}
	onRun={handleRunStarted}
/>

<!-- YAML Editor Dialog -->
<Dialog
	bind:open={editorOpen}
	title={editing ? `Edit Playbook: ${editing.name}` : 'New Playbook'}
	size="lg"
>
	<p class="body-small text-on-surface-variant -mt-2 mb-3">
		{editing?.origin === 'builtin'
			? 'Built-in playbooks are read-only; duplicate to customize.'
			: 'Configure steps, dependencies, and command templates in YAML'}
	</p>
	<div class="flex flex-col gap-3 py-1">
		{#if editorError}
			<div class="rounded-lg bg-error-container p-3 body-medium text-on-error-container">
				{editorError}
			</div>
		{/if}

		<TextField
			label="Playbook YAML"
			multiline
			rows={18}
			mono
			bind:value={editorYaml}
			disabled={editing?.origin === 'builtin'}
		/>
	</div>

	{#snippet actions()}
		{#if editing?.origin === 'user'}
			<Button variant="text" class="text-error mr-auto" onclick={() => deletePlaybook(editing!)}>
				Delete
			</Button>
		{/if}
		{#if editing?.origin === 'builtin'}
			<Button variant="outlined" icon="content_copy" onclick={() => duplicatePlaybook(editing!)}>
				Duplicate to Custom
			</Button>
		{/if}
		<Button variant="text" onclick={() => (editorOpen = false)} disabled={savingYaml}>
			Close
		</Button>
		{#if editing?.origin !== 'builtin'}
			<Button variant="filled" icon="save" onclick={savePlaybook} loading={savingYaml}>
				Save Playbook
			</Button>
		{/if}
	{/snippet}
</Dialog>
