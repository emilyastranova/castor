<script lang="ts">
	import { tick } from 'svelte';
	import { Icon } from '$lib/m3';
	import { GROUPS, QUICK, searchEmoji } from '$lib/emoji';
	import { customEmojis } from '$lib/emojis.svelte';
	import Emoji from './Emoji.svelte';

	/** A popover to pick an emoji: the quick ones, search, every group, and the instance's own. */
	interface Props {
		open: boolean;
		/** The button it opens from (it's placed next to it, inside the window). */
		anchor: HTMLElement | null;
		onpick: (emoji: string) => void;
	}
	let { open = $bindable(false), anchor, onpick }: Props = $props();

	let query = $state('');
	let panel: HTMLDivElement | undefined = $state();
	let input: HTMLInputElement | undefined = $state();
	let pos = $state({ left: 0, top: 0 });
	const W = 336, H = 380;

	$effect(() => {
		if (!open) return;
		customEmojis.load();
		query = '';
		const r = anchor?.getBoundingClientRect();
		if (r) {
			const below = r.bottom + 6 + H < window.innerHeight;
			pos = {
				left: Math.max(8, Math.min(r.left, window.innerWidth - W - 8)),
				top: below ? r.bottom + 6 : Math.max(8, r.top - H - 6)
			};
		}
		void tick().then(() => input?.focus());
	});

	const customs = $derived(customEmojis.list);
	const found = $derived.by(() => {
		const q = query.trim().toLowerCase().replaceAll(':', '');
		if (!q) return null;
		const mine = customs.filter((c) => c.name.includes(q)).map((c) => `:${c.name}:`);
		return [...mine, ...searchEmoji(q)];
	});

	function pick(e: string) {
		onpick(e);
		open = false;
		anchor?.focus();
	}
	function outside(e: MouseEvent) {
		if (open && panel && !panel.contains(e.target as Node) && !anchor?.contains(e.target as Node)) open = false;
	}
	function key(e: KeyboardEvent) {
		if (!open) return;
		if (e.key === 'Escape') {
			e.stopPropagation();
			open = false;
			anchor?.focus();
		}
	}
</script>

<svelte:document onmousedown={outside} onkeydowncapture={key} />

{#if open}
	<div
		bind:this={panel}
		class="fixed z-[60] flex flex-col overflow-hidden rounded-md border border-outline-variant bg-surface-container shadow-e3"
		style:left="{pos.left}px"
		style:top="{pos.top}px"
		style:width="{W}px"
		style:height="{H}px"
		role="dialog"
		aria-label="Pick an emoji"
	>
		<div class="flex items-center gap-2 border-b border-outline-variant px-3 py-2">
			<Icon name="search" size={18} class="text-on-surface-variant" />
			<input
				bind:this={input}
				bind:value={query}
				class="h-8 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant"
				placeholder="Find an emoji"
				aria-label="Find an emoji"
				onkeydown={(e) => {
					if (e.key === 'Enter' && found?.length) {
						e.preventDefault();
						pick(found[0]);
					}
				}}
			/>
		</div>
		<div class="flex-1 overflow-y-auto px-2 py-2">
			{#snippet grid(list: string[])}
				<div class="grid grid-cols-8 gap-0.5">
					{#each list as e (e)}
						<button type="button" class="state-layer focus-ring flex size-9 items-center justify-center rounded-sm" title={e} aria-label={e} onclick={() => pick(e)}>
							<Emoji emoji={e} size={24} />
						</button>
					{/each}
				</div>
			{/snippet}
			{#if found}
				{#if found.length}{@render grid(found)}{:else}<p class="px-2 py-6 text-center body-medium text-on-surface-variant">No emoji found for “{query}”</p>{/if}
			{:else}
				{@render grid(QUICK)}
				{#if customs.length}
					<p class="mt-3 mb-1 px-1 label-medium text-on-surface-variant">This instance’s</p>
					{@render grid(customs.map((c) => `:${c.name}:`))}
				{/if}
				{#each GROUPS as group (group.name)}
					<p class="mt-3 mb-1 flex items-center gap-1 px-1 label-medium text-on-surface-variant"><Icon name={group.icon} size={14} />{group.name}</p>
					{@render grid(group.items.map(([e]) => e))}
				{/each}
			{/if}
		</div>
	</div>
{/if}
