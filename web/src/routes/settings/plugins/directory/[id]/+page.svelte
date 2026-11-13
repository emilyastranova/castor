<script lang="ts">
	import type { DirectoryPluginPage } from '$lib/api';
	import { api } from '$lib/api';
	import { Button, Icon, IconButton, TopAppBar } from '$lib/m3';
	import { absoluteTime, relativeTime } from '$lib/format';
	import { renderMarkdown } from '$lib/markdown';
	import { copyableCode } from '$lib/copyCode';
	import { EVENT_WORDS } from '$lib/plugins.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import PluginIcon from '$lib/components/PluginIcon.svelte';
	import PluginInstallDialog from '$lib/components/PluginInstallDialog.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let entry = $state<DirectoryPluginPage>(data.entry);
	// svelte-ignore state_referenced_locally
	let loaded = data.entry;
	$effect(() => {
		if (data.entry !== loaded) {
			loaded = data.entry;
			entry = data.entry;
		}
	});
	let installOpen = $state(false);

	/** A README from the plugin's repository: rendered sanitised, without its pictures (they'd be
	 *  fetched from wherever it points, telling that host who's looking). */
	const markdown = (text: string) => renderMarkdown(text).replace(/<img\b[^>]*>/gi, '');
	let m = $derived(entry.manifest);
	let installable = $derived(!!entry.latest && !entry.taken && !entry.problems.length && (!entry.installed || entry.update));
</script>

<svelte:head><title>{entry.name} · Plugins · Castor</title></svelte:head>

