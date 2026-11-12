<script lang="ts">
	import type { Plugin } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon, IconButton, Menu, MenuItem, Switch, TopAppBar } from '$lib/m3';
	import { confirmAction } from '$lib/confirm.svelte';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime } from '$lib/format';
	import PluginDialog from '$lib/components/PluginDialog.svelte';
	import PluginInstallDialog from '$lib/components/PluginInstallDialog.svelte';

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
	async function reload() {
		try {
			list = await api().plugins.list();
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
	let configOpen = $state(false);
	let configuring = $state<Plugin | null>(null);
	const configure = (p: Plugin) => {
		configuring = p;
		configOpen = true;
	};
	const put = (p: Plugin) => {
		list = [...list.filter((x) => x.id !== p.id), p].sort((a, b) => a.name.localeCompare(b.name));
	};

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
		error: { label: "Won't start", tone: 'bg-error-container text-on-error-container', icon: 'error' }
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
		<Button icon="add" onclick={() => (installOpen = true)}>Install plugin</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-3 pt-2">
		<p class="max-w-[70ch] body-small text-on-surface-variant">
			Plugins add actions to projects, cards and assets: importing another tool's results, enriching what Castor found. Each runs sandboxed, in its own
			process with no access to this server, and acts in Castor only as its own account with the permissions you approved, in the projects you choose. See
			<a class="text-primary underline" href="https://github.com/emilyastranova/castor/wiki/Plugins" target="_blank" rel="noopener">Plugins</a> in the wiki to write one.
		</p>
		{#each list as p (p.id)}
			{@const st = STATUS[p.status] ?? STATUS.stopped}
			<Card variant="outlined" class="flex flex-col gap-3 px-4 py-3.5">
				<div class="flex items-start gap-3">
					<span class="grid size-10 shrink-0 place-items-center rounded-md bg-secondary-container text-on-secondary-container"><Icon name="extension" /></span>
					<div class="min-w-0 flex-1">
						<p class="flex flex-wrap items-center gap-x-2 gap-y-1 title-medium text-on-surface">
							{p.name}
							<span class="body-medium text-on-surface-variant">{p.version}</span>
							<span class="flex items-center gap-1 rounded-full px-2 py-0.5 label-medium {st.tone}"><Icon name={st.icon} size={14} />{st.label}</span>
							<span class="flex items-center gap-1 rounded-full border border-outline-variant px-2 py-0.5 label-medium text-on-surface-variant" title="It runs with no access to this server's files, network or programs">
								<Icon name="shield" size={14} />Sandboxed
							</span>
						</p>
						<p class="mt-0.5 body-small text-on-surface-variant">
							{p.publisher ? `by ${p.publisher} · ` : ''}<code class="font-mono">{p.id}</code> · in {where(p)} · installed {relativeTime(p.installed_at)}{p.installed_by ? ` by ${p.installed_by}` : ''}
						</p>
						{#if p.description}<p class="mt-1 body-medium text-on-surface">{p.description}</p>{/if}
					</div>
					<Switch checked={p.enabled} label="On" onchange={(on) => setEnabled(p, on)} />
					<Menu>
						{#snippet anchor()}<IconButton icon="more_vert" label="More for {p.name}" />{/snippet}
						<MenuItem icon="restart_alt" onclick={() => restart(p)}>Start again</MenuItem>
						{#if p.homepage}<MenuItem icon="open_in_new" onclick={() => window.open(p.homepage, '_blank', 'noopener')}>Homepage</MenuItem>{/if}
						<MenuItem icon="delete" danger onclick={() => remove(p)}>Remove</MenuItem>
					</Menu>
				</div>

				{#if p.status === 'error' || p.status === 'crashed'}
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
					<div class="min-w-48 flex-1">
						<p class="mb-1 label-medium text-on-surface-variant">Can</p>
						<p class="text-on-surface">{p.permission_details.map((s) => s.label).join(' · ') || 'Nothing in Castor'}</p>
					</div>
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
					<Button variant="text" icon="tune" onclick={() => configure(p)}>Settings, runs and log</Button>
				</div>
			</Card>
		{:else}
			<div class="flex flex-col items-center gap-2 rounded-lg border border-dashed border-outline-variant px-6 py-10 text-center">
				<Icon name="extension" size={32} class="text-on-surface-variant" />
				<p class="title-small text-on-surface">No plugins yet</p>
				<p class="max-w-md body-medium text-on-surface-variant">Install one from its .zip or a link to it. Castor's own, Nessus import, is in the repository's <code class="font-mono">plugins/</code> folder.</p>
				<Button icon="add" onclick={() => (installOpen = true)}>Install plugin</Button>
			</div>
		{/each}
	</div>
</div>

<PluginInstallDialog bind:open={installOpen} projects={data.projects} oninstalled={(p) => { put(p); snackbar.show(`Installed ${p.name}`); }} />
<PluginDialog bind:open={configOpen} plugin={configuring} projects={data.projects} onchanged={(p) => { put(p); snackbar.show(`Saved ${p.name}`); }} />
