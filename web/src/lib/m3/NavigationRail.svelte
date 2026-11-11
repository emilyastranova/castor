<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';

	export interface NavItem {
		href: string;
		icon: string;
		label: string;
		badge?: number | string;
		/** Active only on this exact path (for a link that's the parent of other items). */
		exact?: boolean;
		/** Draw a divider before this item (starts a new group). */
		divider?: boolean;
	}
	interface Props {
		items: NavItem[];
		current: string;
		top?: Snippet;
		bottom?: Snippet;
	}
	let { items, current, top, bottom }: Props = $props();
	const active = (item: NavItem) => (item.exact || item.href === '/' ? current === item.href : current.startsWith(item.href));
</script>

<!-- On short windows the destinations tighten, then drop their labels (tooltip and screen-reader
     text keep them), then scroll; the top and bottom snippets stay put. -->
<nav class="flex h-full w-20 shrink-0 flex-col items-center gap-3 bg-surface py-3 short:gap-2 short:py-2" aria-label="Main">
	{@render top?.()}
	<div class="flex min-h-0 w-full flex-1 flex-col items-center gap-3 overflow-y-auto py-1 [scrollbar-width:none] short:gap-1">
		{#each items as item (item.label)}
			{#if item.divider}<span class="h-px w-10 shrink-0 bg-outline-variant" aria-hidden="true"></span>{/if}
			<a
				href={item.href}
				title={item.label}
				class="group flex w-full shrink-0 flex-col items-center gap-1 outline-none short:gap-0"
				aria-current={active(item) ? 'page' : undefined}
			>
				<span
					data-active={active(item) || undefined}
					class="m3-rail-ind state-layer relative flex h-8 w-14 items-center justify-center rounded-full transition-colors group-focus-visible:outline-3 group-focus-visible:outline-offset-2 group-focus-visible:outline-secondary {active(item)
						? 'bg-secondary-container text-on-secondary-container'
						: 'text-on-surface-variant group-hover:text-on-surface'}"
				>
					<Icon name={item.icon} filled={active(item)} />
					{#if item.badge}
						<span class="absolute -top-1 right-2 min-w-4 rounded-full bg-error px-1 text-center text-on-error label-small">{item.badge}</span>
					{/if}
				</span>
				<span class="m3-rail-label label-medium shorter:sr-only {active(item) ? 'text-on-surface' : 'text-on-surface-variant'}">{item.label}</span>
			</a>
		{/each}
	</div>
	{@render bottom?.()}
</nav>