<TopAppBar title={entry.name} subtitle="Plugin directory">
	{#snippet leading()}<IconButton icon="arrow_back" label="Back to Browse" href="/settings/plugins?tab=browse" />{/snippet}
	{#snippet actions()}
		{#if installable}
			<Button icon={entry.update ? 'upgrade' : 'download'} onclick={() => (installOpen = true)}>{entry.update ? `Update to ${entry.latest?.version}` : 'Install'}</Button>
		{:else if entry.installed}
			<Button variant="outlined" icon="check" href="/settings/plugins">Installed {entry.installed}</Button>
		{/if}
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-5 pt-2">
		<div class="flex items-start gap-4">
			<PluginIcon src={entry.icon_url} size={64} />
			<div class="min-w-0 flex-1">
				<p class="body-medium text-on-surface-variant">
					by <a href="/settings/plugins/publishers/{entry.publisher}" class="text-primary hover:underline">{entry.publisher_name}</a>
					· <code class="font-mono">{entry.id}</code>
					{#if entry.release}· {entry.release.version}{#if entry.release.published_at}, <span title={absoluteTime(entry.release.published_at)}>{relativeTime(entry.release.published_at)}</span>{/if}{/if}
					{#if m?.license}· {m.license}{/if}
				</p>
				{#if entry.description}<p class="mt-1 body-large text-on-surface">{entry.description}</p>{/if}
				<p class="mt-2 flex flex-wrap gap-x-4 gap-y-1 body-small">
					<a href={entry.source_url} target="_blank" rel="noopener noreferrer" class="flex items-center gap-1 text-primary hover:underline"><Icon name="code" size={16} />Source</a>
					{#if entry.release?.url}<a href={entry.release.url} target="_blank" rel="noopener noreferrer" class="flex items-center gap-1 text-primary hover:underline"><Icon name="new_releases" size={16} />Release on GitHub</a>{/if}
					{#if m?.homepage && !m.homepage.startsWith(`https://github.com/${entry.repo}`)}<a href={m.homepage} target="_blank" rel="noopener noreferrer" class="flex items-center gap-1 text-primary hover:underline"><Icon name="open_in_new" size={16} />Homepage</a>{/if}
				</p>
			</div>
		</div>

		{#if entry.problems.length || entry.error}
			<div class="flex flex-col gap-1 rounded-md bg-error-container px-3 py-2 body-small text-on-error-container" role="alert">
				{#each [...entry.problems, ...(entry.error ? [entry.error] : [])] as p (p)}<p class="flex gap-2"><Icon name="error" size={16} class="mt-px shrink-0" />{p}</p>{/each}
			</div>
		{/if}
		{#if entry.taken}
			<p class="flex items-center gap-2 rounded-md bg-surface-container px-3 py-2 body-small text-on-surface-variant">
				<Icon name="block" size={16} />Another publisher's plugin with the id <code class="font-mono">{entry.id}</code> is installed here, so this one can't be.
			</p>
		{/if}

		{#if m}
			<section class="grid gap-3 sm:grid-cols-2" aria-label="What it asks for">
				<div class="rounded-md border px-3 py-2.5 {m.runtime === 'unrestricted' ? 'border-error bg-error-container/40' : 'border-outline-variant'}">
					<p class="flex items-center gap-1.5 label-large {m.runtime === 'unrestricted' ? 'text-error' : 'text-on-surface'}">
						<Icon name={m.runtime === 'unrestricted' ? 'gpp_maybe' : 'shield'} size={18} />{m.runtime === 'unrestricted' ? 'Unrestricted' : 'Sandboxed'}
					</p>
					<p class="mt-1 body-small text-on-surface-variant">
						{#if m.runtime === 'unrestricted'}Full access to the server it runs on: files, programs, any network. It can only be installed with restricted mode off.
						{:else if !m.main && !m.actions.length}No code: it only adds to the library.
						{:else}No access to the server's files or programs{m.network.length ? `; it reaches ${m.network.join(', ')}` : ', and no network'}.{/if}
					</p>
				</div>
				<div class="rounded-md border border-outline-variant px-3 py-2.5">
					<p class="label-large text-on-surface">It can</p>
					<p class="mt-1 body-small text-on-surface-variant">{m.permission_details.map((s) => s.label).join(' · ') || 'Nothing in Castor'}</p>
					{#if m.events.length}<p class="mt-1 body-small text-on-surface-variant">Told when: {m.events.map((e) => EVENT_WORDS[e] ?? e).join(' · ')}</p>{/if}
				</div>
				{#if m.actions.length || m.ui?.length || m.asset_types?.length || m.contents?.length}
					<div class="rounded-md border border-outline-variant px-3 py-2.5 sm:col-span-2">
						<p class="label-large text-on-surface">It adds</p>
						<ul class="mt-1 flex flex-wrap gap-x-4 gap-y-1 body-small text-on-surface-variant">
							{#each m.actions as a (a.id)}<li class="flex items-center gap-1"><Icon name={a.icon} size={16} />{a.label}</li>{/each}
							{#each m.ui ?? [] as u (u.id)}<li class="flex items-center gap-1"><Icon name={u.icon} size={16} />{u.title}</li>{/each}
							{#each m.asset_types ?? [] as t (t.name)}<li class="flex items-center gap-1"><Icon name={t.icon} size={16} />{t.plural || `${t.label}s`}</li>{/each}
							{#each m.contents ?? [] as c (c)}<li class="flex items-center gap-1"><Icon name="library_books" size={16} />{c.replace('_', ' ')} for the library</li>{/each}
						</ul>
					</div>
				{/if}
			</section>
			<p class="-mt-2 body-small text-on-surface-variant">Installing shows all of it again{m.main ? ', with its settings and the projects to turn it on in,' : ''} before anything runs.</p>
		{/if}

		{#if entry.release?.notes}
			<section aria-label="Release notes">
				<h2 class="mb-1.5 title-small text-on-surface">What's in {entry.release.version}</h2>
				<div class="prose-m3 body-medium text-on-surface" use:copyableCode>{@html markdown(entry.release.notes)}</div>
			</section>
		{/if}
		{#if entry.readme}
			<section aria-label="README" class="border-t border-outline-variant pt-4">
				<div class="prose-m3 body-medium text-on-surface" use:copyableCode>{@html markdown(entry.readme)}</div>
			</section>
		{/if}
	</div>
</div>

<PluginInstallDialog
	bind:open={installOpen}
	projects={data.projects}
	directoryId={entry.id}
	oninstalled={async (p) => {
		snackbar.show(`Installed ${p.name} ${p.version}`);
		entry = await api().plugins.directoryPlugin(entry.id);
	}}
/>
