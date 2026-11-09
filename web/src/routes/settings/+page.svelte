<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { Icon, TopAppBar } from '$lib/m3';
	import { SECTIONS } from './sections';

	// Wide enough for the section list beside the page: open the first section, as before.
	onMount(() => {
		if (matchMedia('(min-width: 768px)').matches) void goto('/settings/general', { replaceState: true });
	});
</script>

<svelte:head><title>Settings · Castor</title></svelte:head>

<div class="md:hidden">
	<TopAppBar title="Settings" />
	<nav class="flex flex-col px-2 pb-6" aria-label="Settings">
		{#each SECTIONS as s (s.href)}
			<a href={s.href} class="state-layer flex min-h-14 items-center gap-4 rounded-lg px-3 py-2 text-on-surface">
				<span class="flex size-10 shrink-0 items-center justify-center rounded-full bg-secondary-container text-on-secondary-container"><Icon name={s.icon} /></span>
				<span class="min-w-0 flex-1">
					<span class="block title-small">{s.label}</span>
					<span class="block truncate body-small text-on-surface-variant">{s.detail}</span>
				</span>
				<Icon name="chevron_right" class="text-on-surface-variant" />
			</a>
		{/each}
	</nav>
</div>
