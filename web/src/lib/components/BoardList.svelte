<script lang="ts">
	import type { Column, Task, UUID } from '$lib/api';
	import { Icon, IconButton, Menu, MenuItem } from '$lib/m3';
	import AssigneeFace from './AssigneeFace.svelte';
	import { people } from '$lib/people.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { isRunning, kindIcon, runState } from '$lib/taskStatus';
	import StatusChip from './StatusChip.svelte';
	import PriorityGlyph, { priorityLabels } from './PriorityGlyph.svelte';
	import ColumnGlyph from './ColumnGlyph.svelte';

	/** The board as a list: every card as a row, grouped by column (in board order), with the
	 *  same filters as the board. Moving a card is a menu on its row. */
	interface Props {
		columns: Column[];
		/** A column's cards as the board shows them (filters applied). */
		tasksIn: (column: Column) => Task[];
		readonly?: boolean;
		onopen: (task: Task) => void;
		onadd: (column: Column) => void;
		/** Move a card to the end of another column. */
		onmoveto: (taskId: UUID, columnId: UUID) => void;
	}
	let { columns, tasksIn, readonly = false, onopen, onadd, onmoveto }: Props = $props();

	let folded = $state<Set<string>>(loadFolded());
	function loadFolded(): Set<string> {
		try {
			return new Set(JSON.parse(localStorage.getItem('castor.list.folded') ?? '[]'));
		} catch {
			return new Set();
		}
	}
	function toggle(id: string) {
		const next = new Set(folded);
		if (!next.delete(id)) next.add(id);
		folded = next;
		try {
			localStorage.setItem('castor.list.folded', JSON.stringify([...next]));
		} catch {
			/* only for this tab */
		}
	}

	// ---- sorting: within each column; a header cycles up, down, back to board order ----
	type SortKey = 'title' | 'status' | 'priority' | 'assignees' | 'updated';
	type Sort = { key: SortKey; dir: 1 | -1 } | null;
	/** The direction a first click sorts in: the most useful end first. */
	const FIRST: Record<SortKey, 1 | -1> = { title: 1, status: 1, priority: -1, assignees: 1, updated: -1 };
	let sort = $state<Sort>(loadSort());
	function loadSort(): Sort {
		try {
			const s = JSON.parse(localStorage.getItem('castor.list.sort') ?? 'null');
			return s && s.key in FIRST && (s.dir === 1 || s.dir === -1) ? s : null;
		} catch {
			return null;
		}
	}
	function sortBy(key: SortKey) {
		const first = FIRST[key];
		sort = sort?.key !== key ? { key, dir: first } : sort.dir === first ? { key, dir: (-first) as 1 | -1 } : null;
		try {
			localStorage.setItem('castor.list.sort', JSON.stringify(sort));
		} catch {
			/* only for this tab */
		}
	}
	/** What needs looking at first: waiting for you, running, queued, failed, then the rest. */
	function statusRank(t: Task): number {
		if (t.run) return t.run.attention ? 0 : t.run.status === 'running' ? 1 : t.run.status === 'failed' ? 3 : t.run.status === 'succeeded' ? 5 : 6;
		const s = t.latest_job?.status;
		if (!s) return t.kind === 'command' ? 7 : 8;
		if (t.latest_job?.waiting_input) return 0;
		return ({ running: 1, dispatched: 1, queued: 2, failed: 3, lost: 3, succeeded: 5, cancelled: 6 } as Record<string, number>)[s] ?? 6;
	}
	const firstAssignee = (t: Task) => assigneesOf(t)[0]?.display_name.toLowerCase() ?? '\uffff'; // nobody: last
	function compare(a: Task, b: Task, s: NonNullable<Sort>): number {
		const by = {
			title: () => a.title.localeCompare(b.title, undefined, { sensitivity: 'base', numeric: true }),
			status: () => statusRank(a) - statusRank(b),
			priority: () => a.priority - b.priority,
			assignees: () => firstAssignee(a).localeCompare(firstAssignee(b)),
			updated: () => a.updated_at.localeCompare(b.updated_at)
		}[s.key]();
		return by * s.dir || a.position - b.position; // ties keep board order
	}
	const sorted = (tasks: Task[]) => (sort ? [...tasks].sort((a, b) => compare(a, b, sort!)) : tasks);

	let groups = $derived(columns.map((c) => ({ column: c, tasks: sorted(tasksIn(c)) })));
	let total = $derived(groups.reduce((n, g) => n + g.tasks.length, 0));
	// The middle columns, by when they show. Every row has all of them (no colspan): a span
	// over hidden columns would still give them width in a fixed table.
	const MIDDLE = ['hidden sm:table-cell', 'hidden lg:table-cell', 'hidden md:table-cell', 'hidden xl:table-cell', 'hidden md:table-cell'];
	const ariaSort = (key: string) => (sort?.key === key ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined);
	const assigneesOf = (t: Task) => people.assigneesOf(t);
