<script lang="ts">
	import type { VariableSet } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, Select, Switch, TextField } from '$lib/m3';
	import { bytes } from '$lib/format';
	import { snackbar } from '$lib/snackbar.svelte';
	import {
		ENTRY_TYPES,
		MAX_IMPORT_BYTES,
		SEPARATORS,
		entryType,
		parseEntries,
		summarise,
		type EntryType,
		type Separator
	} from '$lib/targets';

	/** Paste or drop a file of hosts/IPs/URLs, preview how it parses, then use it once or
	 *  save it as a target list. */
	interface Props {
		open: boolean;
		title?: string;
		/** Saving is the only option (creating a target list from the Variables page). */
		saveOnly?: boolean;
		/** No saving: just hand the entries back (filling a field in a set). */
		useOnly?: boolean;
		/** Text to start from, e.g. a field's current value. */
		initialText?: string;
		/** entries: the parsed list; saved: the new target list if the user saved one. */
		onuse: (entries: string[], saved: VariableSet | null) => void;
	}
	let { open = $bindable(false), title = 'Import a list', saveOnly = false, useOnly = false, initialText = '', onuse }: Props = $props();

	let text = $state('');
	let separator = $state<Separator>('auto');
	let custom = $state('');
	let stripComments = $state(true);
	let dedupe = $state(true);
	let save = $state(false);
	let name = $state('');
	let fileName = $state<string | null>(null);
	let dragging = $state(false);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let filter = $state<EntryType | null>(null);
	let openedFor = $state(false);

	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (!open) return;
		text = initialText;
		separator = 'auto';
		custom = '';
		save = saveOnly;
		name = '';
		fileName = null;
		error = null;
		filter = null;
	});

	let parsed = $derived(parseEntries(text, { separator, custom, stripComments, dedupe }));
	let counts = $derived(summarise(parsed.entries));
	let shown = $derived(filter ? parsed.entries.filter((e) => entryType(e) === filter) : parsed.entries);
	const PREVIEW_LIMIT = 500;

	async function readFile(file: File) {
		if (file.size > MAX_IMPORT_BYTES) {
			snackbar.error(`${file.name} is ${bytes(file.size)}; lists are limited to ${bytes(MAX_IMPORT_BYTES)}`);
			return;
		}
		text = await file.text();
		fileName = file.name;
		if (!name) name = file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
	}

	function onDrop(e: DragEvent) {
		e.preventDefault();
		dragging = false;
		const file = e.dataTransfer?.files?.[0];
		if (file) void readFile(file);
	}

	function pick(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		if (file) void readFile(file);
	}

	async function confirm() {
		error = null;
		if (!parsed.entries.length) return;
		let saved: VariableSet | null = null;
		if (save) {
			saving = true;
			try {
				saved = await api().variables.createSet({
					kind_id: 'target-list',
					name: name.trim(),
					description: fileName ? `Imported from ${fileName}` : '',
					values: { entries: parsed.entries }
				});
				snackbar.show(`Saved “${saved.name}” (${parsed.entries.length} entries)`);
			} catch (err) {
				error = err instanceof ApiError ? err.message : 'Could not save the list';
				saving = false;
				return;
			}
			saving = false;
		}
		onuse(parsed.entries, saved);
		open = false;
	}
</script>

