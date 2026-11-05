<script lang="ts">
	import type { ImportChoice, ImportItem, ImportPreview, ImportResult } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, Select, Switch, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime } from '$lib/format';

	/** Import a .castor.zip (or a library .yaml): choose the file, see what it holds and what
	 *  already exists, pick skip / replace / keep both per item, then import it all at once. */
	interface Props {
		open: boolean;
		title?: string;
		/** File types the picker offers. */
		accept?: string;
		onimported?: (result: ImportResult) => void;
	}
	let { open = $bindable(false), title = 'Import', accept = '.zip,.yaml,.yml', onimported }: Props = $props();

	let preview = $state<ImportPreview | null>(null);
	let choices = $state<Record<string, ImportChoice>>({});
	let passphrase = $state('');
	let createUsers = $state(false);
	let busy = $state(false);
	let error = $state<string | null>(null);
	let dragging = $state(false);
	let input: HTMLInputElement | undefined = $state();

	$effect(() => {
		if (!open) return;
		preview = null;
		choices = {};
		passphrase = '';
		createUsers = false;
		error = null;
	});

	function show(p: ImportPreview) {
		preview = p;
		choices = Object.fromEntries(p.items.map((i) => [i.key, choices[i.key] ?? i.default]));
	}
	async function pick(file: File | undefined) {
		if (!file) return;
		busy = true;
		error = null;
		try {
			show(await api().transfer.preview({ file }));
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not read the file';
		} finally {
			busy = false;
		}
	}
	async function unlock() {
		if (!preview) return;
		busy = true;
		error = null;
		try {
			show(await api().transfer.preview({ token: preview.token, filename: preview.filename }, passphrase));
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not unlock';
		} finally {
			busy = false;
		}
	}
	async function run() {
		if (!preview) return;
		busy = true;
		error = null;
		try {
			const result = await api().transfer.run({
				token: preview.token,
				filename: preview.filename,
				choices,
				passphrase: preview.secrets.unlocked ? passphrase : undefined,
				create_users: createUsers
			});
			const n = Object.values(result.imported).reduce((sum, names) => sum + names.length, 0);
			snackbar.show(n ? `Imported ${n} item${n === 1 ? '' : 's'}${result.created_users.length ? ` and ${result.created_users.length} people` : ''}` : 'Nothing imported');
			open = false;
			onimported?.(result);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'The import failed; nothing was changed';
		} finally {
			busy = false;
		}
	}

	const KINDS: Record<ImportItem['kind'], { label: string; icon: string }> = {
		project: { label: 'Projects', icon: 'view_kanban' },
		variable_kind: { label: 'Variable kinds', icon: 'category' },
		tool: { label: 'Tools', icon: 'handyman' },
		command: { label: 'Commands', icon: 'terminal' },
		playbook: { label: 'Playbooks', icon: 'account_tree' },
		variable_set: { label: 'Variable sets', icon: 'key' }
	};
	const STATE: Record<ImportItem['state'], string> = { new: 'New', exists: 'Already here', builtin: 'Built-in here', same: 'Identical' };
	function choiceLabel(item: ImportItem, c: ImportChoice): string {
		if (c === 'import') return 'Import';
		if (c === 'skip') return item.state === 'same' ? 'Nothing to do' : 'Skip';
		if (c === 'replace') return 'Replace yours';
		return 'Keep both';
	}
	let groups = $derived.by(() => {
		const out: [ImportItem['kind'], ImportItem[]][] = [];
		for (const kind of Object.keys(KINDS) as ImportItem['kind'][]) {
			const items = preview?.items.filter((i) => i.kind === kind) ?? [];
			if (items.length) out.push([kind, items]);
		}
		return out;
	});
	let count = $derived(preview ? preview.items.filter((i) => choices[i.key] && choices[i.key] !== 'skip').length : 0);
	const locked = $derived(preview?.secrets.mode === 'passphrase' && !preview.secrets.unlocked);
</script>

