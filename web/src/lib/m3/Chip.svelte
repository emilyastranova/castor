<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';
	import Icon from './Icon.svelte';

	type Variant = 'assist' | 'filter' | 'input' | 'suggestion';
	interface Props extends HTMLButtonAttributes {
		variant?: Variant;
		icon?: string;
		selected?: boolean;
		onremove?: () => void;
		elevated?: boolean;
		children?: Snippet;
	}
	let {
		variant = 'assist',
		icon,
		selected = false,
		onremove,
		elevated = false,
		class: klass = '',
		children,
		...rest
	}: Props = $props();
</script>

<button
	type="button"
	class="state-layer touch-target focus-ring inline-flex h-8 max-w-full items-center gap-2 rounded-sm px-3 label-large transition-colors disabled:opacity-40 {selected
		? 'bg-secondary-container text-on-secondary-container'
		: elevated
			? 'bg-surface-container-low text-on-surface shadow-e1'
			: 'border border-outline-variant text-on-surface-variant'} {icon || (variant === 'filter' && selected) ? 'pl-2' : ''} {onremove ? 'pr-2' : ''} {klass}"
	aria-pressed={variant === 'filter' ? selected : undefined}
	{...rest}
>
	{#if variant === 'filter' && selected}
		<Icon name="check" size={18} />
	{:else if icon}
		<Icon name={icon} size={18} class={selected ? '' : 'text-primary'} />
	{/if}
	<span class="truncate">{@render children?.()}</span>
	{#if onremove}
		<span
			role="button"
			tabindex="-1"
			class="touch-target -mr-1 inline-flex size-5 items-center justify-center rounded-full hover:bg-on-surface/10"
			aria-label="Remove"
			onclick={(e) => {
				e.stopPropagation();
				onremove();
			}}
			onkeydown={(e) => e.key === 'Enter' && onremove()}
		>
			<Icon name="close" size={16} />
		</span>
	{/if}
</button>
