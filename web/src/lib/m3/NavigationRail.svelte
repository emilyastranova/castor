<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';

	export interface NavItem {
		href: string;
		icon: string;
		label: string;
		badge?: number | string;
	}
	interface Props {
		items: NavItem[];
		current: string;
		top?: Snippet;
		bottom?: Snippet;
	}
	let { items, current, top, bottom }: Props = $props();
	const active = (href: string) => (href === '/' ? current === '/' : current.startsWith(href));
</script>

<nav class="flex h-full w-20 shrink-0 flex-col items-center gap-3 bg-surface py-3" aria-label="Main">
	{@render top?.()}
	<div class="flex flex-1 flex-col items-center gap-3 pt-2">
		{#each items as item (item.href)}
			<a
				href={item.href}
				class="group flex w-full flex-col items-center gap-1 focus-ring rounded-md"
				aria-current={active(item.href) ? 'page' : undefined}
			>
				<span
					class="state-layer relative flex h-8 w-14 items-center justify-center rounded-full transition-colors {active(item.href)
						? 'bg-secondary-container text-on-secondary-container'
						: 'text-on-surface-variant group-hover:text-on-surface'}"
				>
					<Icon name={item.icon} filled={active(item.href)} />
					{#if item.badge}
						<span class="absolute -top-1 right-2 min-w-4 rounded-full bg-error px-1 text-center text-on-error label-small">{item.badge}</span>
					{/if}
				</span>
				<span class="label-medium {active(item.href) ? 'text-on-surface' : 'text-on-surface-variant'}">{item.label}</span>
			</a>
		{/each}
	</div>
	{@render bottom?.()}
</nav>
