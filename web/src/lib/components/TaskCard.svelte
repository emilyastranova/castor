<script lang="ts">
	import type { Task } from '$lib/api';
	import { Avatar, Icon } from '$lib/m3';
	import { people } from '$lib/people.svelte';
	import StatusChip from './StatusChip.svelte';
	import PriorityGlyph, { priorityLabels } from './PriorityGlyph.svelte';

	interface Props {
		task: Task;
		onopen: (task: Task) => void;
	}
	let { task, onopen }: Props = $props();
	let assignees = $derived(task.assignees.map((id) => people.user(task.project_id, id)).filter((u) => !!u));
	let running = $derived(task.latest_job?.status === 'running' || task.latest_job?.status === 'dispatched');
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
			name={task.kind === 'command' ? 'terminal' : 'person'}
			size={18}
			class="mt-0.5 {task.kind === 'command' ? 'text-primary' : 'text-on-surface-variant'}"
		/>
		<p class="min-w-0 flex-1 body-medium text-on-surface">{task.title}</p>
		{#if task.priority > 0}
			<span class="mt-0.5" title="{priorityLabels[task.priority] ?? ''} priority"><PriorityGlyph level={task.priority} /></span>
		{/if}
	</div>
	{#if task.kind === 'command' && task.command}
		<code class="truncate rounded-xs bg-surface-container px-2 py-1 font-mono text-[12px] text-on-surface-variant">{task.command}</code>
	{/if}
	{#if task.tags.length || task.latest_job || task.comment_count || task.attachment_count || assignees.length}
		<div class="flex flex-wrap items-center gap-1.5">
			<StatusChip job={task.latest_job} />
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
