<script lang="ts">
	import type { DirectoryEntry, Plugin, PluginLibraryKind, PluginMode } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Button, Card, Icon, IconButton, Menu, MenuItem, Switch, Tabs, TopAppBar } from '$lib/m3';
	import { appSettings } from '$lib/appSettings.svelte';
	import PluginDirectory from '$lib/components/PluginDirectory.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime } from '$lib/format';
	import { EVENT_WORDS } from '$lib/plugins.svelte';
	import PluginDialog from '$lib/components/PluginDialog.svelte';
	import PluginInstallDialog from '$lib/components/PluginInstallDialog.svelte';
	import PluginIcon from '$lib/components/PluginIcon.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let list = $state<Plugin[]>(data.plugins);
	// svelte-ignore state_referenced_locally
	let loaded = data.plugins;
	$effect(() => {
		if (data.plugins !== loaded) {
			loaded = data.plugins;
			list = data.plugins;
		}
	});
	// svelte-ignore state_referenced_locally
	let mode = $state<PluginMode>(data.mode);
	async function reload() {
		try {
			[list, mode] = await Promise.all([api().plugins.list(), api().plugins.mode()]);
		} catch {
			/* keep what's shown */
		}
	}
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name === 'plugins.changed') {
				clearTimeout(timer);
				timer = setTimeout(() => void reload(), 300);
			}
		})
	);

	let installOpen = $state(false);
	/** Installing (or updating) from the directory: its id. */
	let fromDirectory = $state<string | null>(null);
	let directoryVersion = $state(0);
	const installFromDirectory = (id: string) => {
		fromDirectory = id;
		installOpen = true;
	};
	const installFile = () => {
		fromDirectory = null;
		installOpen = true;
	};

	let tab = $derived(page.url.searchParams.get('tab') === 'browse' ? 'browse' : 'installed');
	const setTab = (id: string) => goto(id === 'browse' ? '?tab=browse' : '?', { replaceState: true, noScroll: true, keepFocus: true });
	let updates = $derived(list.filter((p) => p.latest_version));

	$effect(() => void appSettings.load());
	let checking = $state(false);
	async function checkUpdates() {
		checking = true;
		try {
			const r = await api().plugins.checkUpdates();
			snackbar.show(r.updates.length ? `${r.updates.length} update${r.updates.length === 1 ? '' : 's'}: ${r.updates.map((u) => `${u.name} ${u.to}`).join(', ')}` : 'Every plugin is up to date');
			void reload();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not check for updates');
		} finally {
			checking = false;
		}
	}
	async function setUpdateChecks(on: boolean) {
		try {
			appSettings.value = await api().settings.update({ plugin_update_checks: on });
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not change it');
		}
	}
	const KIND_WORDS: Record<PluginLibraryKind, [string, string]> = {
		commands: ['command', 'commands'], tools: ['tool', 'tools'], parsers: ['parser', 'parsers'],
		playbooks: ['playbook', 'playbooks'], themes: ['theme', 'themes'], variable_kinds: ['variable kind', 'variable kinds']
	};
	const libraryWords = (p: Plugin) =>
		(Object.keys(KIND_WORDS) as PluginLibraryKind[])
			.map((k) => [k, p.library.filter((i) => i.kind === k).length] as const)
			.filter(([, n]) => n)
			.map(([k, n]) => `${n} ${KIND_WORDS[k][n === 1 ? 0 : 1]}`)
			.join(' · ');
	let configOpen = $state(false);
	let configuring = $state<Plugin | null>(null);
	const configure = (p: Plugin) => {
		configuring = p;
		configOpen = true;
	};
	const put = (p: Plugin) => {
		list = [...list.filter((x) => x.id !== p.id), p].sort((a, b) => a.name.localeCompare(b.name));
	};

	async function setRestricted(on: boolean) {
		if (!on) {
			const ok = await confirmAction({
				title: 'Allow unrestricted plugins?',
				message:
					'An unrestricted plugin runs with full access to this server: its files (Castor’s configuration, the database password, the vault key), its programs and any network, and it can put script in Castor’s own page, acting as whoever is looking. Castor can’t limit or watch what it does. Turn this off only to install a plugin you trust as much as Castor itself.',
				confirmLabel: 'Allow them'
			});
			if (!ok) {
				mode = { ...mode }; // the switch goes back
				return;
			}
		}
		try {
			mode = await api().plugins.setMode(on);
			snackbar.show(on ? 'Restricted mode is on: unrestricted plugins are stopped' : 'Unrestricted plugins can be installed');
			void reload();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not change it');
		}
	}

	async function setEnabled(p: Plugin, on: boolean) {
		try {
			put(await api().plugins.update(p.id, { enabled: on }));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not change it');
		}
	}
	async function restart(p: Plugin) {
		try {
			put(await api().plugins.restart(p.id));
			snackbar.show(`${p.name} is running`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : `${p.name} didn't start`);
			void reload();
		}
	}
	async function remove(p: Plugin) {
		const ok = await confirmAction({
			title: `Remove ${p.name}?`,
			message: 'It stops, and its files, settings, storage, runs and log go. What it recorded in projects (assets, comments…) stays.',
			confirmLabel: 'Remove'
		});
		if (!ok) return;
		try {
			await api().plugins.remove(p.id);
			list = list.filter((x) => x.id !== p.id);
			snackbar.show(`Removed ${p.name}`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove it');
		}
	}

	const STATUS: Record<Plugin['status'], { label: string; tone: string; icon: string }> = {
		running: { label: 'Running', tone: 'bg-primary-container text-on-primary-container', icon: 'play_circle' },
		starting: { label: 'Starting', tone: 'bg-surface-container-highest text-on-surface-variant', icon: 'progress_activity' },
		stopped: { label: 'Stopped', tone: 'bg-surface-container-highest text-on-surface-variant', icon: 'pause_circle' },
		crashed: { label: 'Crashed', tone: 'bg-error-container text-on-error-container', icon: 'error' },
		error: { label: "Won't start", tone: 'bg-error-container text-on-error-container', icon: 'error' },
		library: { label: 'Library only', tone: 'bg-secondary-container text-on-secondary-container', icon: 'library_books' }
	};
	const where = (p: Plugin) =>
		p.all_projects
			? 'every project'
			: p.project_ids.length
				? data.projects.filter((x) => p.project_ids.includes(x.id)).map((x) => x.codename).join(', ') || `${p.project_ids.length} projects`
				: 'no project yet';
</script>

<svelte:head><title>Plugins · Settings · Castor</title></svelte:head>

<TopAppBar title="Plugins" subtitle="What Castor can do, extended">
	{#snippet actions()}
		<Button icon="upload_file" variant={tab === 'browse' ? 'outlined' : 'filled'} onclick={installFile}>Install from a file</Button>
	{/snippet}
</TopAppBar>
<div class="px-6"><Tabs tabs={[{ id: 'installed', label: 'Installed', icon: 'extension', badge: list.length }, { id: 'browse', label: 'Browse', icon: 'storefront' }]} value={tab} onchange={setTab} /></div>

<div class="flex-1 overflow-auto px-6 pb-10">
	{#if tab === 'browse'}
	<div class="max-w-4xl pt-3">
		<PluginDirectory version={directoryVersion} oninstall={(e: DirectoryEntry) => installFromDirectory(e.id)} />
	</div>
	{:else}
	<div class="flex max-w-4xl flex-col gap-3 pt-3">
		<p class="max-w-[70ch] body-small text-on-surface-variant">
			Plugins add actions to projects, cards and assets: importing another tool's results, enriching what Castor found. Each runs sandboxed, in its own
			process with no access to this server, and acts in Castor only as its own account with the permissions you approved, in the projects you choose. See
			<a class="text-primary underline" href="https://github.com/emilyastranova/castor/wiki/Plugins" target="_blank" rel="noopener">Plugins</a> in the wiki to write one.
		</p>
		{#if mode.safe_mode}
			<div class="flex items-start gap-3 rounded-lg bg-tertiary-container px-4 py-3 text-on-tertiary-container" role="status">
				<Icon name="health_and_safety" class="mt-0.5 shrink-0" />
				<p class="body-medium">
					<b class="font-medium">Safe mode.</b> Castor started with every plugin stopped (<code class="font-mono">CASTOR_PLUGINS_SAFE_MODE</code>), so you can remove or
					turn off one that misbehaves. Nothing here runs until the server starts without it.
				</p>
			</div>
		{/if}
		<Card variant="outlined" class="flex items-start gap-3 px-4 py-3.5 {mode.restricted ? '' : 'border-error!'}">
			<Icon name={mode.restricted ? 'shield' : 'gpp_maybe'} class="mt-0.5 shrink-0 {mode.restricted ? 'text-primary' : 'text-error'}" />
			<div class="min-w-0 flex-1">
				<p class="title-small text-on-surface">Restricted mode</p>
				<p class="mt-0.5 body-small text-on-surface-variant">
					{#if mode.restricted}
						Only sandboxed plugins run. Unrestricted ones, which get full access to this server, can't be installed or turned on{mode.restricted_locked
							? ': this server keeps it on (CASTOR_PLUGINS_RESTRICTED)'
							: ''}.
					{:else}
						<span class="text-error">Off: unrestricted plugins can be installed, with full access to this server and script in Castor’s own page.</span> Turning it back on stops
						them.
					{/if}
				</p>
			</div>
			<Switch checked={mode.restricted} label="On" disabled={mode.restricted_locked} onchange={(on) => setRestricted(on)} />
		</Card>
		<Card variant="outlined" class="flex flex-wrap items-start gap-3 px-4 py-3.5">
			<Icon name="update" class="mt-0.5 shrink-0 text-primary" />
			<div class="min-w-48 flex-1">
				<p class="title-small text-on-surface">Updates</p>
				<p class="mt-0.5 body-small text-on-surface-variant">
					{#if updates.length}
						<span class="text-on-surface">{updates.length === 1 ? 'An update is' : `${updates.length} updates are`} ready: {updates.map((p) => `${p.name} ${p.latest_version}`).join(', ')}.</span>
					{:else}
						Castor looks in the plugin directory daily for newer releases and tells admins. Nothing updates by itself: you review each one first.
					{/if}
				</p>
			</div>
			<Button variant="text" icon="refresh" loading={checking} onclick={checkUpdates}>Check now</Button>
			<Switch checked={appSettings.value.plugin_update_checks !== false} label="Daily" onchange={(on) => setUpdateChecks(on)} />
		</Card>
		{#each list as p (p.id)}
			{@const st = STATUS[p.status] ?? STATUS.stopped}
			<Card variant="outlined" class="flex flex-col gap-3 px-4 py-3.5">
				<div class="flex items-start gap-3">
					<PluginIcon src={p.icon_url} />
					<div class="min-w-0 flex-1">
						<p class="flex flex-wrap items-center gap-x-2 gap-y-1 title-medium text-on-surface">
							{p.name}
							<span class="body-medium text-on-surface-variant">{p.version}</span>
							<span class="flex items-center gap-1 rounded-full px-2 py-0.5 label-medium {st.tone}"><Icon name={st.icon} size={14} />{st.label}</span>
							{#if p.runtime === 'unrestricted'}
								<span class="flex items-center gap-1 rounded-full bg-error px-2 py-0.5 label-medium text-on-error" title="It runs with full access to this server: files, programs, any network. Castor can't limit what it does.">
									<Icon name="gpp_maybe" size={14} />Unrestricted
								</span>
							{:else if p.has_code}
								<span class="flex items-center gap-1 rounded-full border border-outline-variant px-2 py-0.5 label-medium text-on-surface-variant" title={p.granted.network?.length ? `It runs with no access to this server's files or programs; its network is only ${p.granted.network.join(', ')}` : "It runs with no access to this server's files, network or programs"}>
									<Icon name="shield" size={14} />Sandboxed
								</span>
							{/if}
						</p>
						<p class="mt-0.5 body-small text-on-surface-variant">
							{#if p.source.startsWith('directory:')}<Icon name="storefront" size={14} class="mr-0.5 align-[-2px]" />{/if}{p.publisher ? `by ${p.publisher} · ` : ''}<code class="font-mono">{p.id}</code>{p.has_code ? ` · in ${where(p)}` : ''} · installed {relativeTime(p.installed_at)}{p.installed_by ? ` by ${p.installed_by}` : ''}
						</p>
						{#if p.description}<p class="mt-1 body-medium text-on-surface">{p.description}</p>{/if}
						{#if p.latest_version}
							<div class="mt-2 flex flex-wrap items-center gap-2 rounded-md bg-tertiary-container/60 px-3 py-1.5 body-small text-on-surface">
								<Icon name="upgrade" size={16} class="text-tertiary" />
								<span class="flex-1">{p.latest_version} is available in the plugin directory.</span>
								<Button variant="text" onclick={() => installFromDirectory(p.id)}>Review the update</Button>
							</div>
						{/if}
					</div>
					{#if p.has_code}<Switch checked={p.enabled} label="On" disabled={!p.enabled && p.runtime === 'unrestricted' && mode.restricted} onchange={(on) => setEnabled(p, on)} />{/if}
					<Menu>
						{#snippet anchor()}<IconButton icon="more_vert" label="More for {p.name}" />{/snippet}
						{#if p.has_code}<MenuItem icon="restart_alt" onclick={() => restart(p)}>Start again</MenuItem>{/if}
						{#if p.source.startsWith('directory:')}<MenuItem icon="storefront" onclick={() => goto(`/settings/plugins/directory/${p.id}`)}>In the directory</MenuItem>{/if}
						{#if p.homepage}<MenuItem icon="open_in_new" onclick={() => window.open(p.homepage, '_blank', 'noopener')}>Homepage</MenuItem>{/if}
						<MenuItem icon="delete" danger onclick={() => remove(p)}>Remove</MenuItem>
					</Menu>
				</div>

				{#if p.per_user.length}
					<p class="flex items-center gap-2 body-small text-on-surface-variant">
						<Icon name="person" size={16} />
						Each person sets their own {p.settings_spec.filter((s) => p.per_user.includes(s.name)).map((s) => s.label).join(', ')}
						· {p.people_set ? `${p.people_set} ${p.people_set === 1 ? 'has' : 'have'}` : 'nobody has yet'}
					</p>
				{/if}
				{#if p.runtime === 'unrestricted' && mode.restricted}
					<div class="flex items-start gap-2 rounded-md bg-surface-container-high px-3 py-2 body-small text-on-surface-variant">
						<Icon name="block" size={16} class="mt-px shrink-0" />
						<span>Stopped: restricted mode is on, and this plugin is unrestricted.</span>
					</div>
				{:else if p.status === 'error' || p.status === 'crashed'}
					<div class="flex items-start gap-2 rounded-md bg-error-container/60 px-3 py-2 body-small text-on-error-container" role="alert">
						<Icon name="error" size={16} class="mt-px shrink-0" />
						<span class="min-w-0 flex-1 break-words">{p.status_message || 'It stopped.'}</span>
						<Button variant="text" onclick={() => restart(p)}>Start again</Button>
					</div>
				{:else if p.missing_settings.length}
					<div class="flex items-center gap-2 rounded-md bg-tertiary-container/50 px-3 py-2 body-small text-on-surface">
						<Icon name="tune" size={16} class="text-tertiary" />
						<span class="flex-1">It needs its settings before it can run: {p.missing_settings.join(', ')}</span>
						<Button variant="text" onclick={() => configure(p)}>Settings</Button>
					</div>
				{/if}

				<div class="flex flex-wrap gap-x-6 gap-y-2 body-small">
					{#if p.has_code}
						<div class="min-w-48 flex-1">
							<p class="mb-1 label-medium text-on-surface-variant">Can</p>
							<p class="text-on-surface">{p.permission_details.map((s) => s.label).join(' · ') || 'Nothing in Castor'}</p>
						</div>
					{/if}
					{#if p.granted.network?.length}
						<div class="min-w-48 flex-1">
							<p class="mb-1 label-medium text-on-surface-variant">Connects to</p>
							<p class="font-mono text-[13px] text-on-surface">{p.granted.network.join(', ')}</p>
						</div>
					{/if}
					{#if p.granted.events?.length}
						<div class="min-w-48 flex-1">
							<p class="mb-1 label-medium text-on-surface-variant">Told when</p>
							<p class="text-on-surface">{p.granted.events.map((e) => EVENT_WORDS[e] ?? e).join(' · ')}</p>
						</div>
					{/if}
					{#if p.library.length}
						<div class="min-w-48 flex-1">
							<p class="mb-1 label-medium text-on-surface-variant">In the library</p>
							<p class="text-on-surface" title={p.library.map((i) => i.name).join(', ')}>{libraryWords(p)}</p>
						</div>
					{/if}
					{#if p.actions.length}
						<div class="min-w-48 flex-1">
							<p class="mb-1 label-medium text-on-surface-variant">Adds</p>
							<p class="flex flex-wrap gap-x-3 gap-y-1 text-on-surface">
								{#each p.actions as a (a.id)}<span class="flex items-center gap-1"><Icon name={a.icon} size={16} class="text-on-surface-variant" />{a.label}</span>{/each}
							</p>
						</div>
					{/if}
				</div>
				<div class="-mb-1 flex justify-end">
					{#if p.has_code}
						<Button variant="text" icon="tune" onclick={() => configure(p)}>Settings, runs and log</Button>
					{:else}
						<Button variant="text" icon="library_books" href="/library">Open the library</Button>
					{/if}
				</div>
			</Card>
		{:else}
			<div class="flex flex-col items-center gap-2 rounded-lg border border-dashed border-outline-variant px-6 py-10 text-center">
				<Icon name="extension" size={32} class="text-on-surface-variant" />
				<p class="title-small text-on-surface">No plugins yet</p>
				<p class="max-w-md body-medium text-on-surface-variant">Find one in the plugin directory, or install one from its .zip or a link to it.</p>
				<div class="flex gap-2">
					<Button icon="storefront" onclick={() => setTab('browse')}>Browse plugins</Button>
					<Button variant="outlined" icon="upload_file" onclick={installFile}>Install from a file</Button>
				</div>
			</div>
		{/each}
	</div>
	{/if}
</div>

<PluginInstallDialog bind:open={installOpen} projects={data.projects} directoryId={fromDirectory} oninstalled={(p) => { const was = list.find((x) => x.id === p.id); put(p); directoryVersion++; snackbar.show(`${was ? 'Updated' : 'Installed'} ${p.name} ${p.version}`); }} />
<PluginDialog bind:open={configOpen} plugin={configuring} projects={data.projects} onchanged={(p) => { put(p); snackbar.show(`Saved ${p.name}`); }} />
