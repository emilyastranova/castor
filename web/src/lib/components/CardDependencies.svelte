<script lang="ts">
	import Spoilers from '$lib/components/Spoilers.svelte';
	import { maskSpoilers } from '$lib/spoilers';
	import type { CardDependencies, CardRef, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Icon, IconButton, Select } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	/** What a card waits on (it's blocked until each is done, reported or archived) and which
	 *  cards wait on it. Adding picks any other card of the project. */
	interface Props {
		task: Task;
		editable?: boolean;
		/** Save a new list (the server checks it: same project, no circles). */
		onchange: (ids: UUID[]) => Promise<void> | void;
		onopen: (id: UUID) => void;
	}
	let { task, editable = true, onchange, onopen }: Props = $props();

	let deps = $state<CardDependencies | null>(null);
	let cards = $state<Task[] | null>(null);
	// Reload when the card's list changes (here or elsewhere) or what it waits on moves.
	let key = $derived(`${task.id}:${(task.waits_on ?? []).join(',')}:${(task.blocked_by ?? []).map((b) => b.id).join(',')}`);
	$effect(() => {
		void key;
		const id = task.id;
		api()
			.tasks.dependencies(id)
			.then((d) => id === task.id && (deps = d))
			.catch(() => {});
	});
	async function loadCards() {
		if (cards) return;
		try {
			cards = (await api().projects.board(task.project_id)).tasks;
		} catch {
			cards = [];
		}
	}
	let options = $derived.by(() => {
		const taken = new Set([task.id, ...(task.waits_on ?? [])]);
		return (cards ?? [])
			.filter((c) => !taken.has(c.id))
			.sort((a, b) => a.title.localeCompare(b.title))
			.map((c) => ({ value: c.id, label: maskSpoilers(c.title), keywords: c.tags.join(' ') }));
	});
	async function add(id: string) {
		if (!id) return;
		try {
			await onchange([...(task.waits_on ?? []), id]);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not add it');
		}
	}
	async function remove(id: UUID) {
		try {
			await onchange((task.waits_on ?? []).filter((x) => x !== id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove it');
		}
	}
	let blocked = $derived(task.blocked_by?.length ?? 0);
</script>

{#snippet row(c: CardRef, removable: boolean)}
	<li class="flex items-center gap-1">
		<button type="button" class="state-layer flex min-w-0 flex-1 items-center gap-2 rounded-sm px-1.5 py-1 text-left" onclick={() => onopen(c.id)}>
			<Icon name={c.finished ? 'check_circle' : 'radio_button_unchecked'} size={18} class="shrink-0 {c.finished ? 'text-primary' : 'text-on-surface-variant'}" />
			<span class="min-w-0 flex-1">
				<span class="block truncate body-medium {c.finished ? 'text-on-surface-variant line-through' : 'text-on-surface'}"><Spoilers text={c.title} /></span>
				<span class="block truncate body-small text-on-surface-variant">{c.archived ? 'Archived' : c.column}</span>
			</span>
		</button>
		{#if removable && editable}<IconButton icon="close" size="sm" label="Stop waiting on {maskSpoilers(c.title)}" onclick={() => remove(c.id)} />{/if}
	</li>
{/snippet}

<div class="flex flex-col gap-1.5">
	<div class="flex items-center gap-2">
		<span class="flex-1 label-large text-on-surface-variant">Depends on</span>
		{#if blocked}
			<span class="inline-flex items-center gap-1 rounded-sm bg-tertiary-container px-1.5 py-0.5 label-small text-on-tertiary-container" title="It waits on {blocked} unfinished card{blocked === 1 ? '' : 's'}">
				<Icon name="lock" size={14} />Blocked
			</span>
		{:else if task.waits_on?.length}
			<span class="inline-flex items-center gap-1 label-small text-primary"><Icon name="lock_open" size={14} />Ready</span>
		{/if}
	</div>
	{#if deps?.waits_on.length}
		<ul class="flex flex-col">
			{#each deps.waits_on as c (c.id)}{@render row(c, true)}{/each}
		</ul>
	{:else if !editable}
		<p class="body-small text-on-surface-variant">Nothing.</p>
	{/if}
	{#if editable}
		<div onfocusin={loadCards} onpointerdown={loadCards} role="presentation">
			<Select label={cards && !options.length ? 'No other cards' : 'Add a card it waits on'} value="" options={options} onchange={(v) => void add(v)} disabled={cards !== null && !options.length} searchable />
		</div>
	{/if}
	{#if deps?.blocks.length}
		<span class="mt-1 label-large text-on-surface-variant">Blocks</span>
		<ul class="flex flex-col">
			{#each deps.blocks as c (c.id)}{@render row(c, false)}{/each}
		</ul>
	{/if}
</div>
