<script lang="ts">
	import type { Task } from '$lib/api';
	import { Avatar, Icon } from '$lib/m3';
	import { people } from '$lib/people.svelte';
	import StatusChip from './StatusChip.svelte';
	import PriorityGlyph, { priorityLabels } from './PriorityGlyph.svelte';
	import { isRunning, kindIcon, runState as runStateOf } from '$lib/taskStatus';

	interface Props {
		task: Task;
		onopen: (task: Task) => void;
	}
	let { task, onopen }: Props = $props();
	let assignees = $derived(task.assignees.map((id) => people.user(task.project_id, id)).filter((u) => !!u));
	let running = $derived(isRunning(task));
	/** A playbook run's card: its state in a word, and how far along it is. */
	let runState = $derived(runStateOf(task));
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
	{#if task.kind === 'command' && task.command}
		<code class="truncate rounded-xs bg-surface-container px-2 py-1 font-mono text-[12px] text-on-surface-variant">{task.command}</code>
	{/if}
	{#if task.tags.length || task.latest_job || task.comment_count || task.attachment_count || assignees.length}
		<div class="flex flex-wrap items-center gap-1.5">
			<StatusChip job={task.latest_job} />
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
						{#each assignees.slice(0, 3) as u (u.id)}<span class="rounded-full ring-2 ring-surface-container-lowest"><Avatar name={u.display_name} src={u.avatar_url} size={20} /></span>{/each}
						{#if assignees.length > 3}<span class="z-10 flex size-5 items-center justify-center rounded-full bg-surface-container-highest text-[10px] ring-2 ring-surface-container-lowest">+{assignees.length - 3}</span>{/if}
					</span>
				{/if}
			</span>
		</div>
	{/if}
</div>
