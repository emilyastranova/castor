<script lang="ts">
	import type { DirectoryEntry, PluginDirectory } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon, TextField } from '$lib/m3';
	import DirectoryCard from './DirectoryCard.svelte';

	/** Settings → Plugins → Browse: the plugin directory, searchable, with what's installed here. */
	interface Props {
		oninstall: (e: DirectoryEntry) => void;
		/** Bumped by the page after an install, to show the new state. */
		version?: number;
	}
	let { oninstall, version = 0 }: Props = $props();

	let dir = $state<PluginDirectory | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let query = $state('');
	let tag = $state<string | null>(null);

	async function load(refresh = false) {
		loading = true;
		error = null;
		try {
			dir = await api().plugins.directory(refresh);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not read the plugin directory';
		} finally {
			loading = false;
		}
	}
	$effect(() => {
		void version;
		void load();
	});

	let tags = $derived([...new Set((dir?.plugins ?? []).flatMap((p) => p.tags))].sort());
	let shown = $derived.by(() => {
		const q = query.trim().toLowerCase();
		return (dir?.plugins ?? []).filter(
			(p) =>
				(!tag || p.tags.includes(tag)) &&
				(!q || [p.name, p.id, p.description, p.publisher_name, ...p.tags].some((s) => s.toLowerCase().includes(q)))
		);
	});
</script>

<div class="flex flex-col gap-3">
	<p class="max-w-[70ch] body-small text-on-surface-variant">
		The plugin directory lists plugins anyone can publish. Nobody reviews them: installing one shows what it asks for, and it runs sandboxed unless it says otherwise and
		you allow it. Releases come from each plugin's GitHub repository, checked against their published SHA-256.
	</p>
	{#if dir && !dir.enabled}
		<div class="flex items-start gap-3 rounded-lg border border-dashed border-outline-variant px-4 py-6 body-medium text-on-surface-variant">
			<Icon name="cloud_off" class="mt-0.5 shrink-0" />
			<p>The plugin directory is turned off on this server (<code class="font-mono">CASTOR_PLUGIN_DIRECTORY_URL</code> is empty). Install plugins from their .zip instead.</p>
		</div>
	{:else}
		<div class="flex flex-wrap items-center gap-2">
			<TextField label="Search plugins" bind:value={query} class="min-w-56 flex-1" />
			<Button variant="text" icon="refresh" loading={loading} onclick={() => load(true)}>Refresh</Button>
		</div>
		{#if tags.length}
			<div class="flex flex-wrap gap-1.5" role="group" aria-label="Tags">
				{#each tags as t (t)}
					<button
						type="button"
						class="state-layer rounded-full border px-3 py-1 label-large {tag === t ? 'border-transparent bg-secondary-container text-on-secondary-container' : 'border-outline-variant text-on-surface-variant'}"
						aria-pressed={tag === t}
						onclick={() => (tag = tag === t ? null : t)}>{t}</button
					>
				{/each}
			</div>
		{/if}
		{#if error || dir?.error}
			<p class="flex items-start gap-2 rounded-md bg-error-container/60 px-3 py-2 body-small text-on-error-container" role="alert">
				<Icon name="error" size={16} class="mt-px shrink-0" />{error ?? dir?.error}
			</p>
		{/if}
		{#if !dir && loading}
			<p class="flex items-center gap-2 py-6 body-medium text-on-surface-variant"><Icon name="progress_activity" class="animate-spin" />Reading the directory…</p>
		{:else if dir}
			<div class="grid gap-3 sm:grid-cols-2">
				{#each shown as e (e.id)}
					<DirectoryCard entry={e} {oninstall} />
				{:else}
					<p class="py-6 body-medium text-on-surface-variant sm:col-span-2">{dir.plugins.length ? 'Nothing matches.' : 'The directory is empty.'}</p>
				{/each}
			</div>
		{/if}
	{/if}
</div>
