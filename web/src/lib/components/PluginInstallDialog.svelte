<script lang="ts">
	import type { Plugin, PluginPreview, ProjectSummary, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, TextField } from '$lib/m3';
	import { bytes } from '$lib/format';
	import { EVENT_WORDS } from '$lib/plugins.svelte';
	import PluginProjects from './PluginProjects.svelte';
	import PluginSettingsForm from './PluginSettingsForm.svelte';

	/** Install a plugin (or update one): pick its .zip or give a link, read what it is and what it
	 *  asks for, choose its projects and settings, approve. Nothing of it runs before that. */
	interface Props {
		open: boolean;
		projects: ProjectSummary[];
		oninstalled: (p: Plugin) => void;
	}
	let { open = $bindable(false), projects, oninstalled }: Props = $props();

	let file = $state<File | null>(null);
	let url = $state('');
	let checking = $state(false);
	let error = $state<string | null>(null);
	let preview = $state<PluginPreview | null>(null);
	let values = $state<Record<string, unknown>>({});
	let secrets = $state<Record<string, string>>({});
	let projectIds = $state<UUID[]>([]);
	let allProjects = $state(false);
	let approved = $state(false);
	let installing = $state(false);
	let dragging = $state(false);

	let openedFor = false;
	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (open) {
			file = null;
			url = '';
			error = null;
			preview = null;
			approved = false;
		}
	});

	async function check() {
		checking = true;
		error = null;
		try {
			preview = await api().plugins.preview(file ? { file } : { url: url.trim() });
			const m = preview.manifest;
			values = Object.fromEntries(m.settings.filter((s) => s.type !== 'secret' && s.default !== null).map((s) => [s.name, s.default]));
			secrets = {};
			projectIds = [];
			allProjects = false;
			approved = false;
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not read the plugin';
		} finally {
			checking = false;
		}
	}
	function onDrop(e: DragEvent) {
		e.preventDefault();
		dragging = false;
		const f = e.dataTransfer?.files?.[0];
		if (f) {
			file = f;
			url = '';
		}
	}

	let isUpdate = $derived(!!preview?.existing);
	let newPerms = $derived(new Set(preview?.new_permissions.permissions ?? []));
	let newHosts = $derived(new Set(preview?.new_permissions.network ?? []));
	let newEvents = $derived(new Set(preview?.new_permissions.events ?? []));
	let anythingNew = $derived(newPerms.size + newHosts.size + newEvents.size > 0);
	let blocked = $derived(!preview || preview.problems.length > 0 || !!preview.existing?.same);
	async function install() {
		if (!preview) return;
		installing = true;
		error = null;
		try {
			const p = await api().plugins.install({
				token: preview.token,
				source: preview.source,
				approve: approved,
				project_ids: projectIds,
				all_projects: allProjects,
				settings: values,
				secrets: Object.fromEntries(Object.entries(secrets).filter(([, v]) => v !== ''))
			});
			open = false;
			oninstalled(p);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not install it';
		} finally {
			installing = false;
		}
	}
	const PLACES: Record<string, string> = { project: 'the Assets page', card: 'cards', asset: 'assets' };
</script>

