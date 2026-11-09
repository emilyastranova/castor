<script lang="ts">
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';
	import { Icon } from '$lib/m3';

	let { children }: { children: Snippet } = $props();

	import { SECTIONS } from './sections';
</script>

<div class="flex min-h-0 min-w-0 flex-1">
	<nav class="hidden w-60 shrink-0 flex-col gap-1 border-r border-outline-variant/60 px-3 pt-5 md:flex" aria-label="Settings">
		<p class="mb-2 px-3 title-large text-on-surface">Settings</p>
		{#each SECTIONS as s (s.href)}
			{@const current = page.url.pathname.startsWith(s.href)}
			<a
				href={s.href}
				class="state-layer flex items-center gap-3 rounded-full px-3 py-2.5 {current ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface-variant'}"
				aria-current={current ? 'page' : undefined}
			>
				<Icon name={s.icon} filled={current} />
				<span class="min-w-0">
					<span class="block label-large">{s.label}</span>
					<span class="block truncate body-small opacity-80">{s.detail}</span>
				</span>
			</a>
		{/each}
	</nav>
	<div class="flex min-h-0 min-w-0 flex-1 flex-col">
		{#if page.url.pathname !== '/settings'}
			<!-- Phones: the sections are a list at /settings; this goes back to it. -->
			<a href="/settings" class="state-layer flex items-center gap-1 self-start rounded-full py-2 pr-3 pl-2 label-large text-primary md:hidden">
				<Icon name="arrow_back" size={20} />Settings
			</a>
		{/if}
		{@render children()}
	</div>
</div>
