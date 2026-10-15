<script lang="ts">
	import { dndzone, type DndEvent, SHADOW_ITEM_MARKER_PROPERTY_NAME } from 'svelte-dnd-action';
	import { flip } from 'svelte/animate';
	import type { Column, Task, UUID } from '$lib/api';
	import { IconButton } from '$lib/m3';
	import TaskCard from './TaskCard.svelte';
	import ColumnGlyph from './ColumnGlyph.svelte';

	interface Props {
		column: Column;
		tasks: Task[];
		onopen: (task: Task) => void;
		onadd: (column: Column) => void;
		onmove: (taskId: UUID, columnId: UUID, position: number) => void;
	}
	let { column, tasks, onopen, onadd, onmove }: Props = $props();

	// Local copy so svelte-dnd-action can insert its placeholder while dragging.
	let items = $state<Task[]>([]);
	let dragging = $state(false);
	$effect(() => {
		if (!dragging) items = tasks;
	});

	function consider(e: CustomEvent<DndEvent<Task>>) {
		dragging = true;
		items = e.detail.items;
	}

	function finalize(e: CustomEvent<DndEvent<Task>>) {
		items = e.detail.items;
		dragging = false;
		const { id, trigger } = e.detail.info;
		if (trigger !== 'droppedIntoZone' && trigger !== 'droppedIntoAnother') return;
		if (trigger === 'droppedIntoAnother') return; // the receiving column reports the move
		const index = items.findIndex((t) => t.id === id);
		if (index < 0) return;
		const prev = items[index - 1];
		const next = items[index + 1];
		let position: number;
		if (prev && next) position = (prev.position + next.position) / 2;
		else if (prev) position = prev.position + 10;
		else if (next) position = next.position - 10;
		else position = 10;
		onmove(id as UUID, column.id, position);
	}

	let overLimit = $derived(column.wip_limit !== null && tasks.length > column.wip_limit);
</script>

<section class="flex h-full w-80 shrink-0 flex-col rounded-lg bg-surface-container-low">
	<header class="flex items-center gap-2 px-3 pt-3 pb-2">
		<ColumnGlyph role={column.role} size={18} />
		<h2 class="flex-1 truncate title-medium text-on-surface">{column.name}</h2>
		<span class="rounded-full px-2 py-0.5 label-small {overLimit ? 'bg-error-container text-on-error-container' : 'bg-surface-container-highest text-on-surface-variant'}">
			{tasks.length}{column.wip_limit !== null ? `/${column.wip_limit}` : ''}
		</span>
		<IconButton icon="add" label="Add card to {column.name}" size="sm" onclick={() => onadd(column)} />
	</header>
	{#if column.role === 'active'}
		<p class="px-4 pb-2 label-small text-on-surface-variant">Command cards dropped here run on an agent</p>
	{/if}
	<div
		class="flex min-h-24 flex-1 flex-col gap-2 overflow-y-auto px-2 pb-2"
		use:dndzone={{ items, flipDurationMs: 150, type: 'task', dropTargetStyle: {}, dropTargetClasses: ['outline-2', 'outline-dashed', 'outline-primary/40', 'rounded-md'] }}
		onconsider={consider}
		onfinalize={finalize}
	>
		{#each items as task (task.id)}
			<div animate:flip={{ duration: 150 }} class={(task as unknown as Record<string, unknown>)[SHADOW_ITEM_MARKER_PROPERTY_NAME] ? 'opacity-40' : ''}>
				<TaskCard {task} {onopen} />
			</div>
		{/each}
	</div>
</section>