<Dialog bind:open title={preview ? (isUpdate ? `Update ${preview.manifest.name}` : `Install ${preview.manifest.name}`) : 'Install a plugin'} icon="extension" size="md">
	<div class="flex flex-col gap-4 pt-1 pb-2">
		{#if !preview}
			<p class="body-medium text-on-surface-variant">
				A plugin is a <code class="font-mono">.zip</code> with a <code class="font-mono">castor-plugin.yaml</code>. You'll see what it is and what it asks for before
				anything of it runs.
			</p>
			<label
				class="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center {dragging ? 'border-primary bg-primary-container/30' : 'border-outline-variant'}"
				ondragover={(e) => { e.preventDefault(); dragging = true; }}
				ondragleave={() => (dragging = false)}
				ondrop={onDrop}
			>
				<Icon name="upload_file" size={32} class="text-on-surface-variant" />
				{#if file}
					<span class="body-medium text-on-surface">{file.name} <span class="text-on-surface-variant">· {bytes(file.size)}</span></span>
				{:else}
					<span class="body-medium text-on-surface">Drop the plugin's .zip here, or <span class="text-primary underline">choose it</span></span>
				{/if}
				<input type="file" accept=".zip" class="sr-only" onchange={(e) => { file = (e.currentTarget as HTMLInputElement).files?.[0] ?? null; url = ''; }} />
			</label>
			<TextField label="Or a link to its .zip" bind:value={url} placeholder="https://github.com/…/releases/download/v1.0.0/plugin.zip" oninput={() => (file = null)} />
		{:else}
			{@const m = preview.manifest}
			<div class="flex items-start gap-3">
				<span class="grid size-11 shrink-0 place-items-center rounded-md bg-secondary-container text-on-secondary-container"><Icon name="extension" /></span>
				<div class="min-w-0 flex-1">
					<p class="title-medium text-on-surface">{m.name} <span class="body-medium text-on-surface-variant">{m.version}</span></p>
					<p class="body-small text-on-surface-variant">
						{m.publisher ? `by ${m.publisher} · ` : ''}<code class="font-mono">{m.id}</code>{m.license ? ` · ${m.license}` : ''} · {preview.files} files, {bytes(preview.size)}
						{#if m.homepage}· <a href={m.homepage} target="_blank" rel="noopener noreferrer" class="text-primary hover:underline">homepage</a>{/if}
					</p>
					{#if m.description}<p class="mt-1 body-medium text-on-surface">{m.description}</p>{/if}
				</div>
			</div>

			{#if preview.existing}
				<p class="rounded-md bg-surface-container px-3 py-2 body-medium text-on-surface">
					{preview.existing.same ? `This exact release (${m.version}) is already installed.` : `Installed: ${preview.existing.version}. This replaces it with ${m.version}; its settings and projects stay.`}
				</p>
			{/if}

			{#if preview.problems.length}
				<div class="flex flex-col gap-1 rounded-md bg-error-container px-3 py-2 body-small text-on-error-container" role="alert">
					<p class="font-medium">This Castor can't run it:</p>
					{#each preview.problems as p (p)}<p class="flex gap-2"><Icon name="error" size={16} class="mt-px shrink-0" />{p}</p>{/each}
				</div>
			{/if}

			<section class="flex gap-3 rounded-md border border-outline-variant px-3 py-2.5" aria-label="How it runs">
				<Icon name="shield" class="mt-0.5 shrink-0 text-primary" />
				<p class="body-medium text-on-surface">
					<b class="font-medium">Sandboxed.</b> It runs in its own process with no access to this server's files or programs{m.network.length ? ', and no network beyond the hosts below' : ', and no network'}.
					It can only use Castor through its own account, with the permissions below, in the projects you choose.
				</p>
			</section>

			<section aria-label="What it can do">
				<h3 class="mb-1.5 label-large text-on-surface-variant">It can</h3>
				{#if m.permission_details.length}
					<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container">
						{#each m.permission_details as s (s.name)}
							<li class="flex items-start gap-2 px-3 py-2">
								<Icon name={s.name.endsWith(':read') ? 'visibility' : 'edit'} size={18} class="mt-0.5 shrink-0 text-on-surface-variant" />
								<div class="min-w-0 flex-1">
									<p class="body-medium text-on-surface">{s.label}{#if newPerms.has(s.name)}<span class="ml-2 rounded-xs bg-tertiary-container px-1.5 label-small text-on-tertiary-container">New</span>{/if}</p>
									<p class="body-small text-on-surface-variant">{s.description}</p>
								</div>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="body-small text-on-surface-variant italic">Nothing in Castor: no permissions.</p>
				{/if}
			</section>

			{#if m.network.length}
				<section aria-label="Where it connects">
					<h3 class="mb-1.5 label-large text-on-surface-variant">It connects to</h3>
					<ul class="flex flex-wrap gap-1.5">
						{#each m.network as h (h)}
							<li class="flex items-center gap-1.5 rounded-full border border-outline-variant px-2.5 py-1 body-small text-on-surface">
								<Icon name="public" size={16} class="text-on-surface-variant" /><code class="font-mono">{h}</code>
								{#if newHosts.has(h)}<span class="rounded-xs bg-tertiary-container px-1.5 label-small text-on-tertiary-container">New</span>{/if}
							</li>
						{/each}
					</ul>
					<p class="mt-1 body-small text-on-surface-variant">What it sends there is up to the plugin: it can send what it can read in Castor.</p>
				</section>
			{/if}

			{#if m.events.length}
				<section aria-label="What it's told about">
					<h3 class="mb-1.5 label-large text-on-surface-variant">It’s told when</h3>
					<ul class="flex flex-col gap-1 body-medium text-on-surface">
						{#each m.events as e (e)}
							<li class="flex items-center gap-2">
								<Icon name="notifications" size={18} class="text-on-surface-variant" />{EVENT_WORDS[e] ?? e} <code class="font-mono body-small text-on-surface-variant">{e}</code>
								{#if newEvents.has(e)}<span class="rounded-xs bg-tertiary-container px-1.5 label-small text-on-tertiary-container">New</span>{/if}
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			{#if m.actions.length}
				<section aria-label="What it adds">
					<h3 class="mb-1.5 label-large text-on-surface-variant">It adds</h3>
					<ul class="flex flex-col gap-1.5">
						{#each m.actions as a (a.id)}
							<li class="flex items-start gap-2 body-medium text-on-surface">
								<Icon name={a.icon} size={18} class="mt-0.5 shrink-0 text-on-surface-variant" />
								<span>
									<b class="font-medium">{a.label}</b>, on {a.on.map((o) => PLACES[o]).join(' and ')}{a.file ? `, taking a ${a.file.accept.join(' or ')} file` : ''}
									{#if a.description}<span class="block body-small text-on-surface-variant">{a.description}</span>{/if}
								</span>
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			{#if !isUpdate}
				<section aria-label="Projects">
					<h3 class="mb-1.5 label-large text-on-surface-variant">In these projects</h3>
					<PluginProjects {projects} bind:projectIds bind:allProjects />
				</section>
				{#if m.settings.length}
					<section aria-label="Settings">
						<h3 class="mb-1.5 label-large text-on-surface-variant">Settings</h3>
						<PluginSettingsForm spec={m.settings} bind:values bind:secrets />
					</section>
				{/if}
			{/if}

			{#if !blocked}
				<label class="flex cursor-pointer items-start gap-2 rounded-md bg-surface-container-low px-3 py-2.5 body-medium text-on-surface">
					<input type="checkbox" bind:checked={approved} class="mt-1 accent-primary" />
					<span>I've read what {m.name} can do{anythingNew ? ', including what’s new,' : ''} and allow it{isUpdate ? '' : ' in the projects above'}.</span>
				</label>
			{/if}
		{/if}
		{#if error}<p class="body-small text-error" role="alert">{error}</p>{/if}
	</div>
	{#snippet actions()}
		{#if preview}<Button variant="text" class="mr-auto" onclick={() => (preview = null)}>Back</Button>{/if}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		{#if !preview}
			<Button icon="search" loading={checking} disabled={!file && !url.trim().startsWith('https://')} onclick={check}>Check it</Button>
		{:else}
			<Button icon={isUpdate ? 'upgrade' : 'download'} loading={installing} disabled={blocked || !approved || (!isUpdate && !allProjects && !projectIds.length)} onclick={install}>
				{isUpdate ? 'Update' : 'Install'}
			</Button>
		{/if}
	{/snippet}
</Dialog>