<Dialog bind:open {title} icon="playlist_add" size="lg" initialFocus="dialog">
	<div class="grid gap-4 pt-2 pb-2 md:grid-cols-2">
		<!-- input -->
		<div class="flex min-w-0 flex-col gap-3">
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<label
				class="flex cursor-pointer flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed px-4 py-5 text-center transition-colors
					{dragging ? 'border-primary bg-primary-container/30' : 'border-outline-variant hover:border-outline'}"
				ondragover={(e) => {
					e.preventDefault();
					dragging = true;
				}}
				ondragleave={() => (dragging = false)}
				ondrop={onDrop}
			>
				<Icon name="upload_file" size={28} class="text-primary" />
				<span class="body-medium text-on-surface">{fileName ? fileName : 'Drop a file here, or click to choose one'}</span>
				<span class="body-small text-on-surface-variant">Text, CSV, or any list of hosts, IPs, ranges, domains or URLs</span>
				<input type="file" class="hidden" accept=".txt,.csv,.lst,.list,text/*" onchange={pick} />
			</label>
			<TextField
				label="…or paste"
				bind:value={text}
				multiline
				rows={9}
				mono
				placeholder={'10.0.0.0/24\nfileserver.lab.local\nhttps://app.lab.local'}
			/>
			<div class="grid gap-3 sm:grid-cols-2">
				<Select label="Separated by" value={separator} options={SEPARATORS} onchange={(v) => (separator = v as Separator)} />
				{#if separator === 'custom'}
					<TextField label="Separator" bind:value={custom} mono placeholder="|" />
				{/if}
			</div>
			<div class="flex flex-wrap gap-x-6 gap-y-2">
				<Switch bind:checked={dedupe} label="Remove duplicates" />
				<Switch bind:checked={stripComments} label="Ignore # comments" />
			</div>
		</div>

		<!-- preview -->
		<div class="flex min-w-0 flex-col gap-2">
			<div class="flex flex-wrap items-baseline gap-x-2">
				<span class="title-small text-on-surface">Preview</span>
				<span class="body-small text-on-surface-variant">
					{parsed.entries.length} entr{parsed.entries.length === 1 ? 'y' : 'ies'}{parsed.duplicates
						? ` · ${parsed.duplicates} duplicate${parsed.duplicates === 1 ? '' : 's'} removed`
						: ''}{parsed.comments ? ` · ${parsed.comments} comment${parsed.comments === 1 ? '' : 's'} ignored` : ''}
				</span>
			</div>
			{#if parsed.entries.length}
				<div class="flex flex-wrap gap-1.5">
					{#each Object.entries(counts) as [type, count] (type)}
						{@const meta = ENTRY_TYPES[type as EntryType]}
						<button
							type="button"
							class="flex items-center gap-1 rounded-full px-2.5 py-1 label-medium transition-opacity {meta.tone} {filter && filter !== type ? 'opacity-40' : ''}"
							title="Show only {meta.label.toLowerCase()} entries"
							onclick={() => (filter = filter === type ? null : (type as EntryType))}
						>
							<Icon name={meta.icon} size={14} />{meta.label} · {count}
						</button>
					{/each}
				</div>
			{/if}
			<ol class="min-h-48 flex-1 overflow-auto rounded-md bg-surface-container font-mono text-[12.5px] leading-6" style="max-height: 22rem;">
				{#each shown.slice(0, PREVIEW_LIMIT) as entry, i (entry + i)}
					{@const t = entryType(entry)}
					<li class="flex items-center gap-2 border-b border-outline-variant/40 px-3">
						<span class="w-8 shrink-0 text-right text-on-surface-variant">{i + 1}</span>
						<span class="min-w-0 flex-1 truncate {t === 'other' ? 'text-error' : 'text-on-surface'}" title={entry}>{entry}</span>
						<span class="shrink-0 text-[11px] text-on-surface-variant">{ENTRY_TYPES[t].label}</span>
					</li>
				{:else}
					<li class="px-3 py-6 text-center body-medium text-on-surface-variant">Nothing to import yet.</li>
				{/each}
				{#if shown.length > PREVIEW_LIMIT}
					<li class="px-3 py-2 body-small text-on-surface-variant">…and {shown.length - PREVIEW_LIMIT} more</li>
				{/if}
			</ol>
			{#if counts.other}
				<p class="body-small text-error">
					{counts.other} entr{counts.other === 1 ? 'y doesn’t' : 'ies don’t'} look like a host, IP, range or URL. Check the separator, or keep them if
					they're intended.
				</p>
			{/if}
		</div>
	</div>

	<div class="mt-2 flex flex-col gap-3 rounded-md bg-surface-container p-4 {useOnly ? 'hidden' : ''}">
		{#if !saveOnly}
			<div class="flex items-center justify-between gap-3">
				<div>
					<p class="body-medium text-on-surface">Save as a target list</p>
					<p class="body-small text-on-surface-variant">Reuse it on other cards and playbooks, and edit it in one place (Variables).</p>
				</div>
				<Switch bind:checked={save} />
			</div>
		{/if}
		{#if save}
			<TextField label="List name" bind:value={name} required placeholder="e.g. Lab network, Lab exclusion list, Test computer" />
		{/if}
		{#if error}<p class="body-small text-error">{error}</p>{/if}
	</div>

	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="check" loading={saving} disabled={!parsed.entries.length || (save && !name.trim())} onclick={confirm}>
			{save ? (saveOnly ? 'Create list' : 'Save and use') : `Use ${parsed.entries.length || ''} entr${parsed.entries.length === 1 ? 'y' : 'ies'}`}
		</Button>
	{/snippet}
</Dialog>
