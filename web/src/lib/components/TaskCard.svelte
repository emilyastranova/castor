<script lang="ts">
	import type { Task } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { people } from '$lib/people.svelte';
	import AssigneeFace from './AssigneeFace.svelte';
	import StatusChip from './StatusChip.svelte';
	import PriorityGlyph, { priorityLabels } from './PriorityGlyph.svelte';
	import { isRunning, kindIcon, runState as runStateOf, subtaskState } from '$lib/taskStatus';
	import { DUE_TONE, dueInfo } from '$lib/due';
	import { dndzone, type DndEvent } from 'svelte-dnd-action';
	import { dropInto } from '$lib/dropInto.svelte';

	interface Props {
		task: Task;
		onopen: (task: Task) => void;
		/** In a Done or Reported column: its due date no longer needs attention. */
		finished?: boolean;
		/** An activity card on the board: a card dropped on it becomes its subtask. */
		ondropinto?: (taskId: Task['id'], into: Task) => void;
	}
	let { task, onopen, finished = false, ondropinto }: Props = $props();
	// The drop strip's own zone (svelte-dnd-action puts the dragged card's placeholder in it).
	let dropped = $state<Task[]>([]);
	let offering = $derived(!!ondropinto && dropInto.accepts(task));
	function droppedIn(e: CustomEvent<DndEvent<Task>>) {
		const id = e.detail.info.id;
		dropped = [];
		dropInto.dragging = null;
		if (e.detail.info.trigger === 'droppedIntoZone') ondropinto?.(id as Task['id'], task);
	}
	let due = $derived(task.due_at ? dueInfo(task.due_at, finished) : null);
	let blockers = $derived(finished ? [] : (task.blocked_by ?? []));
	let assignees = $derived(people.assigneesOf(task));
	let running = $derived(isRunning(task));
	/** A playbook run's card: its state in a word, and how far along it is. */
	let runState = $derived(runStateOf(task));
	/** An activity card: how far along its subtasks are. */
	let subs = $derived(subtaskState(task));
</script>

<div
	class="group state-layer relative flex cursor-pointer flex-col gap-2 rounded-md bg-surface-container-lowest p-3 shadow-e1 transition-shadow hover:shadow-e2 {running ? 'ring-1 ring-tertiary' : ''}"
	role="button"
	tabindex="0"
	onclick={() => onopen(task)}
	onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onopen(task))}
