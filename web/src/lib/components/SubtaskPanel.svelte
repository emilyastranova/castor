<script lang="ts">
	import type { Column, Job, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
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
	import StatusChip from './StatusChip.svelte';

	/** An activity card's subtasks (in its Subtasks tab): where each one is, adding new or existing
	 *  cards, ordering, taking one out, and running the command ones. Opening a subtask shows its
	 *  own card on top (onopen), like a playbook run's steps. */
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
	$effect(() =>
		realtime.subscribe((e) => {
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
	let done = $derived(kids.filter(finished).length);
	let failed = $derived(kids.filter((k) => !finished(k) && (k.latest_job?.status === 'failed' || k.latest_job?.status === 'lost')).length);
	let runnable = $derived(kids.filter((k) => k.kind === 'command' && k.command.trim() && !finished(k)));

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

	// ---- ordering, taking out, running ----
	async function shift(t: Task, by: number) {
		const ids = kids.map((k) => k.id);
		const i = ids.indexOf(t.id);
		const j = i + by;
		if (i < 0 || j < 0 || j >= ids.length) return;
		[ids[i], ids[j]] = [ids[j], ids[i]];
		const before = kids;
		kids = ids.map((id) => before.find((k) => k.id === id)!);
		try {
			kids = await api().tasks.orderSubtasks(task.id, ids);
		} catch (err) {
			kids = before;
			snackbar.error(err instanceof ApiError ? err.message : 'Could not reorder them');
		}
	}
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
	async function runAll() {
		running = true;
		try {
			const r = await api().tasks.runSubtasks(task.id);
			const parts = [r.started.length ? `Started ${r.started.length}` : 'Nothing to start'];
			if (r.skipped.length) parts.push(`left out ${r.skipped.length}: ${r.skipped.map((s) => `“${s.title}” (${s.reason})`).join(', ')}`);
			snackbar.show(parts.join('; '));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not run them');
		} finally {
			running = false;
		}
	}
</script>

<div class="flex flex-col gap-4">
	{#if kids.length}
		<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
			<span class="body-medium text-on-surface">{done} of {kids.length} done</span>
			{#if failed}<span class="inline-flex items-center gap-1 body-small text-error"><Icon name="error" size={16} />{failed} failed</span>{/if}
			<span class="flex-1"></span>
			{#if editable && runnable.length}
				<Button variant="tonal" icon="play_arrow" loading={running} onclick={runAll}>Run {runnable.length === 1 ? 'the command' : `all ${runnable.length} commands`}</Button>
			{/if}
		</div>
		<span class="flex h-1.5 overflow-hidden rounded-full bg-surface-container-highest">
			<span class="block h-full bg-primary transition-all" style:width="{(100 * done) / kids.length}%"></span>
			{#if failed}<span class="block h-full bg-error transition-all" style:width="{(100 * failed) / kids.length}%"></span>{/if}
		</span>
	{/if}

	{#if loaded}
		<div>
			{#if kids.length}
				<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container">
					{#each kids as kid, i (kid.id)}
						{@const col = columnOf(kid)}
						{@const due = kid.due_at ? dueInfo(kid.due_at, finished(kid)) : null}
						{@const faces = people.assigneesOf(kid)}
						<li class="flex items-center pr-1" in:enter={{ enabled: animate }} out:leave={{ enabled: animate }}>
							<button type="button" class="state-layer flex min-w-0 flex-1 items-center gap-3 px-3 py-2 text-left" onclick={() => onopen(kid.id)}>
								<span title={col?.name}><ColumnGlyph role={col?.role} size={18} /></span>
								<Icon name={kindIcon(kid)} size={16} class="shrink-0 text-on-surface-variant" />
								<span class="min-w-0 flex-1 truncate body-medium {finished(kid) ? 'text-on-surface-variant line-through decoration-on-surface-variant/50' : 'text-on-surface'}">{kid.title}</span>
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
		</div>

		{#if editable}
			<div class="flex flex-wrap items-center gap-2">
				<form class="flex min-w-0 flex-1 items-center gap-2 rounded-full bg-surface-container-high pr-1 pl-4" onsubmit={(e) => (e.preventDefault(), quickAdd())}>
					<Icon name="add" size={20} class="text-on-surface-variant" />
					<input class="h-10 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Add a subtask" bind:value={newTitle} disabled={adding} aria-label="New subtask title" />
					{#if newTitle.trim()}<Button variant="text" type="submit" loading={adding}>Add</Button>{/if}
				</form>
				<Button variant="text" icon="playlist_add" onclick={() => (pickOpen = true)}>Add cards from the board</Button>
			</div>
		{/if}
	{/if}
</div>

{#if pickOpen}
	<GroupCardsDialog bind:open={pickOpen} projectId={task.project_id} {columns} parent={task} ondone={() => void load()} />
{/if}
