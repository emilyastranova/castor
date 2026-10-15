<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto, invalidateAll } from '$app/navigation';
	import { NavigationRail, Snackbar, Icon, IconButton, Menu, MenuItem, Avatar } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { initTheme, toggleMode, isDark } from '$lib/m3/theme';
	import { api } from '$lib/api';
	import { snackbar } from '$lib/snackbar.svelte';

	let { data, children } = $props();
	let dark = $state(false);

	const nav = [
		{ href: '/projects', icon: 'view_kanban', label: 'Projects' },
		{ href: '/agents', icon: 'dns', label: 'Agents' },
		{ href: '/activity', icon: 'history', label: 'Activity' }
	];

	onMount(() => {
		initTheme();
		dark = isDark();
	});

	$effect(() => {
		if (data.user) realtime.start();
		else realtime.stop();
	});

	async function logout() {
		await api().auth.logout();
		realtime.stop();
		await invalidateAll();
		await goto('/login');
	}
</script>

<svelte:head>
	<title>Castor</title>
</svelte:head>

{#if data.user}
	<div class="flex h-dvh w-full overflow-hidden bg-surface">
		<NavigationRail items={nav} current={page.url.pathname}>
			{#snippet top()}
				<a href="/projects" class="flex size-12 items-center justify-center rounded-lg bg-primary-container text-on-primary-container" aria-label="Castor home">
					<Icon name="radar" filled />
				</a>
			{/snippet}
			{#snippet bottom()}
				<div class="flex flex-col items-center gap-2">
					{#if !realtime.connected}
						<span class="flex flex-col items-center text-error label-small" title="Live updates disconnected; reconnecting">
							<Icon name="cloud_off" size={20} />
						</span>
					{/if}
					<IconButton
						icon={dark ? 'light_mode' : 'dark_mode'}
						label="Toggle theme"
						onclick={() => {
							toggleMode();
							dark = isDark();
						}}
					/>
					<Menu align="left">
						{#snippet anchor()}
							<button type="button" class="focus-ring rounded-full" aria-label="Account menu">
								<Avatar name={data.user?.display_name ?? ''} size={40} />
							</button>
						{/snippet}
						<div class="px-3 py-2">
							<p class="title-small text-on-surface">{data.user?.display_name}</p>
							<p class="body-small text-on-surface-variant">@{data.user?.username}</p>
						</div>
						<MenuItem icon="logout" onclick={logout}>Sign out</MenuItem>
					</Menu>
				</div>
			{/snippet}
		</NavigationRail>
		<main class="flex min-w-0 flex-1 flex-col overflow-hidden">
			{@render children()}
		</main>
	</div>
{:else}
	{@render children()}
{/if}
<Snackbar />
