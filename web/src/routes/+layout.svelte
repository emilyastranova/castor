<script lang="ts">
	import PersonName from '$lib/components/PersonName.svelte';
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { afterNavigate, beforeNavigate, goto, invalidateAll } from '$app/navigation';
	import { loadPluginScripts, navigated } from '$lib/pluginScripts';
	import { NavigationRail, Snackbar, Icon, IconButton, Menu, MenuItem, Avatar } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { applySeed, applyTheme, currentTheme, getSeed, initTheme, toggleMode, isDark } from '$lib/m3/theme';
	import { themes } from '$lib/themes.svelte';
	import { api } from '$lib/api';
	import { snackbar } from '$lib/snackbar.svelte';
	import ConfirmHost from '$lib/components/ConfirmHost.svelte';
	import ImageViewer from '$lib/components/ImageViewer.svelte';
	import CommandPalette from '$lib/components/CommandPalette.svelte';
	import SnapshotDialog from '$lib/components/SnapshotDialog.svelte';
	import { snapshots } from '$lib/snapshots.svelte';
	import { palette } from '$lib/palette.svelte';
	import NotificationBell from '$lib/components/NotificationBell.svelte';
	import PhoneNav from '$lib/components/PhoneNav.svelte';
	import { currentProject } from '$lib/currentProject.svelte';
	import AccountDialog from '$lib/components/AccountDialog.svelte';
	import { notifications } from '$lib/notifications.svelte';
	import { version } from '$lib/version.svelte';
	import { updates } from '$lib/updates.svelte';
	import UpdateBanner from '$lib/components/UpdateBanner.svelte';
	import { revealSpoilers } from '$lib/spoilers';
	import { brand } from '$lib/brand.svelte';
	import type { Branding } from '$lib/api';
	import BrandLogo from '$lib/components/BrandLogo.svelte';

	let { data, children } = $props();
	let dark = $state(false);

	// This project (Home, Board, Assets, Files follow the current project), then the shared library,
	// then the instance. Switching projects is in the project title and the account menu.
	let nav = $derived([
		{ href: currentProject.home, icon: 'space_dashboard', label: 'Home', exact: true },
		{ href: currentProject.board, icon: 'view_kanban', label: 'Board', exact: true },
		{ href: currentProject.assets, icon: 'radar', label: 'Assets', exact: true },
		{ href: currentProject.files, icon: 'folder_open', label: 'Files', exact: true },
		{ href: '/playbooks', icon: 'account_tree', label: 'Playbooks', divider: true },
		{ href: '/library', icon: 'handyman', label: 'Library' },
		{ href: '/variables', icon: 'key', label: 'Variables' },
		{ href: '/parsers', icon: 'data_object', label: 'Parsers' },
		{ href: '/snapshot', icon: 'photo_camera', label: 'Snapshot' },
		{ href: '/agents', icon: 'dns', label: 'Agents', divider: true },
		{ href: '/routes', icon: 'alt_route', label: 'Routes' },
		{ href: '/people', icon: 'groups', label: 'People' },
		...(data.user?.role === 'admin' ? [{ href: '/settings', icon: 'settings', label: 'Settings' }] : [])
	]);
	let accountOpen = $state(false);

	onMount(() => {
		initTheme();
		dark = isDark();
		return revealSpoilers(); // ||spoilers|| anywhere on the page show on a click
	});
	// Your account's theme colour wins over whatever this browser last showed.
	$effect(() => {
		const seed = data.user?.theme_seed;
		if (seed !== undefined && (seed || null) !== getSeed()) void applySeed(seed || null);
	});
	// …and so does its theme. A theme with only light or only dark has no light/dark switch.
	$effect(() => {
		if (!data.user) return;
		void themes.load();
		const theme = themes.effective(data.user.theme);
		if (theme && (theme.id !== currentTheme().id || theme.css !== currentTheme().css)) applyTheme(theme);
		dark = isDark();
	});
	// The company's name and logos: set before the page renders (titles on the server too), and
	// again when an admin changes them.
	// svelte-ignore state_referenced_locally
	brand.info = data.branding ?? null;
	$effect.pre(() => {
		brand.info = data.branding ?? null;
	});
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name === 'branding.changed') brand.info = e.data as Branding;
		})
	);
	// An admin changed a theme: load them again (the effect above re-applies yours).
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name === 'themes.changed') void themes.load(true);
		})
	);
	let oneMode = $derived((themes.effective(data.user?.theme)?.modes ?? currentTheme().modes) !== 'both');

	$effect.pre(() => {
		if (data.user) currentProject.init(data.user.id);
	});

	// Unrestricted plugins' scripts (page.script), if an admin installed any.
	$effect(() => {
		if (data.user) void loadPluginScripts(data.user);
	});
	afterNavigate(() => navigated(new URL(page.url)));

	// A newer Castor is running than this page: watch for it, and make the next move to another
	// page a full load so it picks the new version up (staying on a page, e.g. opening a card or
	// typing a filter that goes into the address, doesn't reload it).
	onMount(() => updates.start());
	beforeNavigate(({ from, to, willUnload }) => {
		if (updates.available && !willUnload && to?.url && to.url.origin === location.origin && from?.route.id !== to.route.id) {
			location.href = to.url.href;
		}
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
			void updates.check(); // an upgrade restarts the server
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
		// Not straight back to single sign-on (it would sign you in again): the page says you're out.
		await goto('/login?signed_out');
	}

	$effect(() => {
		if (data.user) version.load();
	});
</script>

<svelte:head>
	<title>{brand.name}</title>
</svelte:head>
<svelte:window onkeydown={shortcut} />

{#if data.user}
	<!-- Phones get a bottom bar (PhoneNav) instead of the rail. -->
	<div class="app-shell flex h-dvh w-full flex-col overflow-hidden bg-surface md:flex-row">
		<div class="hidden md:contents">
		<NavigationRail items={nav} current={page.url.pathname}>
			{#snippet top()}
				{#if brand.info?.mark_url || brand.info?.mark_dark_url}
					<!-- The company's square logo (Settings → Branding) in place of Castor's. -->
					<a href={currentProject.home} class="flex size-12 items-center justify-center rounded-lg" aria-label="{brand.name} home" title={brand.name}>
						<BrandLogo kind="mark" class="size-11 rounded-md" />
					</a>
				{:else}
					<a href={currentProject.home} class="flex size-12 items-center justify-center rounded-lg bg-primary-container text-on-primary-container" aria-label="{brand.name} home" title={brand.name}>
						<Icon name="radar" filled />
					</a>
				{/if}
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
					{#if !oneMode}<IconButton
						icon={dark ? 'light_mode' : 'dark_mode'}
						label="Toggle theme"
						onclick={() => {
							toggleMode();
							dark = isDark();
						}}
					/>{/if}
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
						<MenuItem icon="extension" onclick={() => goto('/plugins')}>Your plugin settings</MenuItem>
						<MenuItem icon="logout" onclick={logout}>Sign out</MenuItem>
						<p class="border-t border-outline-variant/60 px-3 pt-2 pb-1 body-small text-on-surface-variant" title={version.mismatch ? 'The web app and the server are on different versions: one has been upgraded and the other not yet' : undefined}>{version.label}</p>
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
			ontheme={oneMode
				? undefined
				: () => {
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
<UpdateBanner />
<ConfirmHost />
<ImageViewer />
{#if data.user}<CommandPalette />{/if}
<!-- A picture of a code block (its camera button), from anywhere. -->
{#if snapshots.request}
	{#key snapshots.request}
		<SnapshotDialog
			bind:open={() => !!snapshots.request, (v) => !v && snapshots.close()}
			text={snapshots.request.text}
			language={snapshots.request.language}
			title={snapshots.request.title ?? ''}
		/>
	{/key}
{/if}
