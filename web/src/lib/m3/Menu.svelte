<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		open?: boolean;
		align?: 'left' | 'right';
		anchor?: Snippet;
		children?: Snippet;
		class?: string;
	}
	let { open = $bindable(false), align = 'right', anchor, children, class: klass = '' }: Props = $props();
	let root: HTMLDivElement | undefined = $state();

	function onDocClick(e: MouseEvent) {
		if (open && root && !root.contains(e.target as Node)) open = false;
	}
	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape') open = false;
	}
</script>

<svelte:document onclick={onDocClick} onkeydown={onKey} />

<div class="relative inline-block {klass}" bind:this={root}>
	<div onclick={() => (open = !open)} role="presentation">{@render anchor?.()}</div>
	{#if open}
		<div
			class="absolute z-40 mt-1 min-w-48 overflow-hidden rounded-xs bg-surface-container py-2 shadow-e2 {align === 'right' ? 'right-0' : 'left-0'}"
			role="menu"
			tabindex="-1"
			onclick={() => (open = false)}
			onkeydown={(e) => e.key === 'Enter' && (open = false)}
		>
			{@render children?.()}
		</div>
	{/if}
</div>
