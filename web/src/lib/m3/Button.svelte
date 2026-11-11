<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';
	import Icon from './Icon.svelte';

	type Variant = 'filled' | 'tonal' | 'outlined' | 'text' | 'elevated' | 'danger';
	interface Props extends HTMLButtonAttributes {
		variant?: Variant;
		icon?: string;
		trailingIcon?: string;
		href?: string;
		/** With href: open it in a new tab (for pages outside the app, like the API docs). */
		external?: boolean;
		loading?: boolean;
		children?: Snippet;
	}
	let {
		variant = 'filled',
		icon,
		trailingIcon,
		href,
		external = false,
		loading = false,
		disabled,
		class: klass = '',
		children,
		...rest
	}: Props = $props();

	const base =
		'state-layer touch-target focus-ring inline-flex h-10 items-center justify-center gap-2 rounded-full px-6 label-large transition-[box-shadow,background-color] duration-150 select-none disabled:pointer-events-none disabled:opacity-40 whitespace-nowrap';
	const variants: Record<Variant, string> = {
		filled: 'bg-primary text-on-primary hover:shadow-e1',
		tonal: 'bg-secondary-container text-on-secondary-container hover:shadow-e1',
		outlined: 'border border-outline text-primary',
		text: 'text-primary px-3',
		elevated: 'bg-surface-container-low text-primary shadow-e1 hover:shadow-e2',
		danger: 'bg-error text-on-error hover:shadow-e1'
	};
	let cls = $derived(`m3-btn m3-btn-${variant} ${base} ${variants[variant]} ${icon ? 'pl-4' : ''} ${trailingIcon ? 'pr-4' : ''} ${klass}`);
</script>

{#if href}
	<a {href} class={cls} aria-disabled={disabled || loading} target={external ? '_blank' : undefined} rel={external ? 'noopener' : undefined}>
		{#if icon}<Icon name={icon} size={18} />{/if}
		{@render children?.()}
		{#if trailingIcon}<Icon name={trailingIcon} size={18} />{/if}
	</a>
{:else}
	<button class={cls} disabled={disabled || loading} {...rest}>
		{#if loading}
			<span class="size-4 animate-spin rounded-full border-2 border-current border-t-transparent"></span>
		{:else if icon}<Icon name={icon} size={18} />{/if}
		{@render children?.()}
		{#if trailingIcon}<Icon name={trailingIcon} size={18} />{/if}
	</button>
{/if}
