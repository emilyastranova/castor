<script lang="ts">
	import type { HTMLButtonAttributes } from 'svelte/elements';
	import Icon from './Icon.svelte';

	type Variant = 'standard' | 'filled' | 'tonal' | 'outlined';
	interface Props extends HTMLButtonAttributes {
		icon: string;
		label: string;
		variant?: Variant;
		selected?: boolean;
		size?: 'sm' | 'md';
		href?: string;
	}
	let {
		icon,
		label,
		variant = 'standard',
		selected = false,
		size = 'md',
		href,
		class: klass = '',
		...rest
	}: Props = $props();

	const variants: Record<Variant, string> = {
		standard: 'text-on-surface-variant',
		filled: 'bg-primary text-on-primary',
		tonal: 'bg-secondary-container text-on-secondary-container',
		outlined: 'border border-outline text-on-surface-variant'
	};
	let cls = $derived(
		`state-layer focus-ring inline-flex shrink-0 items-center justify-center rounded-full transition-colors disabled:pointer-events-none disabled:opacity-40 ${size === 'sm' ? 'size-8' : 'size-10'} ${variants[variant]} ${selected ? 'text-primary' : ''} ${klass}`
	);
	let iconSize = $derived(size === 'sm' ? 20 : 24);
</script>

{#if href}
	<a {href} class={cls} aria-label={label} title={label}>
		<Icon name={icon} filled={selected} size={iconSize} />
	</a>
{:else}
	<button type="button" class={cls} aria-label={label} title={label} aria-pressed={selected || undefined} {...rest}>
		<Icon name={icon} filled={selected} size={iconSize} />
	</button>
{/if}
