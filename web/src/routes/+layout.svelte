<script lang="ts">
	import PersonName from '$lib/components/PersonName.svelte';
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto, invalidateAll } from '$app/navigation';
	import { NavigationRail, Snackbar, Icon, IconButton, Menu, MenuItem, Avatar } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { applySeed, getSeed, initTheme, toggleMode, isDark } from '$lib/m3/theme';
	import { api } from '$lib/api';
	import { snackbar } from '$lib/snackbar.svelte';
	import ConfirmHost from '$lib/components/ConfirmHost.svelte';
	import ImageViewer from '$lib/components/ImageViewer.svelte';
	import CommandPalette from '$lib/components/CommandPalette.svelte';
	import { palette } from '$lib/palette.svelte';
	import NotificationBell from '$lib/components/NotificationBell.svelte';
	import PhoneNav from '$lib/components/PhoneNav.svelte';
	import { currentProject } from '$lib/currentProject.svelte';
	import AccountDialog from '$lib/components/AccountDialog.svelte';
	import { notifications } from '$lib/notifications.svelte';

	let { data, children } = $props();
	let dark = $state(false);

	// This project (Home, Board, Assets follow the current project), then the shared library,
	// then the instance. Switching projects is in the project title and the account menu.
	let nav = $derived([
		{ href: currentProject.home, icon: 'space_dashboard', label: 'Home', exact: true },
		{ href: currentProject.board, icon: 'view_kanban', label: 'Board', exact: true },
		{ href: currentProject.assets, icon: 'radar', label: 'Assets', exact: true },
		{ href: '/playbooks', icon: 'account_tree', label: 'Playbooks', divider: true },
		{ href: '/library', icon: 'handyman', label: 'Library' },
		{ href: '/variables', icon: 'key', label: 'Variables' },
		{ href: '/parsers', icon: 'data_object', label: 'Parsers' },
		{ href: '/agents', icon: 'dns', label: 'Agents', divider: true },
		{ href: '/people', icon: 'groups', label: 'People' },
		...(data.user?.role === 'admin' ? [{ href: '/settings', icon: 'settings', label: 'Settings' }] : [])
	]);
	let accountOpen = $state(false);

	onMount(() => {
		initTheme();
		dark = isDark();
	});
	// Your account's theme colour wins over whatever this browser last showed.
	$effect(() => {
		const seed = data.user?.theme_seed;
		if (seed !== undefined && (seed || null) !== getSeed()) void applySeed(seed || null);
	});

	$effect.pre(() => {
		if (data.user) currentProject.init(data.user.id);
	});

	$effect(() => {
		if (data.user) {
			realtime.start();
			notifications.start(data.user.id);
		} else {
			realtime.stop();
			notifications.stop();
		}
	});

	// Back after a dropped connection (e.g. the server restarted): reload what the page
	// shows, since the events sent while it was away were missed.
	let seenReconnects = 0;
	$effect(() => {
		const n = realtime.reconnects;
		if (n > seenReconnects) {
			seenReconnects = n;
			void invalidateAll();
			void notifications.load();
		}
	});

	/** Ctrl+K / Cmd+K opens search from anywhere (and closes it again). */
	function shortcut(e: KeyboardEvent) {
		if (!data.user || e.key.toLowerCase() !== 'k' || !(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
		e.preventDefault();
		if (palette.open) palette.hide();
		else palette.show();
	}

	async function logout() {
		await api().auth.logout();
		realtime.stop();
		notifications.stop();
		await invalidateAll();
		await goto('/login');
	}
</script>

<svelte:head>
	<title>Castor</title>
</svelte:head>
<svelte:window onkeydown={shortcut} />

{#if data.user}
	<!-- Phones get a bottom bar (PhoneNav) instead of the rail. -->
	<div class="flex h-dvh w-full flex-col overflow-hidden bg-surface md:flex-row">
		<div class="hidden md:contents">
		<NavigationRail items={nav} current={page.url.pathname}>
			{#snippet top()}
				<a href={currentProject.home} class="flex size-12 items-center justify-center rounded-lg bg-primary-container text-on-primary-container" aria-label="Castor home">
					<Icon name="radar" filled />
				</a>
				<IconButton icon="search" label="Search everything (Ctrl+K)" class="mt-2" onclick={() => palette.show()} />
			{/snippet}
			{#snippet bottom()}
				<div class="flex flex-col items-center gap-2">
					{#if !realtime.connected}
						<span class="flex flex-col items-center text-error label-small" title="Live updates disconnected; reconnecting">
							<Icon name="cloud_off" size={20} />
						</span>
					{/if}
					<NotificationBell />
					<IconButton
						icon={dark ? 'light_mode' : 'dark_mode'}
						label="Toggle theme"
						onclick={() => {
							toggleMode();
							dark = isDark();
						}}
					/>
					<Menu side>
						{#snippet anchor()}
							<button type="button" class="focus-ring rounded-full" aria-label="Account menu">
								<Avatar name={data.user?.display_name ?? ''} src={data.user?.avatar_url} size={40} />
							</button>
						{/snippet}
						<div class="px-3 py-2">
							<p class="title-small text-on-surface"><PersonName name={data.user?.display_name} /></p>
							<p class="body-small text-on-surface-variant">@{data.user?.username} · {data.user?.role}</p>
						</div>
						<MenuItem icon="manage_accounts" onclick={() => (accountOpen = true)}>Account and password</MenuItem>
						<MenuItem icon="swap_horiz" onclick={() => goto('/projects')}>Switch project</MenuItem>
						<MenuItem icon="key" onclick={() => goto('/tokens')}>API tokens</MenuItem>
						<MenuItem icon="logout" onclick={logout}>Sign out</MenuItem>
					</Menu>
				</div>
			{/snippet}
		</NavigationRail>
		</div>
		<main class="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
			{@render children()}
		</main>
		<PhoneNav
			items={nav}
			current={page.url.pathname}
			user={data.user}
			{dark}
			onaccount={() => (accountOpen = true)}
			ontheme={() => {
				toggleMode();
				dark = isDark();
			}}
			onlogout={logout}
		/>
	</div>
{:else}
	{@render children()}
{/if}
{#if data.user}<AccountDialog bind:open={accountOpen} user={data.user} />{/if}
<Snackbar />
<ConfirmHost />
<ImageViewer />
{#if data.user}<CommandPalette />{/if}
