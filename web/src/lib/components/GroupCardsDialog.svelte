<script lang="ts">
	import Spoilers from '$lib/components/Spoilers.svelte';
	import type { Column, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, Progress, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { kindIcon } from '$lib/taskStatus';
	import ColumnGlyph from './ColumnGlyph.svelte';

	/** Choose cards on the board to put into one card as its subtasks: a new activity card
	 *  (`parent` unset: consolidating a cluttered board) or an existing one. Cards that can't be
	 *  subtasks (playbook runs, cards with subtasks of their own) aren't offered. */
	interface Props {
		open: boolean;
		projectId: UUID;
		columns: Column[];
		/** Add to this card; none: make a new one. */
		parent?: Task | null;
		/** Chosen to start with. */
		preselected?: UUID[];
		ondone: (parent: Task) => void;
	}
	let { open = $bindable(false), projectId, columns, parent = null, preselected = [], ondone }: Props = $props();

	let cards = $state<Task[]>([]);
	let loading = $state(false);
	let chosen = $state<Set<UUID>>(new Set());
	let title = $state('');
	let q = $state('');
	let saving = $state(false);

	$effect(() => {
		if (!open) return;
		chosen = new Set(preselected);
		title = '';
		q = '';
		loading = true;
		api()
			.projects.board(projectId)
			.then((b) => (cards = b.tasks.filter((t) => !t.run && !t.subtasks?.total && t.id !== parent?.id)))
			.catch((err) => snackbar.error(err instanceof ApiError ? err.message : 'Could not load the board'))
			.finally(() => (loading = false));
	});

	let shown = $derived.by(() => {
		const needle = q.trim().toLowerCase();
		const match = (t: Task) => !needle || t.title.toLowerCase().includes(needle) || t.tags.some((g) => g.toLowerCase().includes(needle));
		return columns
			.map((c) => ({ column: c, cards: cards.filter((t) => t.column_id === c.id && match(t)).sort((a, b) => a.position - b.position) }))
			.filter((g) => g.cards.length);
	});

	function toggle(id: UUID) {
		const next = new Set(chosen);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		chosen = next;
	}
	function toggleColumn(list: Task[]) {
		const all = list.every((t) => chosen.has(t.id));
		const next = new Set(chosen);
		for (const t of list) {
			if (all) next.delete(t.id);
			else next.add(t.id);
		}
		chosen = next;
	}

	// In board order, so the subtasks keep the order they had on the board.
	let ordered = $derived(shown.flatMap((g) => g.cards).filter((t) => chosen.has(t.id)).map((t) => t.id));
	let ready = $derived(chosen.size > 0 && (parent !== null || title.trim().length > 0));

	async function submit() {
		if (!ready) return;
		saving = true;
		const ids = [...ordered, ...[...chosen].filter((id) => !ordered.includes(id))];
		try {
			if (parent) {
				await api().tasks.addSubtasks(parent.id, ids);
				ondone(await api().tasks.get(parent.id));
				snackbar.show(`Added ${ids.length} card${ids.length === 1 ? '' : 's'} to “${parent.title}”`);
			} else {
				const made = await api().projects.consolidate(projectId, { title: title.trim(), task_ids: ids });
				ondone(made);
				snackbar.show(`Grouped ${ids.length} card${ids.length === 1 ? '' : 's'} into “${made.title}”`);
			}
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not group the cards');
		} finally {
			saving = false;
		}
	}
</script>

<Dialog bind:open title={parent ? `Add cards to “${parent.title}”` : 'Group cards into one'} icon="checklist" size="md">
	<div class="flex flex-col gap-3 pt-1 pb-2">
		<p class="body-medium text-on-surface-variant">
			{parent
				? 'The cards you choose become its subtasks: they leave the board and show up in its Subtasks tab.'
				: 'A new card holds the cards you choose as its subtasks, so the board shows one card instead of many. It goes in the column most of them are in.'}
		</p>
		{#if !parent}
			<TextField label="Title of the new card" bind:value={title} placeholder="Update homelab LXCs" required />
		{/if}
		<label class="flex items-center gap-2 rounded-full bg-surface-container-high px-4">
			<Icon name="search" size={20} class="text-on-surface-variant" />
			<input class="h-10 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter by title or tag" bind:value={q} />
		</label>
		{#if loading}
			<div class="flex justify-center py-8"><Progress variant="circular" size={24} /></div>
		{:else if !shown.length}
			<p class="py-6 text-center body-medium text-on-surface-variant">{q.trim() ? 'No cards match.' : 'There are no cards on the board that can be subtasks.'}</p>
		{:else}
			<div class="flex max-h-[50vh] flex-col gap-3 overflow-y-auto">
				{#each shown as group (group.column.id)}
					{@const all = group.cards.every((t) => chosen.has(t.id))}
					<section>
						<div class="mb-1 flex items-center gap-2 px-1">
							<ColumnGlyph role={group.column.role} size={16} />
							<span class="flex-1 label-large text-on-surface-variant">{group.column.name}</span>
							<button type="button" class="state-layer rounded-xs px-2 py-0.5 label-medium text-primary" onclick={() => toggleColumn(group.cards)}>
								{all ? 'Clear' : 'Choose all'}
							</button>
						</div>
						<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container">
							{#each group.cards as t (t.id)}
								<li>
									<label class="state-layer flex cursor-pointer items-center gap-3 px-3 py-2">
										<input type="checkbox" class="size-4 shrink-0 accent-[var(--md-sys-color-primary)]" checked={chosen.has(t.id)} onchange={() => toggle(t.id)} />
										<Icon name={kindIcon(t)} size={18} class="shrink-0 text-on-surface-variant" />
										<span class="min-w-0 flex-1 truncate body-medium text-on-surface"><Spoilers text={t.title} /></span>
										{#each t.tags.slice(0, 2) as tag (tag)}
											<span class="hidden shrink-0 rounded-xs border border-outline-variant px-1.5 py-0.5 label-small text-on-surface-variant sm:inline">{tag}</span>
										{/each}
									</label>
								</li>
							{/each}
						</ul>
					</section>
				{/each}
			</div>
		{/if}
	</div>
	{#snippet actions()}
		<span class="mr-auto body-medium text-on-surface-variant">{chosen.size} chosen</span>
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="checklist" disabled={!ready} loading={saving} onclick={submit}>{parent ? 'Add' : 'Group'}</Button>
	{/snippet}
</Dialog>
