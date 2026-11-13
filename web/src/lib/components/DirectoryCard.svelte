<script lang="ts">
	import type { DirectoryEntry } from '$lib/api';
	import { Button, Card, Icon } from '$lib/m3';
	import PluginIcon from './PluginIcon.svelte';

	/** One plugin in the plugin directory (Browse, a publisher's page). */
	interface Props {
		entry: DirectoryEntry;
		showPublisher?: boolean;
		oninstall: (e: DirectoryEntry) => void;
	}
	let { entry: e, showPublisher = true, oninstall }: Props = $props();
</script>

<Card variant="outlined" class="flex flex-col gap-2 px-4 py-3.5">
	<div class="flex items-start gap-3">
		<PluginIcon src={e.icon_url} size={40} />
		<div class="min-w-0 flex-1">
			<a href="/settings/plugins/directory/{e.id}" class="title-medium text-on-surface hover:underline">{e.name}</a>
			<p class="body-small text-on-surface-variant">
				{#if showPublisher}by <a href="/settings/plugins/publishers/{e.publisher}" class="text-primary hover:underline">{e.publisher_name}</a> · {/if}
				{e.latest ? e.latest.version : 'no release yet'}
			</p>
		</div>
		{#if e.update}
			<span class="flex items-center gap-1 rounded-full bg-tertiary-container px-2 py-0.5 label-medium text-on-tertiary-container"><Icon name="upgrade" size={14} />Update</span>
		{:else if e.installed}
			<span class="flex items-center gap-1 rounded-full bg-primary-container px-2 py-0.5 label-medium text-on-primary-container"><Icon name="check" size={14} />Installed</span>
		{/if}
	</div>
	{#if e.description}<p class="line-clamp-3 body-medium text-on-surface">{e.description}</p>{/if}
	{#if e.tags.length}
		<p class="flex flex-wrap gap-1">
			{#each e.tags as t (t)}<span class="rounded-xs bg-surface-container-high px-1.5 py-0.5 label-small text-on-surface-variant">{t}</span>{/each}
		</p>
	{/if}
	{#if e.taken}
		<p class="flex items-center gap-1.5 body-small text-on-surface-variant"><Icon name="block" size={16} />Another plugin here has the id <code class="font-mono">{e.id}</code>.</p>
	{:else if e.error}
		<p class="flex items-center gap-1.5 body-small text-error"><Icon name="error" size={16} />{e.error}</p>
	{/if}
	<div class="mt-auto -mb-1 flex justify-end gap-1">
		<Button variant="text" href="/settings/plugins/directory/{e.id}">Details</Button>
		{#if e.latest && !e.taken && (!e.installed || e.update)}
			<Button variant="tonal" icon={e.update ? 'upgrade' : 'download'} onclick={() => oninstall(e)}>{e.update ? `Update to ${e.latest.version}` : 'Install'}</Button>
		{/if}
	</div>
</Card>
