<script lang="ts">
	import { Icon, IconButton, TextField } from '$lib/m3';
	import { isListRef, lists, type ListRef } from '$lib/lists.svelte';
	import ListPickerDialog from './ListPickerDialog.svelte';
	import ListIngestDialog from './ListIngestDialog.svelte';
	import { MAX_IMPORT_BYTES } from '$lib/targets';
	import { snackbar } from '$lib/snackbar.svelte';

	/** A text or list field that can also link a saved list ({list: id}) or be filled from
	 *  an imported file. Used for command arguments and playbook inputs. */
	interface Props {
		label: string;
		value: unknown;
		/** list: entries are kept one per line; string: comma separated. */
		type: 'string' | 'list';
		required?: boolean;
		mono?: boolean;
		placeholder?: string;
		supporting?: string;
		error?: string | null;
		projectId?: string | null;
		onchange: (value: unknown) => void;
	}
	let { label, value, type, required = false, mono = true, placeholder = '', supporting = '', error = null, projectId = null, onchange }: Props = $props();

	let pickerOpen = $state(false);
	let ingestOpen = $state(false);
	// A file dropped straight onto the field opens the import preview with it loaded.
	let droppedText = $state<string | null>(null);
	let dragOver = $state(false);
	$effect(() => {
		if (!ingestOpen) droppedText = null;
	});

	async function onDrop(e: DragEvent) {
		const file = e.dataTransfer?.files?.[0];
		if (!file) return;
		e.preventDefault();
		dragOver = false;
		if (file.size > MAX_IMPORT_BYTES) {
			snackbar.error(`${file.name} is too large to import`);
			return;
		}
		droppedText = await file.text();
		ingestOpen = true;
	}

	let ref = $derived(isListRef(value) ? (value as ListRef) : null);
	let linked = $derived(ref ? lists.find(ref.list) : undefined);
	$effect(() => {
		if (ref && !lists.loaded) void lists.load();
	});

	const asText = (v: unknown) => (Array.isArray(v) ? v.join(type === 'list' ? '\n' : ', ') : v === null || v === undefined ? '' : String(v));

	function useEntries(entries: string[]) {
		onchange(type === 'list' ? entries.join('\n') : entries.join(','));
	}
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="rounded-xs transition-shadow {dragOver ? 'ring-2 ring-primary' : ''}"
	ondragover={(e) => {
		if (e.dataTransfer?.types.includes('Files')) {
			e.preventDefault();
			dragOver = true;
		}
	}}
	ondragleave={() => (dragOver = false)}
	ondrop={onDrop}
>
{#if ref}
	<div class="flex flex-col gap-1 pt-2">
		<div class="flex items-center gap-3 rounded-xs border border-primary/60 bg-primary-container/20 px-3 py-2">
			<Icon name="link" size={20} class="shrink-0 text-primary" />
			<div class="min-w-0 flex-1">
				<p class="label-medium text-on-surface-variant">{label}{required ? ' *' : ''}</p>
				<p class="truncate body-medium text-on-surface">
					{linked?.set.name ?? ref.list}
					<span class="text-on-surface-variant">
						· {linked ? `${linked.entries.length} entr${linked.entries.length === 1 ? 'y' : 'ies'}` : lists.loaded ? 'list not found' : '…'}
					</span>
				</p>
				{#if linked?.entries.length}
					<p class="truncate font-mono text-[12px] text-on-surface-variant">{linked.entries.slice(0, 8).join(', ')}{linked.entries.length > 8 ? ', …' : ''}</p>
				{/if}
			</div>
			<IconButton icon="swap_horiz" label="Use a different list" size="sm" onclick={() => (pickerOpen = true)} />
			<IconButton icon="link_off" label="Unlink (type values instead)" size="sm" onclick={() => onchange(linked ? asText(linked.entries) : '')} />
		</div>
		<p class="px-4 body-small {error ? 'text-error' : 'text-on-surface-variant'}">
			{error ?? 'Linked: edits to this list apply the next time the card runs'}
		</p>
	</div>
{:else}
	<TextField
		{label}
		{required}
		{mono}
		multiline={type === 'list'}
		rows={2}
		value={asText(value)}
		{placeholder}
		{error}
		supporting={[supporting, 'drop a file here to import it'].filter(Boolean).join(' · ')}
		trailingIcon="playlist_add"
		trailingLabel="Use a saved list or import a file"
		ontrailing={() => (pickerOpen = true)}
		oninput={(e) => onchange((e.currentTarget as HTMLInputElement).value)}
	/>
{/if}
</div>

{#if pickerOpen}
	<ListPickerDialog
		bind:open={pickerOpen}
		{projectId}
		onpick={(l) => onchange({ list: l.set.id })}
		onimport={() => (ingestOpen = true)}
	/>
{/if}
{#if ingestOpen}
	<ListIngestDialog
		bind:open={ingestOpen}
		initialText={droppedText ?? (ref ? '' : asText(value))}
		onuse={(entries, saved) => {
			if (saved) {
				void lists.load(true);
				onchange({ list: saved.id });
			} else useEntries(entries);
			droppedText = null;
		}}
	/>
{/if}
