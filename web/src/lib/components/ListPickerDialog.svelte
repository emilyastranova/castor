<script lang="ts">
	import { Button, Dialog, Icon } from '$lib/m3';
	import { lists, type SavedList } from '$lib/lists.svelte';
	import { kindIcon } from '$lib/variables';

	/** Choose a saved list (target lists and other sets with a list field), or go import one. */
	interface Props {
		open: boolean;
		projectId?: string | null;
		onpick: (list: SavedList) => void;
		onimport: () => void;
		/** Fill it from the project's assets instead (3f). */
		onassets?: () => void;
	}
	let { open = $bindable(false), projectId = null, onpick, onimport, onassets }: Props = $props();

	let query = $state('');
	$effect(() => {
		if (open) {
			query = '';
			void lists.load(true);
		}
	});

	let shown = $derived(
		lists.forProject(projectId).filter((l) => {
			const q = query.trim().toLowerCase();
			return !q || l.set.name.toLowerCase().includes(q) || l.entries.some((e) => e.toLowerCase().includes(q));
		})
	);
</script>

<Dialog bind:open title="Use a saved list" icon="list_alt" size="md" initialFocus="dialog">
	<div class="flex flex-col gap-3 pt-2 pb-2">
		<label class="flex items-center gap-2 rounded-full bg-surface-container-high px-4">
			<Icon name="search" size={20} class="text-on-surface-variant" />
			<input class="h-10 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Search lists or entries" bind:value={query} autocomplete="off" />
		</label>
		<ul class="flex max-h-[26rem] flex-col gap-2 overflow-auto">
			{#each shown as l (l.set.id)}
				<li>
					<button
						type="button"
						class="state-layer flex w-full items-start gap-3 rounded-md border border-outline-variant px-3 py-2.5 text-left hover:border-outline"
						onclick={() => {
							onpick(l);
							open = false;
						}}
					>
						<Icon name={l.kind.id === 'target-list' ? 'my_location' : kindIcon(l.kind.id)} class="mt-0.5 text-primary" />
						<span class="min-w-0 flex-1">
							<span class="flex items-baseline gap-2">
								<span class="truncate title-small text-on-surface">{l.set.name}</span>
								<span class="shrink-0 body-small text-on-surface-variant">{l.entries.length} entr{l.entries.length === 1 ? 'y' : 'ies'}</span>
							</span>
							<span class="block truncate font-mono text-[12px] text-on-surface-variant">
								{l.entries.slice(0, 6).join(', ')}{l.entries.length > 6 ? ', …' : ''}
							</span>
							{#if l.kind.id !== 'target-list'}
								<span class="block body-small text-on-surface-variant">{l.kind.name} · {l.field}</span>
							{/if}
						</span>
					</button>
				</li>
			{:else}
				<li class="px-2 py-8 text-center body-medium text-on-surface-variant">
					{lists.loaded ? (query ? 'No list matches.' : 'No saved lists yet. Import one to get started.') : 'Loading…'}
				</li>
			{/each}
		</ul>
	</div>
	{#snippet actions()}
		<a href="/variables" class="mr-auto self-center body-small text-primary hover:underline">Manage lists</a>
		{#if onassets && projectId}
			<Button
				variant="text"
				icon="radar"
				onclick={() => {
					open = false;
					onassets();
				}}>From assets…</Button
			>
		{/if}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button
			variant="tonal"
			icon="upload_file"
			onclick={() => {
				open = false;
				onimport();
			}}>Import from a file or paste…</Button
		>
	{/snippet}
</Dialog>