</script>

{#snippet header(key: 'title' | 'status' | 'priority' | 'assignees' | 'updated', label: string)}
	{@const on = sort?.key === key}
	<button
		type="button"
		class="state-layer inline-flex items-center gap-1 rounded-sm px-1 py-0.5 {on ? 'text-on-surface' : ''}"
		title={on ? (sort?.dir === FIRST[key] ? 'Click to reverse' : 'Click for board order') : `Sort by ${label.toLowerCase()}`}
		onclick={() => sortBy(key)}
	>
		{label}
		<Icon name={on && sort?.dir === -1 ? 'arrow_downward' : 'arrow_upward'} size={14} class={on ? 'text-primary' : 'opacity-0 group-hover/head:opacity-60'} />
	</button>
{/snippet}

{#snippet status(t: Task)}
	{@const run = runState(t)}
	{#if run}
		<span class="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 label-small {run.tone}">
			<Icon name={run.icon} size={14} class={t.run?.status === 'running' && !t.run.attention ? 'motion-safe:animate-spin' : ''} />{run.label}
		</span>
	{:else if t.latest_job}
		<StatusChip job={t.latest_job} batch={t.batch} />
	{:else if t.kind === 'command'}
		<span class="label-small text-on-surface-variant">Not run yet</span>
	{/if}
{/snippet}

<div class="flex min-h-0 flex-1 flex-col overflow-auto rounded-lg bg-surface-container-low">
	<!-- Fixed widths: the card column takes what's left and long commands truncate in it. -->
	<table class="w-full table-fixed border-separate border-spacing-0 text-left">
		<thead class="sticky top-0 z-10 bg-surface-container-low">
			<tr class="group/head label-medium text-on-surface-variant">
				<th class="py-2 pr-2 pl-2 font-medium" aria-sort={ariaSort('title')}>{@render header('title', 'Card')}</th>
				<th class="hidden w-44 px-1 py-2 font-medium sm:table-cell" aria-sort={ariaSort('status')}>{@render header('status', 'Status')}</th>
				<th class="hidden w-28 px-1 py-2 font-medium lg:table-cell" aria-sort={ariaSort('priority')}>{@render header('priority', 'Priority')}</th>
				<th class="hidden w-28 px-1 py-2 font-medium md:table-cell" aria-sort={ariaSort('assignees')}>{@render header('assignees', 'Assignees')}</th>
				<th class="hidden w-52 px-2 py-2 font-medium xl:table-cell">Tags</th>
				<th class="hidden w-28 px-1 py-2 font-medium md:table-cell" aria-sort={ariaSort('updated')}>{@render header('updated', 'Updated')}</th>
				<th class="w-12 py-2 pr-2"><span class="sr-only">Actions</span></th>
			</tr>
		</thead>
		{#each groups as g (g.column.id)}
			{@const open = !folded.has(g.column.id)}
			<tbody>
				<tr class="bg-surface-container">
					<th scope="rowgroup" class="border-t border-outline-variant/60 px-2 py-1 text-left font-normal">
						<div class="flex items-center gap-2">
							<button
								type="button"
								class="state-layer flex min-w-0 flex-1 items-center gap-2 rounded-sm px-2 py-1.5 text-left"
								aria-expanded={open}
								onclick={() => toggle(g.column.id)}
							>
								<Icon name="chevron_right" size={18} class="shrink-0 text-on-surface-variant transition-transform duration-150 {open ? 'rotate-90' : ''}" />
								<ColumnGlyph role={g.column.role} size={18} />
								<span class="truncate title-small text-on-surface">{g.column.name}</span>
								<span class="rounded-full bg-surface-container-highest px-2 label-small text-on-surface-variant tabular-nums">{g.tasks.length}</span>
								{#if g.column.role === 'active'}<span class="hidden truncate body-small text-on-surface-variant sm:inline">· command cards moved here run on an agent</span>{/if}
							</button>
						</div>
					</th>
					{#each MIDDLE as cls, i (i)}<td class="{cls} border-t border-outline-variant/60"></td>{/each}
					<td class="border-t border-outline-variant/60 py-1 pr-2 text-right">
						{#if !readonly}<IconButton icon="add" label="New card in {g.column.name}" size="sm" onclick={() => onadd(g.column)} />{/if}
					</td>
				</tr>
				{#if open}
					{#each g.tasks as t (t.id)}
						{@const who = assigneesOf(t)}
						<tr
							class="group cursor-pointer align-middle outline-none hover:bg-on-surface/5 focus-visible:bg-on-surface/8 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-secondary {isRunning(t) ? 'bg-tertiary-container/15' : ''}"
							tabindex="0"
							data-card={t.id}
							onclick={() => onopen(t)}
							onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onopen(t))}
						>
							<td class="border-t border-outline-variant/40 py-2 pr-2 pl-4">
								<div class="flex min-w-0 items-center gap-2 overflow-hidden">
									<Icon name={kindIcon(t)} size={18} class="shrink-0 {t.run || t.kind === 'command' ? 'text-primary' : 'text-on-surface-variant'}" />
									<div class="min-w-0 flex-1">
										<p class="truncate body-medium text-on-surface">{t.title}</p>
										{#if t.kind === 'command' && t.command}<p class="truncate font-mono text-[12px] text-on-surface-variant">{t.command}</p>{/if}
										<div class="mt-1 sm:hidden">{@render status(t)}</div>
									</div>
									<span class="ml-auto hidden shrink-0 items-center gap-2 label-small text-on-surface-variant sm:flex">
										{#if t.comment_count}<span class="flex items-center gap-0.5" title="{t.comment_count} comments"><Icon name="chat_bubble" size={14} />{t.comment_count}</span>{/if}
										{#if t.attachment_count}<span class="flex items-center gap-0.5" title="{t.attachment_count} files"><Icon name="attach_file" size={14} />{t.attachment_count}</span>{/if}
									</span>
								</div>
							</td>
							<td class="hidden overflow-hidden border-t border-outline-variant/40 px-2 py-2 whitespace-nowrap sm:table-cell">{@render status(t)}</td>
							<td class="hidden border-t border-outline-variant/40 px-2 py-2 lg:table-cell">
								{#if t.priority > 0}
									<span class="flex items-center gap-1.5 label-medium text-on-surface-variant"><PriorityGlyph level={t.priority} />{priorityLabels[t.priority] ?? ''}</span>
								{/if}
							</td>
							<td class="hidden border-t border-outline-variant/40 px-2 py-2 md:table-cell">
								{#if who.length}
									<span class="flex -space-x-1.5" title={who.map((u) => u.display_name).join(', ')}>
										{#each who.slice(0, 4) as u (u.id)}<span class="rounded-full ring-2 ring-surface-container-low"><AssigneeFace who={u} size={22} /></span>{/each}
										{#if who.length > 4}<span class="z-10 flex size-[22px] items-center justify-center rounded-full bg-surface-container-highest label-small ring-2 ring-surface-container-low">+{who.length - 4}</span>{/if}
									</span>
								{/if}
							</td>
							<td class="hidden border-t border-outline-variant/40 px-2 py-2 xl:table-cell">
								<span class="flex flex-wrap gap-1">
									{#each t.tags.slice(0, 4) as tag (tag)}<span class="rounded-xs border border-outline-variant px-1.5 py-0.5 label-small text-on-surface-variant">{tag}</span>{/each}
								</span>
							</td>
							<td class="hidden border-t border-outline-variant/40 px-2 py-2 body-small whitespace-nowrap text-on-surface-variant md:table-cell" title={absoluteTime(t.updated_at)}>
								{relativeTime(t.updated_at)}
							</td>
							<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (stops the row opening the card) -->
							<td class="border-t border-outline-variant/40 py-1 pr-2 text-right" onclick={(e) => e.stopPropagation()}>
								{#if !readonly}
									<Menu align="right">
										{#snippet anchor()}<IconButton icon="more_vert" label="Move {t.title}" size="sm" />{/snippet}
										<p class="px-3 pt-1 pb-2 label-medium text-on-surface-variant">Move to</p>
										{#each columns.filter((c) => c.id !== t.column_id) as c (c.id)}
											<MenuItem onclick={() => onmoveto(t.id, c.id)}>
												<span class="flex items-center gap-2"><ColumnGlyph role={c.role} size={18} />{c.name}</span>
											</MenuItem>
										{/each}
									</Menu>
								{/if}
							</td>
						</tr>
					{:else}
						<tr>
							<td class="border-t border-outline-variant/40 px-4 py-2.5 body-small text-on-surface-variant italic">No cards here.</td>
							{#each MIDDLE as cls, i (i)}<td class="{cls} border-t border-outline-variant/40"></td>{/each}
							<td class="border-t border-outline-variant/40"></td>
						</tr>
					{/each}
				{/if}
			</tbody>
		{/each}
	</table>
	{#if total === 0 && columns.length}
		<p class="px-4 py-6 text-center body-medium text-on-surface-variant">No cards match.</p>
	{/if}
</div>
