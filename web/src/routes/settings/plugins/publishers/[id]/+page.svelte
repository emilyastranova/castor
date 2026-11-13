<script lang="ts">
	import type { DirectoryEntry } from '$lib/api';
	import { api } from '$lib/api';
	import { Icon, IconButton, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import DirectoryCard from '$lib/components/DirectoryCard.svelte';
	import PluginInstallDialog from '$lib/components/PluginInstallDialog.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let publisher = $state(data.publisher);
	// svelte-ignore state_referenced_locally
	let loaded = data.publisher;
	$effect(() => {
		if (data.publisher !== loaded) {
			loaded = data.publisher;
			publisher = data.publisher;
		}
	});
	let installOpen = $state(false);
	let installing = $state<string | null>(null);
	const install = (e: DirectoryEntry) => {
		installing = e.id;
		installOpen = true;
	};
</script>

<svelte:head><title>{publisher.name} · Plugins · Castor</title></svelte:head>

<TopAppBar title={publisher.name} subtitle="Publisher in the plugin directory">
	{#snippet leading()}<IconButton icon="arrow_back" label="Back to Browse" href="/settings/plugins?tab=browse" />{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-4 pt-2">
		<div class="flex items-start gap-4">
			<span class="grid size-14 shrink-0 place-items-center rounded-full bg-secondary-container text-on-secondary-container"><Icon name="storefront" size={28} /></span>
			<div class="min-w-0 flex-1">
				{#if publisher.description}<p class="body-large text-on-surface">{publisher.description}</p>{/if}
				<p class="mt-1 flex flex-wrap gap-x-4 gap-y-1 body-small text-on-surface-variant">
					<span><code class="font-mono">{publisher.id}</code></span>
					{#each publisher.github as g (g)}
						<a href="https://github.com/{g}" target="_blank" rel="noopener noreferrer" class="flex items-center gap-1 text-primary hover:underline"><Icon name="code" size={16} />github.com/{g}</a>
					{/each}
					{#if publisher.url}<a href={publisher.url} target="_blank" rel="noopener noreferrer" class="flex items-center gap-1 text-primary hover:underline"><Icon name="open_in_new" size={16} />Website</a>{/if}
				</p>
				<p class="mt-2 body-small text-on-surface-variant">
					The directory checks that each of a publisher's plugins comes from one of its GitHub accounts and says it's theirs; it doesn't review what they do.
				</p>
			</div>
		</div>
		<h2 class="title-small text-on-surface">{publisher.plugins.length} plugin{publisher.plugins.length === 1 ? '' : 's'}</h2>
		<div class="grid gap-3 sm:grid-cols-2">
			{#each publisher.plugins as e (e.id)}<DirectoryCard entry={e} showPublisher={false} oninstall={install} />{/each}
		</div>
	</div>
</div>

<PluginInstallDialog
	bind:open={installOpen}
	projects={data.projects}
	directoryId={installing}
	oninstalled={async (p) => {
		snackbar.show(`Installed ${p.name} ${p.version}`);
		publisher = await api().plugins.publisher(publisher.id);
	}}
/>
