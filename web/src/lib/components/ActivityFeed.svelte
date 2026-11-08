<script lang="ts">
	import PersonName from '$lib/components/PersonName.svelte';
	import { enter, leave } from '$lib/motion';
	import type { Activity, UUID } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { relativeTime, absoluteTime } from '$lib/format';

	interface Props {
		items: Activity[];
		onopentask?: (taskId: UUID) => void;
		showProject?: boolean;
		class?: string;
	}
	let { items, onopentask, class: klass = '' }: Props = $props();

	function iconFor(type: string): { name: string; cls: string } {
		if (type.startsWith('job.succeeded')) return { name: 'check_circle', cls: 'text-primary' };
		if (type.startsWith('job.failed') || type.startsWith('job.lost')) return { name: 'error', cls: 'text-error' };
		if (type.startsWith('job.cancelled')) return { name: 'cancel', cls: 'text-on-surface-variant' };
		if (type.startsWith('job.')) return { name: 'bolt', cls: 'text-tertiary' };
		if (type.startsWith('agent.connected')) return { name: 'dns', cls: 'text-primary' };
		if (type.startsWith('agent.')) return { name: 'dns', cls: 'text-on-surface-variant' };
		if (type.startsWith('comment.')) return { name: 'chat_bubble', cls: 'text-secondary' };
		if (type.startsWith('attachment.')) return { name: 'attach_file', cls: 'text-secondary' };
		if (type.startsWith('task.moved')) return { name: 'swap_horiz', cls: 'text-on-surface-variant' };
		if (type.startsWith('task.deleted')) return { name: 'delete', cls: 'text-on-surface-variant' };
		if (type.startsWith('task.')) return { name: 'add_circle', cls: 'text-on-surface-variant' };
		if (type.startsWith('project.')) return { name: 'view_kanban', cls: 'text-primary' };
		return { name: 'info', cls: 'text-on-surface-variant' };
	}
</script>

<ol class="flex flex-col overflow-y-auto {klass}">
	{#if items.length === 0}
		<li class="px-4 py-6 text-center body-medium text-on-surface-variant">Nothing has happened yet.</li>
	{/if}
	{#each items as a (a.id)}
		{@const ic = iconFor(a.type)}
		<li in:enter out:leave>
			<button
				type="button"
				class="state-layer flex w-full items-start gap-3 px-4 py-2.5 text-left disabled:cursor-default"
				disabled={!a.task_id || !onopentask}
				onclick={() => a.task_id && onopentask?.(a.task_id)}
			>
				<Icon name={ic.name} size={20} class="mt-0.5 {ic.cls}" filled />
				<span class="min-w-0 flex-1">
					<span class="block body-medium text-on-surface">{a.message}</span>
					<span class="block body-small text-on-surface-variant" title={absoluteTime(a.created_at)}>
						{a.actor_type === 'agent' ? 'agent ' : ''}<PersonName name={a.actor_name} />{a.payload?.via === 'mcp' ? ' via MCP' : ''} · {relativeTime(a.created_at)}
					</span>
				</span>
			</button>
		</li>
	{/each}
</ol>
