<script lang="ts">
	import Spoilers from '$lib/components/Spoilers.svelte';
	import { untrack } from 'svelte';
	import type { Column, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, Progress } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { absoluteTime, relativeTime } from '$lib/format';
	import { kindIcon } from '$lib/taskStatus';

	/** A project's archived cards: find one, open it, or put it back on the board. */
	interface Props {
		open: boolean;
		projectId: UUID;
		columns: Column[];
		editable?: boolean;
		onopen: (task: Task) => void;
		onrestored: (task: Task) => void;
	}
	let { open = $bindable(false), projectId, columns, editable = false, onopen, onrestored }: Props = $props();

	let q = $state('');
	let items = $state<Task[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let loaded = $state(false); // the empty message only once there's an answer
	let restoring = $state<UUID | null>(null);

	async function load(more = false) {
		loading = true;
		const query = q;
		try {
			const r = await api().projects.archived(projectId, query.trim(), more ? items.length : 0);
			if (query !== q) return;
			items = more ? [...items, ...r.items] : r.items;
			total = r.total;
			loaded = true;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load the archived cards');
		} finally {
			loading = false;
		}
	}
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		void q;
		if (!open) return;
		clearTimeout(timer);
		// Only q (and opening) reloads: reading items here would make every load start another.
		timer = setTimeout(() => void load(), untrack(() => items.length) || q ? 200 : 0);
		return () => clearTimeout(timer);
	});

	const columnName = (id: UUID) => columns.find((c) => c.id === id)?.name ?? 'a column';
	async function restore(t: Task) {
		restoring = t.id;
		try {
			const back = await api().tasks.restore(t.id);
			items = items.filter((x) => x.id !== t.id);
			total -= 1;
			onrestored(back);
			snackbar.show(`“${t.title}” is back in ${columnName(t.column_id)}`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not restore it');
		} finally {
			restoring = null;
		}
	}
</script>

<Dialog bind:open title="Archived cards" icon="inventory_2" size="md">
	<div class="flex flex-col gap-3 pt-1 pb-2">
		<label class="flex items-center gap-2 rounded-full bg-surface-container-high px-4">
			<Icon name="search" size={20} class="text-on-surface-variant" />
			<input class="h-10 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Search titles, descriptions and tags" bind:value={q} />
		</label>
		{#if !loaded || (loading && !items.length)}
			<div class="flex justify-center py-8"><Progress variant="circular" size={24} /></div>
		{:else if !items.length}
			<div class="flex flex-col items-center gap-2 py-8 text-center">
				<Icon name="inventory_2" size={36} class="text-on-surface-variant" />
				<p class="body-medium text-on-surface-variant">
					{q.trim() ? 'No archived cards match.' : 'Nothing archived yet. Archive a card from its menu, or a whole column from its header.'}
				</p>
			</div>
		{:else}
			<p class="body-small text-on-surface-variant">{total} archived card{total === 1 ? '' : 's'}{q.trim() ? ' match' : ''}</p>
			<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container">
				{#each items as t (t.id)}
					<li class="flex items-center gap-2 pr-2">
						<button type="button" class="state-layer flex min-w-0 flex-1 items-center gap-3 px-3 py-2 text-left" onclick={() => onopen(t)}>
							<Icon name={kindIcon(t)} size={18} class="shrink-0 text-on-surface-variant" />
							<span class="min-w-0 flex-1">
								<span class="block truncate body-medium text-on-surface"><Spoilers text={t.title} /></span>
								<span class="block truncate body-small text-on-surface-variant" title={t.archived_at ? absoluteTime(t.archived_at) : ''}>
									From {columnName(t.column_id)} · archived {t.archived_at ? relativeTime(t.archived_at) : ''}
								</span>
							</span>
						</button>
						{#if editable}
							<Button variant="text" icon="unarchive" loading={restoring === t.id} onclick={() => restore(t)}>Restore</Button>
						{/if}
					</li>
				{/each}
			</ul>
			{#if items.length < total}
				<Button variant="text" class="self-center" loading={loading} onclick={() => load(true)}>Show more</Button>
			{/if}
		{/if}
	</div>
</Dialog>
