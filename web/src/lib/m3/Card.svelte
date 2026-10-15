<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAttributes } from 'svelte/elements';

	type Variant = 'elevated' | 'filled' | 'outlined';
	interface Props extends HTMLAttributes<HTMLDivElement> {
		variant?: Variant;
		interactive?: boolean;
		children?: Snippet;
	}
	let { variant = 'filled', interactive = false, class: klass = '', children, ...rest }: Props = $props();
	const variants: Record<Variant, string> = {
		elevated: 'bg-surface-container-low shadow-e1',
		filled: 'bg-surface-container-highest',
		outlined: 'bg-surface border border-outline-variant'
	};
</script>

<div
	class="rounded-md text-on-surface {variants[variant]} {interactive ? 'state-layer cursor-pointer transition-shadow hover:shadow-e2' : ''} {klass}"
	{...rest}
>
	{@render children?.()}
</div>
