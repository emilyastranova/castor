<script lang="ts">
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';
	import { Icon } from '$lib/m3';

	let { children }: { children: Snippet } = $props();

	/** Admin settings, one section per page; add new ones here. */
	const SECTIONS = [
		{ href: '/settings/users', icon: 'group', label: 'Users', detail: 'Accounts and roles' },
		{ href: '/settings/transfer', icon: 'sync_alt', label: 'Import and export', detail: 'Archives of projects and libraries' }
	];
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
		{@render children()}
	</div>
</div>
