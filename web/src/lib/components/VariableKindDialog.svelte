<script lang="ts">
	import { readOnlyOrigin } from '$lib/origin';
	import AiGenerateDialog from './AiGenerateDialog.svelte';
	import { aiStatus } from '$lib/ai.svelte';
	import type { VarField, VarFieldType, VariableKind } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, IconButton, Select, Switch, TextField } from '$lib/m3';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { FIELD_TYPES, envName, kindIcon } from '$lib/variables';

	interface Props {
		open: boolean;
		/** null: create a new kind. Built-in kinds open read-only. */
		kind: VariableKind | null;
		onsaved: (kind: VariableKind) => void;
		ondeleted: (id: string) => void;
	}
	let { open = $bindable(false), kind, onsaved, ondeleted }: Props = $props();

	// choices is edited as text, one per line or comma separated.
	type Row = { name: string; type: VarFieldType; label: string; description: string; required: boolean; choices: string };

	let id = $state('');
	let name = $state('');
	let description = $state('');
	let rows = $state<Row[]>([]);
	let error = $state<string | null>(null);
	let saving = $state(false);
	let openedFor = $state<string | null>(null);
	let readOnly = $derived(readOnlyOrigin(kind?.origin));
	let aiOpen = $state(false);
	$effect(() => void aiStatus.load());
	/** The form as JSON (which is YAML), for the AI to change. */
	let asYaml = $derived(JSON.stringify({ id, name, description, fields: rows.map((r) => ({ name: r.name, type: r.type, label: r.label, description: r.description, required: r.required, ...(r.choices.trim() ? { choices: r.choices.split(/[\n,]/).map((c) => c.trim()).filter(Boolean) } : {}) })) }, null, 1));
	function useDraft(spec: Record<string, unknown> | null) {
		if (!spec) return;
		if (!kind) id = String(spec.id ?? id);
		name = String(spec.name ?? name);
		description = String(spec.description ?? '');
		rows = ((spec.fields as VarField[]) ?? []).map((f) => ({ name: f.name, type: f.type, label: f.label ?? '', description: f.description ?? '', required: !!f.required, choices: (f.choices ?? []).join('\n') }));
	}

	const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 63);

	$effect(() => {
		const key = open ? (kind?.id ?? 'new') : null;
		if (key === openedFor) return;
		openedFor = key;
		if (!open) return;
		id = kind?.id ?? '';
		name = kind?.name ?? '';
		description = kind?.description ?? '';
		rows = (kind?.fields ?? [{ name: 'host', type: 'host', label: '', description: '', required: true, default: null }]).map(
			(f: VarField) => ({
				name: f.name,
				type: f.type,
				label: f.label,
				description: f.description,
				required: f.required,
				choices: (f.choices ?? []).join('\n')
			})
		);
		error = null;
	});

	function duplicate() {
		if (!kind) return;
		kind = null;
		openedFor = 'new';
		id = `${id}-custom`;
		name = `${name} (custom)`;
	}

	async function save() {
		saving = true;
		error = null;
		const body = {
			id,
			name,
			description,
			fields: rows.map((r) => {
				const choices = r.choices.split(/[,\n]/).map((c) => c.trim()).filter(Boolean);
				return { ...r, default: null, choices: (r.type === 'text' || r.type === 'list') && choices.length ? choices : null };
			})
		};
		try {
			const saved = kind ? await api().variables.updateKind(kind.id, body) : await api().variables.createKind(body);
			onsaved(saved);
			snackbar.show(kind ? `Saved “${saved.name}”` : `Added “${saved.name}”`);
			open = false;
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}

	async function remove() {
		if (!kind) return;
		const ok = await confirmAction({ title: `Delete kind “${kind.name}”?`, message: 'Only kinds with no sets can be deleted.', confirmLabel: 'Delete kind' });
		if (!ok) return;
		try {
			await api().variables.removeKind(kind.id);
			ondeleted(kind.id);
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete');
		}
	}
</script>

<Dialog bind:open title={kind ? kind.name : 'New variable kind'} icon={kindIcon(kind?.id ?? '')} size="lg" initialFocus="dialog">
	<div class="flex flex-col gap-4 pt-2 pb-2">
		{#if readOnly}
			<p class="body-small text-on-surface-variant">Built-in kinds are read-only. Duplicate one to make your own version.</p>
		{/if}
		{#if error}
			<div class="rounded-md bg-error-container p-3 body-medium text-on-error-container">{error}</div>
		{/if}
		<div class="grid gap-3 sm:grid-cols-2">
			<TextField
				label="Name"
				bind:value={name}
				readonly={readOnly}
				oninput={() => { if (!kind) id = slug(name); }}
				placeholder="e.g. Socket test"
			/>
			<TextField label="ID" bind:value={id} mono readonly={readOnly || !!kind} supporting="Commands ask for a kind by this id" />
		</div>
		<TextField label="Description" bind:value={description} readonly={readOnly} />

		<div class="flex flex-col gap-2">
			<span class="title-small text-on-surface">Fields</span>
			{#each rows as row, i (i)}
				<div class="grid items-start gap-2 rounded-md bg-surface-container p-3 sm:grid-cols-[1fr_160px_1fr_auto_auto]">
					<TextField label="Name" bind:value={row.name} mono readonly={readOnly} supporting={row.name ? `{{ slot.${row.name} }} · ${envName('slot', row.name)}` : 'lowercase_with_underscores'} />
					{#if readOnly}
						<TextField label="Type" value={FIELD_TYPES.find((t) => t.value === row.type)?.label ?? row.type} readonly />
					{:else}
						<Select label="Type" value={row.type} options={FIELD_TYPES} onchange={(v) => (row.type = v as VarFieldType)} />
					{/if}
					<TextField label="Label (optional)" bind:value={row.label} readonly={readOnly} />
					<div class="flex h-14 items-center gap-2">
						<Switch bind:checked={row.required} disabled={readOnly} />
						<span class="body-small text-on-surface-variant">Required</span>
					</div>
					{#if !readOnly}
						<div class="flex h-14 items-center">
							<IconButton icon="delete" label="Remove field" size="sm" disabled={rows.length === 1} onclick={() => (rows = rows.filter((_, j) => j !== i))} />
						</div>
					{/if}
					{#if row.type === 'text' || row.type === 'list'}
						<div class="sm:col-span-5">
							<TextField
								label="Choices (optional)"
								bind:value={row.choices}
								readonly={readOnly}
								multiline
								rows={2}
								mono
								supporting={row.type === 'text'
									? 'One per line: the set editor shows a dropdown of these'
									: 'One per line: the set editor shows these as toggles'}
							/>
						</div>
					{/if}
				</div>
			{/each}
			{#if !readOnly}
				<Button variant="text" icon="add" class="self-start" onclick={() => (rows = [...rows, { name: '', type: 'text', label: '', description: '', required: false, choices: '' }])}>Add field</Button>
			{/if}
		</div>
	</div>
	{#snippet actions()}
		{#if kind && !readOnly}
			<Button variant="text" icon="delete" class="mr-auto text-error!" onclick={remove}>Delete</Button>
		{/if}
		{#if !readOnly && aiStatus.enabled}<Button variant="text" icon="auto_awesome" onclick={() => (aiOpen = true)}>AI</Button>{/if}
		<Button variant="text" onclick={() => (open = false)}>{readOnly ? 'Close' : 'Cancel'}</Button>
		{#if readOnly}
			<Button variant="tonal" icon="content_copy" onclick={duplicate}>Duplicate</Button>
		{:else}
			<Button icon="check" loading={saving} disabled={!id || !name.trim()} onclick={save}>{kind ? 'Save' : 'Add kind'}</Button>
		{/if}
	{/snippet}
</Dialog>

<AiGenerateDialog bind:open={aiOpen} kind="variable_kind" current={rows.length && name ? asYaml : ''} onuse={(d) => useDraft(d.spec)} />
