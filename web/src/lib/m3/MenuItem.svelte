<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';
	interface Props {
		icon?: string;
		danger?: boolean;
		disabled?: boolean;
		onclick?: () => void;
		children?: Snippet;
	}
	let { icon, danger = false, disabled = false, onclick, children }: Props = $props();
</script>

<button
	type="button"
	role="menuitem"
	class="state-layer flex h-12 w-full items-center gap-3 px-3 text-left label-large disabled:opacity-40 {danger ? 'text-error' : 'text-on-surface'}"
	{disabled}
	{onclick}
>
	{#if icon}<Icon name={icon} class={danger ? 'text-error' : 'text-on-surface-variant'} />{/if}
	<span class="flex-1">{@render children?.()}</span>
</button>