>
	<div class="flex items-start gap-2">
		<Icon
			name={kindIcon(task)}
			size={18}
			class="mt-0.5 {task.run || task.kind === 'command' ? 'text-primary' : 'text-on-surface-variant'}"
		/>
		<p class="min-w-0 flex-1 body-medium text-on-surface">{task.title}</p>
		{#if task.priority > 0}
			<span class="mt-0.5" title="{priorityLabels[task.priority] ?? ''} priority"><PriorityGlyph level={task.priority} /></span>
		{/if}
	</div>
	{#if task.run && runState}
		<div class="flex flex-col gap-1.5">
			<span class="h-1.5 overflow-hidden rounded-full bg-surface-container-highest" title="{task.run.done} of {task.run.total} steps done">
				<span class="block h-full rounded-full transition-all {task.run.failed ? 'bg-error' : 'bg-primary'}" style:width="{task.run.total ? (100 * task.run.done) / task.run.total : 0}%"></span>
			</span>
			<span class="flex items-center gap-1.5">
				<span class="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 label-small {runState.tone}">
					<Icon name={runState.icon} size={14} class={task.run.status === 'running' && !task.run.attention ? 'motion-safe:animate-spin' : ''} />{runState.label}
				</span>
				<span class="label-small text-on-surface-variant">Playbook</span>
			</span>
		</div>
	{/if}
	{#if task.subtasks && subs}
		<div class="flex flex-col gap-1.5">
			<span class="flex h-1.5 overflow-hidden rounded-full bg-surface-container-highest" title="{task.subtasks.done} of {task.subtasks.total} subtasks done">
				<span class="block h-full bg-primary transition-all" style:width="{(100 * task.subtasks.done) / task.subtasks.total}%"></span>
				{#if task.subtasks.failed}<span class="block h-full bg-error transition-all" style:width="{(100 * task.subtasks.failed) / task.subtasks.total}%"></span>{/if}
			</span>
			<span class="flex items-center gap-1.5">
				<span class="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 label-small {subs.tone}">
					<Icon name={subs.icon} size={14} class={subs.icon === 'progress_activity' ? 'motion-safe:animate-spin' : ''} />{subs.label}
				</span>
				<span class="label-small text-on-surface-variant">Subtasks</span>
			</span>
		</div>
	{/if}
	{#if offering || dropped.length}
		<div
			class="flex min-h-10 items-center justify-center rounded-sm border border-dashed border-primary/60 bg-primary-container/40 px-2 label-medium text-on-primary-container"
			use:dndzone={{ items: dropped, type: 'task', flipDurationMs: 0, dropTargetStyle: {}, dropTargetClasses: ['bg-primary-container'], morphDisabled: true }}
			onconsider={(e) => (dropped = e.detail.items)}
			onfinalize={droppedIn}
			role="region"
			aria-label="Drop here to make it a subtask of {task.title}"
		>
			{#if !dropped.length}<span class="pointer-events-none flex items-center gap-1"><Icon name="subdirectory_arrow_right" size={16} />Drop here to make it a subtask</span>{/if}
			{#each dropped as d (d.id)}<span class="truncate">Make “{d.title}” a subtask</span>{/each}
		</div>
	{/if}
	{#if task.kind === 'command' && task.command}
		<code class="truncate rounded-xs bg-surface-container px-2 py-1 font-mono text-[12px] text-on-surface-variant">{task.command}</code>
	{/if}
	{#if task.tags.length || task.latest_job || task.comment_count || task.attachment_count || assignees.length || due || blockers.length}
		<div class="flex flex-wrap items-center gap-1.5">
			<StatusChip job={task.latest_job} batch={task.batch} />
			{#if blockers.length}
				<span class="inline-flex items-center gap-1 rounded-sm bg-tertiary-container px-1.5 py-0.5 label-small text-on-tertiary-container" title="Waits on {blockers.map((b) => `“${b.title}”`).join(', ')}">
					<Icon name="lock" size={14} />Blocked{blockers.length > 1 ? ` · ${blockers.length}` : ''}
				</span>
			{/if}
			{#if due}
				<span class="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 label-small {DUE_TONE[due.tone]}" title={due.title}><Icon name={due.icon} size={14} />{due.label}</span>
			{/if}
			{#if task.latest_job?.waiting_input && running}<span class="flex items-center gap-1 rounded-full bg-tertiary-container px-1.5 py-0.5 label-small text-on-tertiary-container" title="Waiting for input: {task.latest_job.waiting_input}"><Icon name="keyboard" size={14} />input</span>{/if}
			{#each task.tags.slice(0, 3) as tag (tag)}
				<span class="rounded-xs border border-outline-variant px-1.5 py-0.5 label-small text-on-surface-variant">{tag}</span>
			{/each}
			<span class="ml-auto flex items-center gap-2 label-small text-on-surface-variant">
				{#if task.image}<span title={task.image}><Icon name="deployed_code" size={14} /></span>{/if}
				{#if task.comment_count}<span class="flex items-center gap-0.5"><Icon name="chat_bubble" size={14} />{task.comment_count}</span>{/if}
				{#if task.attachment_count}<span class="flex items-center gap-0.5"><Icon name="attach_file" size={14} />{task.attachment_count}</span>{/if}
				{#if assignees.length}
					<span class="flex -space-x-1.5" title="Assigned to {assignees.map((u) => u.display_name).join(', ')}">
						{#each assignees.slice(0, 3) as u (u.id)}<span class="rounded-full ring-2 ring-surface-container-lowest"><AssigneeFace who={u} size={20} /></span>{/each}
						{#if assignees.length > 3}<span class="z-10 flex size-5 items-center justify-center rounded-full bg-surface-container-highest text-[10px] ring-2 ring-surface-container-lowest">+{assignees.length - 3}</span>{/if}
					</span>
				{/if}
			</span>
		</div>
	{/if}
</div>
