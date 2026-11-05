<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { Button, Card, Icon, TopAppBar } from '$lib/m3';
	import ExportDialog from '$lib/components/ExportDialog.svelte';
	import ImportDialog from '$lib/components/ImportDialog.svelte';
	import { absoluteTime, relativeTime } from '$lib/format';

	let { data } = $props();
	let exportOpen = $state(false);
	let importOpen = $state(false);
</script>

<svelte:head><title>Import and export · Castor</title></svelte:head>

<TopAppBar title="Import and export" subtitle="Move projects and libraries between Castor instances, or keep a copy" />

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="grid max-w-4xl gap-4 pt-2 md:grid-cols-2">
		<Card variant="outlined" class="flex flex-col gap-3 p-5">
			<div class="flex items-center gap-3">
				<span class="flex size-10 items-center justify-center rounded-md bg-primary-container text-on-primary-container"><Icon name="inventory_2" /></span>
				<h2 class="title-medium text-on-surface">Export everything</h2>
			</div>
			<p class="body-medium text-on-surface-variant">
				Every project, your commands, tools, playbooks and variable kinds, variable sets, clients and people (names, roles and pictures; never passwords or tokens). Built-ins aren't included: every Castor ships them.
			</p>
			<Button icon="download" class="mt-auto self-start" onclick={() => (exportOpen = true)}>Export everything…</Button>
		</Card>
		<Card variant="outlined" class="flex flex-col gap-3 p-5">
			<div class="flex items-center gap-3">
				<span class="flex size-10 items-center justify-center rounded-md bg-secondary-container text-on-secondary-container"><Icon name="upload" /></span>
				<h2 class="title-medium text-on-surface">Import an archive</h2>
			</div>
			<p class="body-medium text-on-surface-variant">
				A <code class="font-mono">.castor.zip</code> from any export. You'll see what's new and what already exists before anything changes, and the import is all or nothing.
			</p>
			<Button variant="tonal" icon="upload" class="mt-auto self-start" onclick={() => (importOpen = true)}>Import…</Button>
		</Card>
	</div>

	<h2 class="mt-8 mb-2 title-medium text-on-surface">History</h2>
	{#if data.history.length}
		<ul class="flex max-w-4xl flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container">
			{#each data.history as h (h.id)}
				<li class="flex items-center gap-3 px-4 py-2.5">
					<Icon name={h.type === 'transfer.export' ? 'download' : 'upload'} size={20} class="text-on-surface-variant" />
					<div class="min-w-0 flex-1">
						<p class="truncate body-medium text-on-surface">{h.message}</p>
						<p class="truncate body-small text-on-surface-variant">
							{h.actor_name}{#if h.payload.file}{' '}· <span class="font-mono text-[12px]">{h.payload.file}</span>{/if}{#if h.payload.secrets === 'passphrase'}{' '}· secrets encrypted{/if}
						</p>
					</div>
					<span class="shrink-0 body-small text-on-surface-variant" title={absoluteTime(h.created_at)}>{relativeTime(h.created_at)}</span>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="body-medium text-on-surface-variant">No imports or exports yet.</p>
	{/if}
</div>

<ExportDialog bind:open={exportOpen} title="Export everything" request={{ everything: true, label: 'castor-everything' }} history ondone={() => void invalidateAll()} />
<ImportDialog
	bind:open={importOpen}
	title="Import an archive"
	onimported={(r) => (r.projects.length === 1 ? goto(`/projects/${r.projects[0]}`) : invalidateAll())}
/>
