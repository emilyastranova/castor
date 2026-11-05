<script lang="ts">
	import type { ExportRequest, ImportResult } from '$lib/api';
	import { IconButton } from '$lib/m3';
	import { canEdit } from '$lib/session';
	import ExportDialog from './ExportDialog.svelte';
	import ImportDialog from './ImportDialog.svelte';

	/** Import and Export buttons for a library page's top bar. Export takes what `request`
	 *  names (the page's own items, not built-ins: every Castor has those). */
	interface Props {
		/** e.g. "commands". */
		noun: string;
		request: ExportRequest;
		count: number;
		secrets?: boolean;
		onimported?: (result: ImportResult) => void;
	}
	let { noun, request, count, secrets = false, onimported }: Props = $props();
	let exportOpen = $state(false);
	let importOpen = $state(false);
</script>

{#if canEdit()}<IconButton icon="upload" label="Import {noun}" onclick={() => (importOpen = true)} />{/if}
<IconButton icon="download" label={count ? `Export your ${noun}` : `No ${noun} of yours to export`} disabled={!count} onclick={() => (exportOpen = true)} />

<ExportDialog
	bind:open={exportOpen}
	title="Export your {noun}"
	{request}
	{secrets}
	summary="{count} item{count === 1 ? '' : 's'} as one .castor.zip. Built-ins are left out; every Castor has them. Open one to export it alone as YAML."
/>
<ImportDialog bind:open={importOpen} title="Import {noun}" {onimported} />
