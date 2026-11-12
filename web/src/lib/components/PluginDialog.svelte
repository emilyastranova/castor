<script lang="ts">
	import type { Plugin, PluginLogEntry, PluginRun, ProjectSummary, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, IconButton, Tabs } from '$lib/m3';
	import { absoluteTime, relativeTime } from '$lib/format';
	import PluginProjects from './PluginProjects.svelte';
	import PluginSettingsForm from './PluginSettingsForm.svelte';

	/** One installed plugin: its settings, projects, recent runs and log. */
	interface Props {
		open: boolean;
		plugin: Plugin | null;
		projects: ProjectSummary[];
		onchanged: (p: Plugin) => void;
	}
	let { open = $bindable(false), plugin, projects, onchanged }: Props = $props();

	let tab = $state<'settings' | 'runs' | 'log'>('settings');
	let values = $state<Record<string, unknown>>({});
	let secrets = $state<Record<string, string>>({});
	let projectIds = $state<UUID[]>([]);
	let allProjects = $state(false);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let runs = $state<PluginRun[]>([]);
	let logs = $state<PluginLogEntry[]>([]);

	let openedFor: string | null = null;
	$effect(() => {
		const id = open ? (plugin?.id ?? null) : null;
		if (id === openedFor) return;
		openedFor = id;
		if (!plugin || !open) return;
		tab = plugin.missing_settings.length || plugin.status !== 'error' ? 'settings' : 'log';
		values = { ...plugin.settings };
		secrets = {};
		projectIds = [...plugin.project_ids];
		allProjects = plugin.all_projects;
		error = null;
		void loadMore(plugin.id);
	});
	async function loadMore(id: string) {
		[runs, logs] = await Promise.all([api().plugins.runs(id).catch(() => []), api().plugins.logs(id).catch(() => [])]);
	}

	async function save() {
		if (!plugin) return;
		saving = true;
		error = null;
		try {
			const p = await api().plugins.update(plugin.id, {
				settings: values,
				secrets, // typed ones only: a value replaces the stored secret, cleared to nothing removes it
				project_ids: projectIds,
				all_projects: allProjects
			});
			onchanged(p);
			open = false;
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}
	const RUN: Record<string, string> = { running: 'text-primary', succeeded: 'text-primary', failed: 'text-error' };
	const LEVEL: Record<string, string> = { error: 'text-error', warn: 'text-tertiary', info: 'text-on-surface', debug: 'text-on-surface-variant' };
</script>

<Dialog bind:open title={plugin ? `${plugin.name} ${plugin.version}` : 'Plugin'} icon="extension" size="md">
	{#if plugin}
		<div class="flex flex-col gap-4 pt-1 pb-2">
			<Tabs
				tabs={[
					{ id: 'settings', label: 'Settings', icon: 'tune' },
					{ id: 'runs', label: 'Runs', icon: 'history', badge: runs.filter((r) => r.status === 'running').length || undefined },
					{ id: 'log', label: 'Log', icon: 'receipt_long' }
				]}
				bind:value={tab}
			/>
			{#if tab === 'settings'}
				{#if plugin.settings_spec.length}
					<section aria-label="Settings">
						<PluginSettingsForm spec={plugin.settings_spec} bind:values bind:secrets secretsSet={plugin.secrets_set} />
					</section>
				{:else}
					<p class="body-small text-on-surface-variant italic">It has no settings.</p>
				{/if}
				<section aria-label="Projects">
					<h3 class="mb-1.5 label-large text-on-surface-variant">In these projects</h3>
					<PluginProjects {projects} bind:projectIds bind:allProjects />
				</section>
				<p class="body-small text-on-surface-variant">Saving restarts it, so it picks the changes up.</p>
			{:else if tab === 'runs'}
				{#if runs.length}
					<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container">
						{#each runs as r (r.id)}
							<li class="px-3 py-2 body-small">
								<p class="flex items-center gap-2">
									<span class="font-medium {RUN[r.status]}">{r.status}</span>
									<span class="min-w-0 flex-1 truncate text-on-surface">{r.label ?? r.action}{r.file_name ? `: ${r.file_name}` : ''}</span>
									<span class="shrink-0 text-on-surface-variant" title={absoluteTime(r.started_at)}>{relativeTime(r.started_at)}</span>
								</p>
								{#if r.message}<p class="mt-0.5 break-words text-on-surface-variant">{r.message}</p>{/if}
							</li>
						{/each}
					</ul>
				{:else}
					<p class="body-small text-on-surface-variant italic">Nobody has run its actions yet.</p>
				{/if}
			{:else}
				<div class="flex items-center gap-2">
					<p class="flex-1 body-small text-on-surface-variant">What it logged (console.log and friends) and anything it printed, newest first.</p>
					<IconButton icon="refresh" label="Refresh the log" size="sm" onclick={() => loadMore(plugin!.id)} />
				</div>
				{#if logs.length}
					<ol class="max-h-[50vh] overflow-auto rounded-md bg-surface-container-lowest px-3 py-2 font-mono text-[12px] leading-relaxed">
						{#each logs as l (l.id)}
							<li class="flex gap-2 {LEVEL[l.level]}">
								<span class="shrink-0 text-on-surface-variant" title={absoluteTime(l.at)}>{new Date(l.at).toLocaleTimeString()}</span>
								<span class="min-w-0 break-words whitespace-pre-wrap">{l.message}</span>
							</li>
						{/each}
					</ol>
				{:else}
					<p class="body-small text-on-surface-variant italic">Nothing logged yet.</p>
				{/if}
			{/if}
			{#if error}<p class="body-small text-error" role="alert">{error}</p>{/if}
		</div>
	{/if}
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>{tab === 'settings' ? 'Cancel' : 'Close'}</Button>
		{#if tab === 'settings'}
			<Button icon="check" loading={saving} disabled={!allProjects && !projectIds.length} onclick={save}>Save</Button>
		{/if}
	{/snippet}
</Dialog>
