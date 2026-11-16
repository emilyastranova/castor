<script lang="ts">
	import Spoilers from '$lib/components/Spoilers.svelte';
	import { maskSpoilers } from '$lib/spoilers';
	import { dndzone, type DndEvent, SHADOW_ITEM_MARKER_PROPERTY_NAME } from 'svelte-dnd-action';
	import type { Column, Job, PlaybookRun, PlaybookSpec, PlaybookStepStatus, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { goto } from '$app/navigation';
	import { Button, Icon, IconButton, Menu, MenuItem } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { people } from '$lib/people.svelte';
	import { DUE_TONE, dueInfo } from '$lib/due';
	import { kindIcon } from '$lib/taskStatus';
	import { enter, leave } from '$lib/motion';
	import AssigneeFace from './AssigneeFace.svelte';
	import ColumnGlyph from './ColumnGlyph.svelte';
	import GroupCardsDialog from './GroupCardsDialog.svelte';
	import ManySubtasksDialog from './ManySubtasksDialog.svelte';
	import PlaybookVisualizer from './PlaybookVisualizer.svelte';
	import StatusChip from './StatusChip.svelte';

	/** An activity card's subtasks (in its Subtasks tab): where each one is, adding new or existing
	 *  cards (or one per target), ordering by dragging, ticking manual ones done, running the command
	 *  ones (all at once or a few at a time), a graph when they wait on each other, and saving them
	 *  as a playbook. Opening a subtask shows its own card on top (onopen), like a run's steps. */
	interface Props {
		task: Task;
		columns: Column[];
		editable?: boolean;
		onopen: (taskId: UUID) => void;
	}
	let { task, columns, editable = false, onopen }: Props = $props();

	const FINISHED = new Set(['review', 'done', 'reported']);
	let kids = $state<Task[]>([]);
	let loaded = $state(false);
	let animate = $state(false);
	let parentId = $derived(task.id);

	async function load() {
		try {
			kids = await api().tasks.subtasks(parentId);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load the subtasks');
		} finally {
			loaded = true;
			requestAnimationFrame(() => (animate = true));
		}
	}
	$effect(() => {
		void parentId;
		loaded = false;
		animate = false;
		void load();
	});
	let dragging = $state(false);
	$effect(() =>
		realtime.subscribe((e) => {
			if (dragging) return;
			if (e.name === 'task.created' || e.name === 'task.updated' || e.name === 'task.moved') {
				const t = e.data as Task;
				const i = kids.findIndex((k) => k.id === t.id);
				if (t.parent_id === parentId && !t.archived_at) {
					if (i >= 0) kids[i] = t;
					else kids = [...kids, t];
					kids = [...kids].sort((a, b) => a.position - b.position);
				} else if (i >= 0) kids = kids.filter((k) => k.id !== t.id);
			} else if (e.name === 'task.deleted') {
				const id = (e.data as { id: UUID }).id;
				kids = kids.filter((k) => k.id !== id);
			} else if (e.name === 'job.updated') {
				const job = e.data as Job;
				const i = kids.findIndex((k) => k.id === job.task_id);
				if (i >= 0) kids[i] = { ...kids[i], latest_job: job };
			}
		})
	);

	const columnOf = (t: Task) => columns.find((c) => c.id === t.column_id);
	const finished = (t: Task) => FINISHED.has(columnOf(t)?.role ?? '');
	const failedRun = (t: Task) => t.latest_job?.status === 'failed' || t.latest_job?.status === 'lost';
	let done = $derived(kids.filter(finished).length);
	let failed = $derived(kids.filter((k) => !finished(k) && failedRun(k)).length);
	let runnable = $derived(kids.filter((k) => k.kind === 'command' && k.command.trim() && !finished(k)));
	let queued = $derived(task.subtasks?.queued ?? 0);

	// ---- adding ----
	let newTitle = $state('');
	let adding = $state(false);
	async function quickAdd() {
		const title = newTitle.trim();
		if (!title) return;
		adding = true;
		try {
			const t = await api().projects.createTask(task.project_id, { title, parent_id: task.id });
			if (!kids.some((k) => k.id === t.id)) kids = [...kids, t];
			newTitle = '';
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not add the subtask');
		} finally {
			adding = false;
		}
	}
	let pickOpen = $state(false);
	let manyOpen = $state(false);

	// ---- ordering (drag a row, or the row menu) ----
	async function saveOrder(next: Task[]) {
		const before = kids;
		kids = next;
		try {
			kids = await api().tasks.orderSubtasks(task.id, next.map((k) => k.id));
		} catch (err) {
			kids = before;
			snackbar.error(err instanceof ApiError ? err.message : 'Could not reorder them');
		}
	}
	function shift(t: Task, by: number) {
		const i = kids.findIndex((k) => k.id === t.id);
		const j = i + by;
		if (i < 0 || j < 0 || j >= kids.length) return;
		const next = [...kids];
		[next[i], next[j]] = [next[j], next[i]];
		void saveOrder(next);
	}
	function consider(e: CustomEvent<DndEvent<Task>>) {
		dragging = true;
		kids = e.detail.items;
	}
	function finalize(e: CustomEvent<DndEvent<Task>>) {
		dragging = false;
		void saveOrder(e.detail.items.filter((k) => !(k as unknown as Record<string, unknown>)[SHADOW_ITEM_MARKER_PROPERTY_NAME]));
	}

	// ---- ticking a manual subtask done (or back to To Do) ----
	const columnWith = (role: string) => columns.find((c) => c.role === role);
	async function tick(t: Task, on: boolean) {
		const target = on ? (columnWith('done') ?? columnWith('review')) : (columnWith('todo') ?? columnWith('backlog'));
		if (!target) return;
		const i = kids.findIndex((k) => k.id === t.id);
		const before = kids[i];
		if (i >= 0) kids[i] = { ...t, column_id: target.id };
		try {
			const moved = await api().tasks.move(t.id, target.id, 0);
			const j = kids.findIndex((k) => k.id === t.id);
			if (j >= 0) kids[j] = moved;
		} catch (err) {
			const j = kids.findIndex((k) => k.id === t.id);
			if (j >= 0 && before) kids[j] = before;
			snackbar.error(err instanceof ApiError ? err.message : 'Could not move it');
		}
	}

	// ---- taking out, running, stopping ----
	async function takeOut(t: Task) {
		try {
			await api().tasks.release(t.id);
			kids = kids.filter((k) => k.id !== t.id);
			snackbar.show(`“${t.title}” is its own card on the board again`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not take it out');
		}
	}
	let running = $state(false);
	async function runAll(atOnce: number | null) {
		running = true;
		try {
			const r = await api().tasks.runSubtasks(task.id, false, atOnce);
			const parts = [r.started.length ? `Started ${r.started.length}` : 'Nothing to start'];
			if (r.queued) parts.push(`${r.queued} wait their turn`);
			if (r.skipped.length) parts.push(`left out ${r.skipped.length}: ${r.skipped.map((s) => `“${s.title}” (${s.reason})`).join(', ')}`);
			snackbar.show(parts.join('; '));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not run them');
		} finally {
			running = false;
		}
	}
	async function stopQueue() {
		try {
			const r = await api().tasks.stopSubtasks(task.id);
			snackbar.show(`Stopped: ${r.not_started} won't start (what's running carries on)`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not stop it');
		}
	}

	// ---- saving as a playbook ----
	let saving = $state(false);
	async function saveAsPlaybook() {
		saving = true;
		try {
			const draft = await api().tasks.subtasksPlaybook(task.id);
			const check = await api().playbooks.validate(draft.yaml);
			if (!check.spec) throw new Error(check.errors[0] ?? 'The draft does not parse');
			sessionStorage.setItem('castor.playbook-draft', JSON.stringify(check.spec));
			if (draft.left_out.length) snackbar.show(`Left out (a step can't be a manual card or a hand-typed command): ${draft.left_out.join(', ')}`);
			void goto('/playbooks/edit/new?draft=1');
		} catch (err) {
			snackbar.error(err instanceof ApiError || err instanceof Error ? err.message : 'Could not make the playbook');
		} finally {
			saving = false;
		}
	}

	// ---- the graph, when subtasks wait on each other ----
	let ids = $derived(new Set(kids.map((k) => k.id)));
	let linked = $derived(kids.some((k) => (k.waits_on ?? []).some((w) => ids.has(w))));
	let view = $state<'list' | 'graph'>('list');
	const STATUS = (k: Task): PlaybookStepStatus =>
		finished(k) ? 'succeeded' : failedRun(k) ? 'failed' : k.latest_job?.status === 'approval' ? 'attention' : k.latest_job && ['queued', 'dispatched', 'running', 'waiting'].includes(k.latest_job.status) ? 'running' : 'pending';
	let graph = $derived.by(() => {
		const spec = {
			id: `subtasks-${task.id}`, name: task.title, schema_version: 1, description: '', tags: [], inputs: [],
			steps: kids.map((k) => ({ id: k.id, name: k.title, kind: 'command', command_ref: k.command_ref ?? '', args: {}, depends_on: (k.waits_on ?? []).filter((w) => ids.has(w)) }))
		} as unknown as PlaybookSpec;
		const run = {
			status: kids.some((k) => STATUS(k) === 'running') ? 'running' : 'succeeded',
			step_status: Object.fromEntries(kids.map((k) => [k.id, STATUS(k)])),
			step_results: {},
			tasks: kids.map((k) => ({ ...k, playbook_step_id: k.id, playbook_item: null }))
		} as unknown as PlaybookRun;
		return { spec, run };
	});
</script>

<div class="flex flex-col gap-4">
	{#if kids.length}
		<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
			<span class="body-medium text-on-surface">{done} of {kids.length} done</span>
			{#if failed}<span class="inline-flex items-center gap-1 body-small text-error"><Icon name="error" size={16} />{failed} failed</span>{/if}
			{#if queued}
				<span class="inline-flex items-center gap-1 body-small text-on-surface-variant"><Icon name="schedule" size={16} />{queued} waiting their turn{task.subtasks?.at_once ? ` (${task.subtasks.at_once === 1 ? 'one after another' : `${task.subtasks.at_once} at a time`})` : ''}</span>
				{#if editable}<Button variant="text" icon="stop_circle" onclick={stopQueue}>Stop starting more</Button>{/if}
			{/if}
			<span class="flex-1"></span>
			{#if linked}
				<div class="flex rounded-full border border-outline-variant" role="group" aria-label="Show subtasks as">
					<button type="button" class="state-layer rounded-l-full px-3 py-1 label-medium {view === 'list' ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface-variant'}" aria-pressed={view === 'list'} onclick={() => (view = 'list')}>List</button>
					<button type="button" class="state-layer rounded-r-full px-3 py-1 label-medium {view === 'graph' ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface-variant'}" aria-pressed={view === 'graph'} onclick={() => (view = 'graph')}>Graph</button>
				</div>
			{/if}
			{#if editable && runnable.length}
				<div class="flex">
					<Button variant="tonal" icon="play_arrow" loading={running} class="rounded-r-none" onclick={() => runAll(null)}>Run {runnable.length === 1 ? 'the command' : `all ${runnable.length}`}</Button>
					{#if runnable.length > 1}
						<Menu>
							{#snippet anchor()}<button type="button" class="state-layer flex h-10 items-center rounded-r-full border-l border-on-secondary-container/20 bg-secondary-container pr-3 pl-2 text-on-secondary-container" aria-label="Run a few at a time" title="Run a few at a time"><Icon name="arrow_drop_down" size={20} /></button>{/snippet}
							<MenuItem icon="looks_one" onclick={() => runAll(1)}>One after another</MenuItem>
							{#each [2, 3, 5, 10].filter((n) => n < runnable.length) as n (n)}
								<MenuItem icon="view_week" onclick={() => runAll(n)}>{n} at a time</MenuItem>
							{/each}
							<MenuItem icon="play_arrow" onclick={() => runAll(null)}>All at once</MenuItem>
						</Menu>
					{/if}
				</div>
			{/if}
		</div>
		<span class="flex h-1.5 overflow-hidden rounded-full bg-surface-container-highest">
			<span class="block h-full bg-primary transition-all" style:width="{(100 * done) / kids.length}%"></span>
			{#if failed}<span class="block h-full bg-error transition-all" style:width="{(100 * failed) / kids.length}%"></span>{/if}
		</span>
	{/if}

	{#if loaded}
		{#if view === 'graph' && linked}
			<div class="overflow-hidden rounded-md border border-outline-variant/60">
				<PlaybookVisualizer spec={graph.spec} run={graph.run} onStepSelect={(step) => onopen(step.id)} />
			</div>
		{:else if kids.length}
			<ul
				class="flex flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container"
				use:dndzone={{ items: kids, type: `subtasks-${task.id}`, flipDurationMs: 150, dragDisabled: !editable, dropTargetStyle: {} }}
				onconsider={consider}
				onfinalize={finalize}
				aria-label="Subtasks{editable ? ', drag to reorder' : ''}"
			>
				{#each kids as kid, i (kid.id)}
					{@const col = columnOf(kid)}
					{@const due = kid.due_at ? dueInfo(kid.due_at, finished(kid)) : null}
					{@const faces = people.assigneesOf(kid)}
					<li class="flex items-center pr-1 {(kid as unknown as Record<string, unknown>)[SHADOW_ITEM_MARKER_PROPERTY_NAME] ? 'opacity-40' : ''}" in:enter={{ enabled: animate && !dragging }} out:leave={{ enabled: animate && !dragging }}>
						{#if editable}<span class="flex shrink-0 cursor-grab items-center pl-1.5 text-on-surface-variant/70" aria-hidden="true"><Icon name="drag_indicator" size={18} /></span>{/if}
						{#if kid.kind === 'manual' && editable}
							<input type="checkbox" class="ml-2 size-4 shrink-0 accent-[var(--md-sys-color-primary)]" checked={finished(kid)} aria-label="“{maskSpoilers(kid.title)}” is done" onchange={(e) => tick(kid, (e.currentTarget as HTMLInputElement).checked)} />
						{/if}
						<button type="button" class="state-layer flex min-w-0 flex-1 items-center gap-3 px-3 py-2 text-left" onclick={() => onopen(kid.id)}>
							<span title={col?.name}><ColumnGlyph role={col?.role} size={18} /></span>
							<Icon name={kindIcon(kid)} size={16} class="shrink-0 text-on-surface-variant" />
							<span class="min-w-0 flex-1 truncate body-medium {finished(kid) ? 'text-on-surface-variant line-through decoration-on-surface-variant/50' : 'text-on-surface'}"><Spoilers text={kid.title} /></span>
							{#if due}<span class="hidden shrink-0 items-center gap-1 rounded-sm px-1.5 py-0.5 label-small sm:inline-flex {DUE_TONE[due.tone]}" title={due.title}><Icon name={due.icon} size={14} />{due.label}</span>{/if}
							{#if kid.latest_job}<span class="shrink-0"><StatusChip job={kid.latest_job} compact /></span>{/if}
							{#if faces.length}
								<span class="hidden shrink-0 -space-x-1.5 sm:flex" title="Assigned to {faces.map((u) => u.display_name).join(', ')}">
									{#each faces.slice(0, 2) as u (u.id)}<AssigneeFace who={u} size={20} />{/each}
								</span>
							{/if}
							<span class="hidden shrink-0 body-small text-on-surface-variant md:inline">{col?.name}</span>
							<Icon name="chevron_right" size={18} class="shrink-0 text-on-surface-variant" />
						</button>
						{#if editable}
							<Menu>
								{#snippet anchor()}<IconButton icon="more_vert" label="Subtask menu" />{/snippet}
								<MenuItem icon="arrow_upward" disabled={i === 0} onclick={() => shift(kid, -1)}>Move up</MenuItem>
								<MenuItem icon="arrow_downward" disabled={i === kids.length - 1} onclick={() => shift(kid, 1)}>Move down</MenuItem>
								<MenuItem icon="move_up" onclick={() => takeOut(kid)}>Make it its own card</MenuItem>
							</Menu>
						{/if}
					</li>
				{/each}
			</ul>
		{:else}
			<div class="flex flex-col items-center gap-2 rounded-md bg-surface-container px-4 py-8 text-center">
				<Icon name="checklist" size={36} class="text-on-surface-variant" />
				<p class="max-w-prose body-medium text-on-surface-variant">
					No subtasks yet. Subtasks are cards inside this one: they stay off the board, and this card shows how far along they are{editable ? ', moving to In Progress when one starts and to Review when they are all finished' : ''}.
				</p>
			</div>
		{/if}

		{#if editable}
			<div class="flex flex-wrap items-center gap-2">
				<form class="flex min-w-0 flex-1 basis-64 items-center gap-2 rounded-full bg-surface-container-high pr-1 pl-4" onsubmit={(e) => (e.preventDefault(), quickAdd())}>
					<Icon name="add" size={20} class="text-on-surface-variant" />
					<input class="h-10 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Add a subtask" bind:value={newTitle} disabled={adding} aria-label="New subtask title" />
					{#if newTitle.trim()}<Button variant="text" type="submit" loading={adding}>Add</Button>{/if}
				</form>
				<Menu>
					{#snippet anchor()}<Button variant="text" icon="playlist_add" trailingIcon="arrow_drop_down">Add more</Button>{/snippet}
					<MenuItem icon="playlist_add" onclick={() => (pickOpen = true)}>Cards from the board…</MenuItem>
					<MenuItem icon="dynamic_feed" onclick={() => (manyOpen = true)}>One for each target…</MenuItem>
				</Menu>
				{#if kids.some((k) => k.command_ref)}
					<Button variant="text" icon="account_tree" loading={saving} onclick={saveAsPlaybook}>Save as a playbook</Button>
				{/if}
			</div>
		{/if}
	{/if}
</div>

{#if pickOpen}
	<GroupCardsDialog bind:open={pickOpen} projectId={task.project_id} {columns} parent={task} ondone={() => void load()} />
{/if}
{#if manyOpen}
	<ManySubtasksDialog bind:open={manyOpen} {task} onadded={() => void load()} />
{/if}
