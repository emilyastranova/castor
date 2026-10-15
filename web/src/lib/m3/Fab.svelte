<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';
	import Icon from './Icon.svelte';

	interface Props extends HTMLButtonAttributes {
		icon: string;
		label: string;
		extended?: boolean;
		color?: 'primary' | 'secondary' | 'tertiary' | 'surface';
		children?: Snippet;
	}
	let { icon, label, extended = false, color = 'primary', class: klass = '', children, ...rest }: Props =
		$props();
	const colors = {
		primary: 'bg-primary-container text-on-primary-container',
		secondary: 'bg-secondary-container text-on-secondary-container',
		tertiary: 'bg-tertiary-container text-on-tertiary-container',
		surface: 'bg-surface-container-high text-primary'
	};
</script>

<button
	type="button"
	class="state-layer focus-ring inline-flex h-14 items-center justify-center gap-3 rounded-lg shadow-e3 transition-shadow hover:shadow-e4 label-large {colors[color]} {extended ? 'px-5' : 'w-14'} {klass}"
	aria-label={label}
	title={label}
	{...rest}
>
	<Icon name={icon} />
	{#if extended}{@render children?.()}{/if}
</button>
