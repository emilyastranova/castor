<script lang="ts">
	import type { CommandDefinition, Job, ProjectSummary, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon, Select } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { currentProject } from '$lib/currentProject.svelte';
	import CommandArgsForm from '../CommandArgsForm.svelte';
	import VarBindings from '../VarBindings.svelte';
	import JobConsole from '../JobConsole.svelte';
	import JobResults from '../JobResults.svelte';

	/** Try an HTTP, MCP or plugin action from the Library: fill its arguments and credentials,
	 *  send it once in a project, and watch the run here. It runs on a card made for it and
	 *  archived at once (Archived cards keeps it with its run). */
	interface Props {
		command: CommandDefinition;
	}
	let { command }: Props = $props();

	let projects = $state<ProjectSummary[]>([]);
	let projectId = $state<UUID>('');
	let args = $state<Record<string, unknown>>({});
	let errors = $state<Record<string, string>>({});
	let bindings = $state<Record<string, UUID>>({});
	let sending = $state(false);
	let job = $state<Job | null>(null);
	let taskId = $state<UUID | null>(null);

	$effect(() => {
		api()
			.projects.list()
			.then((list) => {
				projects = list;
				if (!projectId) projectId = list.find((p) => p.id === currentProject.id)?.id ?? list[0]?.id ?? '';
			})
			.catch(() => (projects = []));
	});
	$effect(() => {
		void command.id;
		args = Object.fromEntries((command.spec.args ?? []).filter((a) => a.default !== undefined && a.default !== null).map((a) => [a.name, a.default]));
		errors = {};
		bindings = {};
		job = null;
		taskId = null;
	});
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name === 'job.updated' && job && (e.data as Job).id === job.id) job = e.data as Job;
		})
	);

	async function send() {
		if (!projectId) return;
		sending = true;
		errors = {};
		try {
			const r = await api().commands.try(command.id, { project_id: projectId, args, var_bindings: bindings });
			taskId = r.task_id;
			job = await api().jobs.get(r.job_id);
		} catch (err) {
			const detail = err instanceof ApiError ? (err.detail as { errors?: Record<string, string> } | null) : null;
			if (detail?.errors) errors = detail.errors;
			else snackbar.error(err instanceof ApiError ? err.message : 'Could not send it');
		} finally {
			sending = false;
		}
	}
	let finished = $derived(!!job && !['approval', 'queued', 'dispatched', 'running', 'waiting'].includes(job.status));
</script>

<div class="flex flex-col gap-4">
	<p class="max-w-[70ch] body-medium text-on-surface-variant">
		Send it once to see what comes back. It runs like a card's run (limits, approvals and masked secrets included) on a card made for it,
		archived straight away so the board stays clear; Archived cards keeps it with its run.
	</p>
	<Select label="In the project" bind:value={projectId} options={projects.map((p) => ({ value: p.id, label: `${p.codename} · ${p.name}` }))} />
	{#if command.spec.args?.length}
		<CommandArgsForm spec={command.spec} bind:values={args} bind:errors {projectId} />
	{/if}
	{#if command.spec.vars?.length && projectId}
		{#key projectId}
			<VarBindings slots={command.spec.vars} {bindings} {projectId} onchange={(b) => (bindings = b)} />
		{/key}
	{/if}
	<div class="flex flex-wrap items-center gap-3">
		<Button icon="send" loading={sending} disabled={!projectId || (!!job && !finished)} onclick={send}>{job ? 'Send again' : 'Send it'}</Button>
		{#if command.spec.confirm}<span class="inline-flex items-center gap-1 body-small text-on-surface-variant"><Icon name="front_hand" size={16} />It asks before each run: someone approves it on its card.</span>{/if}
		{#if taskId && job}
			<a class="body-medium text-primary underline-offset-4 hover:underline" href="/projects/{projectId}?task={taskId}">Open its card</a>
		{/if}
	</div>
	{#if job}
		{#key job.id}
			<JobConsole {job} title="Try: {command.name}" />
		{/key}
		{#if finished}
			<JobResults {job} />
		{/if}
	{/if}
</div>