<Dialog bind:open {title} icon="upload" size="md">
	<div class="flex flex-col gap-4 pt-2 pb-2">
		{#if !preview}
			<button
				type="button"
				class="state-layer flex flex-col items-center gap-2 rounded-md border-2 border-dashed px-6 py-10 text-center {dragging ? 'border-primary bg-primary-container/40' : 'border-outline-variant'}"
				onclick={() => input?.click()}
				ondragover={(e) => { e.preventDefault(); dragging = true; }}
				ondragleave={() => (dragging = false)}
				ondrop={(e) => { e.preventDefault(); dragging = false; void pick(e.dataTransfer?.files[0]); }}
				disabled={busy}
			>
				<Icon name={busy ? 'hourglass_top' : 'upload_file'} size={36} class="text-primary" />
				<span class="title-small text-on-surface">{busy ? 'Reading…' : 'Choose or drop a file'}</span>
				<span class="body-small text-on-surface-variant">A Castor archive (.castor.zip), or a command, tool or playbook as .yaml. Nothing changes until you import.</span>
			</button>
			<input bind:this={input} type="file" {accept} class="hidden" onchange={(e) => void pick((e.currentTarget as HTMLInputElement).files?.[0])} />
		{:else}
			<div class="rounded-md bg-surface-container px-3 py-2 body-small text-on-surface-variant">
				<p class="truncate font-medium text-on-surface">{preview.filename}</p>
				<p>
					{#if preview.manifest.created_by}Exported by {preview.manifest.created_by}{/if}
					{#if preview.manifest.created_at}{' '}{relativeTime(preview.manifest.created_at)}{/if}
					{#if preview.manifest.castor_version}{' '}· Castor {preview.manifest.castor_version}{/if}
					{#if preview.manifest.includes?.jobs}{' '}· with job history{/if}
				</p>
			</div>

			{#if locked}
				<div class="flex flex-col gap-2 rounded-md border border-outline-variant p-3">
					<p class="body-small text-on-surface-variant"><Icon name="lock" size={16} class="mr-1 align-[-3px]" />The file's secrets are encrypted. Enter the passphrase it was exported with to bring them; without it they come in empty.</p>
					<form class="flex items-end gap-2" onsubmit={(e) => { e.preventDefault(); void unlock(); }}>
						<TextField label="Passphrase" type="password" bind:value={passphrase} class="flex-1" autocomplete="off" />
						<Button variant="tonal" type="submit" icon="lock_open" disabled={!passphrase} loading={busy}>Unlock</Button>
					</form>
				</div>
			{:else if preview.secrets.unlocked}
				<p class="flex items-center gap-1 body-small text-primary"><Icon name="lock_open" size={16} />Secrets unlocked; they'll be stored in this instance's vault.</p>
			{/if}

			{#each groups as [kind, items] (kind)}
				<section>
					<h3 class="mb-1.5 flex items-center gap-1.5 label-large text-on-surface-variant"><Icon name={KINDS[kind].icon} size={18} />{KINDS[kind].label} ({items.length})</h3>
					<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container">
						{#each items as item (item.key)}
							<li class="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
								<div class="min-w-0 flex-1">
									<p class="truncate body-medium text-on-surface">{item.name}</p>
									<p class="body-small text-on-surface-variant">
										<span class={item.state === 'new' ? 'text-primary' : item.state === 'same' ? '' : 'text-tertiary'}>{STATE[item.state]}</span>
										{#if item.kind !== 'project' && item.kind !== 'variable_set'}<span class="font-mono text-[12px]"> · {item.id}</span>{/if}
										{#each item.notes as note, i (i)}<span> · {note}</span>{/each}
									</p>
									{#if choices[item.key] === 'copy'}<p class="body-small text-on-surface-variant">Comes in as <span class="font-medium text-on-surface">{item.new_name}</span></p>{/if}
								</div>
								{#if item.choices.length > 1}
									<Select
										class="w-44 shrink-0"
										label="Do"
										value={choices[item.key]}
										onchange={(v) => (choices[item.key] = v as ImportChoice)}
										options={item.choices.map((c) => ({ value: c, label: choiceLabel(item, c) }))}
									/>
								{:else}
									<span class="shrink-0 body-small text-on-surface-variant">{choiceLabel(item, item.choices[0])}</span>
								{/if}
							</li>
						{/each}
					</ul>
				</section>
			{/each}

			{#if preview.people.matched.length || preview.people.missing.length}
				<section>
					<h3 class="mb-1.5 flex items-center gap-1.5 label-large text-on-surface-variant"><Icon name="group" size={18} />People</h3>
					<p class="body-small text-on-surface-variant">
						Matched by username: {preview.people.matched.length ? preview.people.matched.join(', ') : 'nobody'}.
					</p>
					{#if preview.people.missing.length}
						<p class="mt-1 body-small text-on-surface-variant">
							Not on this instance: {preview.people.missing.map((p) => p.display_name ? `${p.display_name} (${p.username})` : p.username).join(', ')}.
							{#if !createUsers}Their cards, comments and assignments come in without them.{/if}
						</p>
						{#if preview.people.can_create}
							<div class="mt-2"><Switch bind:checked={createUsers} label="Create them (an admin sets their passwords in Settings → Users)" /></div>
						{/if}
					{/if}
				</section>
			{/if}

			{#each preview.warnings as w, i (i)}
				<p class="flex items-start gap-1.5 body-small text-tertiary"><Icon name="info" size={16} class="mt-0.5 shrink-0" />{w}</p>
			{/each}
			{#if !preview.items.length}<p class="body-medium text-on-surface-variant">The file holds nothing this instance can import.</p>{/if}
		{/if}
		{#if error}<p class="body-small text-error">{error}</p>{/if}
	</div>
	{#snippet actions()}
		{#if preview}<Button variant="text" icon="arrow_back" class="mr-auto" onclick={() => (preview = null)}>Another file</Button>{/if}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		{#if preview}
			<Button icon="upload" loading={busy} disabled={!count && !createUsers} onclick={run}>Import{count ? ` ${count}` : ''}</Button>
		{/if}
	{/snippet}
</Dialog>
