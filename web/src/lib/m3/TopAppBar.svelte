<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		title?: string;
		subtitle?: string;
		leading?: Snippet;
		actions?: Snippet;
		children?: Snippet;
		class?: string;
	}
	let { title, subtitle, leading, actions, children, class: klass = '' }: Props = $props();
</script>

<header class="flex h-16 shrink-0 items-center gap-2 bg-surface px-4 {klass}">
	{@render leading?.()}
	<div class="min-w-0 flex-1">
		{#if children}
			{@render children()}
		{:else}
			<h1 class="truncate title-large text-on-surface">{title}</h1>
			{#if subtitle}<p class="truncate body-small text-on-surface-variant">{subtitle}</p>{/if}
		{/if}
	</div>
	<div class="flex items-center gap-1">{@render actions?.()}</div>
</header>
