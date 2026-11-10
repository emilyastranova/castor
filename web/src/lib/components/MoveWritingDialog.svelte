<script lang="ts">
	import type { Column, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Dialog, Icon } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { kindIcon } from '$lib/taskStatus';

	/** Where a comment or a timeline post goes: a card of the same project (searched by title
	 *  or tag, grouped by column), or the project's timeline. `onpick` does the move; the dialog
	 *  closes when it's done and stays open (saying why) when it fails. */
	interface Props {
		open: boolean;
		projectId: UUID;
		/** What's moving, for the title ("comment" or "post"). */
		what: 'comment' | 'post';
		/** The card it's on now (not offered). */
		fromTaskId?: UUID | null;
		/** Offer the timeline (moving a comment off its card). */
		timeline?: boolean;
		onpick: (toTaskId: UUID | null) => Promise<void>;
	}
	let { open = $bindable(false), projectId, what, fromTaskId = null, timeline = false, onpick }: Props = $props();

	let columns = $state<Column[]>([]);
	let tasks = $state<Task[]>([]);
	let loading = $state(false);
	let query = $state('');
	let moving = $state<string | null>(null);
	let search = $state<HTMLInputElement>();

	$effect(() => {
		if (!open) return;
		query = '';
		moving = null;
		loading = true;
		void api()
			.projects.board(projectId)
			.then((b) => {
				columns = [...b.columns].sort((x, y) => x.position - y.position);
				// Step cards belong to their run's card; the run card itself is offered.
				tasks = b.tasks.filter((t) => !t.playbook_step_id && t.id !== fromTaskId);
			})
			.catch(() => snackbar.error("Couldn't load the board"))
			.finally(() => {
				loading = false;
				requestAnimationFrame(() => search?.focus());
			});
	});

	let groups = $derived.by(() => {
		const q = query.trim().toLowerCase().replace(/^#/, '');
		const match = (t: Task) => !q || t.title.toLowerCase().includes(q) || t.tags.some((g) => g.toLowerCase().includes(q));
		return columns
			.map((c) => ({ column: c, tasks: tasks.filter((t) => t.column_id === c.id && match(t)).sort((a, b) => a.position - b.position) }))
			.filter((g) => g.tasks.length);
	});
	let showTimeline = $derived(timeline && (!query.trim() || 'timeline'.includes(query.trim().toLowerCase())));
	let flat = $derived([...(showTimeline ? [null] : []), ...groups.flatMap((g) => g.tasks.map((t) => t.id))]);
	let active = $state(0);
	$effect(() => {
		void query;
		active = 0;
	});

	async function pick(id: UUID | null) {
		if (moving) return;
		moving = id ?? 'timeline';
		try {
			await onpick(id);
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : `Couldn't move the ${what}`);
		} finally {
			moving = null;
		}
	}
	function keys(e: KeyboardEvent) {
		if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			e.preventDefault();
			active = Math.max(0, Math.min(flat.length - 1, active + (e.key === 'ArrowDown' ? 1 : -1)));
			document.getElementById(`move-opt-${active}`)?.scrollIntoView({ block: 'nearest' });
		} else if (e.key === 'Enter' && flat.length) {
			e.preventDefault();
			void pick(flat[active] as UUID | null);
		}
	}
	const indexOf = (id: UUID | null) => flat.indexOf(id);
</script>

<Dialog bind:open title={`Move this ${what}`} icon="drive_file_move" size="md">
	<div class="flex min-h-0 flex-col gap-3 pb-2">
		<p class="body-medium text-on-surface-variant">
			{what === 'post' ? 'It becomes a comment on the card you pick,' : timeline ? 'Pick a card, or the timeline (where it becomes a post),' : 'Pick a card,'} keeping its author, text and when it was written.
		</p>
		<label class="flex h-11 items-center gap-2 rounded-full bg-surface-container-highest px-4 text-on-surface-variant focus-within:outline-2 focus-within:outline-secondary">
			<Icon name="search" size={20} />
			<input
				bind:this={search}
				bind:value={query}
				class="min-w-0 flex-1 bg-transparent body-large text-on-surface outline-none placeholder:text-on-surface-variant"
				placeholder="Find a card by title or tag"
				aria-label="Find a card"
				aria-controls="move-options"
				aria-activedescendant={flat.length ? `move-opt-${active}` : undefined}
				onkeydown={keys}
			/>
		</label>
		<div id="move-options" role="listbox" aria-label="Where to move it" class="-mx-2 max-h-[min(52vh,480px)] overflow-y-auto px-2">
			{#if loading}
				<p class="py-6 text-center body-medium text-on-surface-variant">Loading the board…</p>
			{:else}
				{#if showTimeline}
					{@const i = indexOf(null)}
					<button
						type="button"
						id="move-opt-{i}"
						role="option"
						aria-selected={active === i}
						class="state-layer mb-2 flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left {active === i ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface'}"
						onmouseenter={() => (active = i)}
						onclick={() => pick(null)}
					>
						<span class="flex size-8 shrink-0 items-center justify-center rounded-full bg-tertiary-container text-on-tertiary-container"><Icon name="timeline" size={18} /></span>
						<span class="min-w-0 flex-1">
							<span class="block label-large">The project's timeline</span>
							<span class="block body-small text-on-surface-variant">As a post on Home, not tied to a card</span>
						</span>
						{#if moving === 'timeline'}<Icon name="progress_activity" size={18} class="animate-spin" />{/if}
					</button>
				{/if}
				{#each groups as g (g.column.id)}
					<p class="sticky top-0 z-10 bg-surface-container-high px-1 pt-2 pb-1 label-medium text-on-surface-variant">{g.column.name}</p>
					{#each g.tasks as t (t.id)}
						{@const i = indexOf(t.id)}
						<button
							type="button"
							id="move-opt-{i}"
							role="option"
							aria-selected={active === i}
							class="state-layer flex w-full items-center gap-3 rounded-md px-3 py-2 text-left {active === i ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface'}"
							onmouseenter={() => (active = i)}
							onclick={() => pick(t.id)}
						>
							<Icon name={kindIcon(t)} size={18} class="shrink-0 {t.run || t.kind === 'command' ? 'text-primary' : 'text-on-surface-variant'}" />
							<span class="min-w-0 flex-1 truncate body-medium">{t.title}</span>
							{#each t.tags.slice(0, 2) as tag (tag)}<span class="hidden shrink-0 rounded-xs border border-outline-variant px-1.5 label-small text-on-surface-variant sm:inline">{tag}</span>{/each}
							{#if moving === t.id}<Icon name="progress_activity" size={18} class="animate-spin" />{/if}
						</button>
					{/each}
				{:else}
					{#if !showTimeline}<p class="py-6 text-center body-medium text-on-surface-variant">{query.trim() ? 'No card matches.' : 'No other cards in this project.'}</p>{/if}
				{/each}
			{/if}
		</div>
	</div>
</Dialog>
