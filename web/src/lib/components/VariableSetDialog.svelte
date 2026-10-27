<script lang="ts">
	import type { ProjectSummary, UUID, VarField, VariableKind, VariableSet } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Chip, Dialog, Icon, IconButton, Select, Switch, TextField } from '$lib/m3';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { bytes } from '$lib/format';
	import { fieldLabel, isFile, kindIcon } from '$lib/variables';

	interface Props {
		open: boolean;
		/** null: create a new set. */
		set: VariableSet | null;
		kinds: VariableKind[];
		projects: Pick<ProjectSummary, 'id' | 'codename' | 'name'>[];
		/** Preselected kind for a new set. */
		kindId?: string;
		/** Manual entry from a card or the Run dialog: kind fixed to `kindId`, and the values
		 *  are a one-off set unless the user chooses to save them to the library. */
		manual?: boolean;
		/** Who a one-off set belongs to, for the help text ("this card", "this run"). */
		scopeLabel?: string;
		onsaved: (set: VariableSet) => void;
		ondeleted?: (id: UUID) => void;
	}
	let {
		open = $bindable(false),
		set,
		kinds,
		projects,
		kindId = '',
		manual = false,
		scopeLabel = 'this card',
		onsaved,
		ondeleted
	}: Props = $props();

	// One-off values (manual entry, or editing a one-off set): no name or projects unless
	// the user saves them to the library.
	let oneOff = $derived(manual || !!set?.ephemeral);
	let saveToLibrary = $state(false);
	let showLibraryFields = $derived(!oneOff || saveToLibrary);

	let kind = $state('');
	let name = $state('');
	let description = $state('');
	let allProjects = $state(true);
	let projectIds = $state<UUID[]>([]);
	// Plain fields as typed text (booleans as booleans).
	let values = $state<Record<string, string | boolean>>({});
	// Secret fields: new value typed, or marked to clear. Revealed values are shown on demand.
	let secretInput = $state<Record<string, string>>({});
	let replacing = $state<Record<string, boolean>>({});
	let clearing = $state<Record<string, boolean>>({});
	let revealed = $state<Record<string, string>>({});
	let pendingFiles = $state<Record<string, File>>({});
	let removingFiles = $state<Record<string, boolean>>({});
	let error = $state<string | null>(null);
	let saving = $state(false);
	let openedFor = $state<string | null>(null);

	let spec = $derived(kinds.find((k) => k.id === kind) ?? null);

	// Lists are edited one item per line (the server also accepts commas).
	const asText = (v: unknown) => (v === undefined || v === null ? '' : Array.isArray(v) ? v.join('\n') : String(v));
	const listItems = (text: string | boolean | undefined) =>
		String(text ?? '')
			.split(/[,\n]/)
			.map((x) => x.trim())
			.filter(Boolean);
	function toggleItem(field: string, item: string) {
		const items = listItems(values[field]);
		values[field] = (items.includes(item) ? items.filter((x) => x !== item) : [...items, item]).join('\n');
	}

	$effect(() => {
		const key = open ? (set?.id ?? `new:${kindId}`) : null;
		if (key === openedFor) return;
		openedFor = key;
		if (!open) return;
		kind = set?.kind_id || kindId || (kinds.find((k) => k.id === 'credential') ?? kinds[0])?.id || '';
		name = set?.ephemeral ? '' : (set?.name ?? '');
		description = set?.description ?? '';
		saveToLibrary = false;
		allProjects = !set || set.project_ids.length === 0;
		projectIds = [...(set?.project_ids ?? [])];
		values = {};
		for (const f of kinds.find((k) => k.id === kind)?.fields ?? []) {
			if (f.type === 'boolean') values[f.name] = Boolean(set?.values[f.name] ?? f.default ?? false);
			else if (f.type !== 'secret' && !isFile(f.type)) values[f.name] = asText(set?.values[f.name] ?? (set ? '' : f.default));
		}
		secretInput = {};
		replacing = {};
		clearing = {};
		revealed = {};
		pendingFiles = {};
		removingFiles = {};
		error = null;
	});

	// Switching the kind of a new set resets its fields.
	function chooseKind(id: string) {
		kind = id;
		values = {};
		for (const f of kinds.find((k) => k.id === id)?.fields ?? []) {
			if (f.type === 'boolean') values[f.name] = Boolean(f.default ?? false);
			else if (f.type !== 'secret' && !isFile(f.type)) values[f.name] = asText(f.default);
		}
	}

	function toggleProject(id: UUID) {
		projectIds = projectIds.includes(id) ? projectIds.filter((p) => p !== id) : [...projectIds, id];
	}

	/** Only what changed: unchanged fields are left alone by the server. */
	function changedValues(): Record<string, unknown> {
		const out: Record<string, unknown> = {};
		for (const f of spec?.fields ?? []) {
			if (isFile(f.type)) continue;
			if (f.type === 'secret') {
				if (clearing[f.name]) out[f.name] = null;
				else if (secretInput[f.name]) out[f.name] = secretInput[f.name];
				continue;
			}
			const now = values[f.name];
			const before = f.type === 'boolean' ? Boolean(set?.values[f.name] ?? f.default ?? false) : asText(set?.values[f.name]);
			if (!set || now !== before) out[f.name] = now === '' ? null : now;
		}
		return out;
	}

	async function save() {
		saving = true;
		error = null;
		try {
			const project_ids = allProjects ? [] : projectIds;
			const values = changedValues();
			let saved: VariableSet;
			if (oneOff && !saveToLibrary) {
				saved = set
					? await api().variables.updateSet(set.id, { values })
					: await api().variables.createSet({ kind_id: kind, values, ephemeral: true });
			} else if (set?.ephemeral) {
				// Promote the one-off set: it joins the library and stays bound where it is.
				saved = await api().variables.updateSet(set.id, { ephemeral: false, name, description, project_ids, values });
			} else {
				saved = set
					? await api().variables.updateSet(set.id, { name, description, project_ids, values })
					: await api().variables.createSet({ kind_id: kind, name, description, project_ids, values });
			}
			for (const [field, file] of Object.entries(pendingFiles)) saved = await api().variables.uploadFile(saved.id, field, file);
			for (const field of Object.keys(removingFiles).filter((f) => removingFiles[f])) {
				saved = await api().variables.removeFile(saved.id, field);
			}
			onsaved(saved);
			if (saved.ephemeral) snackbar.show(`Values set for ${scopeLabel}`);
			else snackbar.show(set && !set.ephemeral ? `Saved “${saved.name}”` : `Added “${saved.name}” to Variables`);
			open = false;
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}

	async function remove() {
		if (!set) return;
		const ok = await confirmAction({
			title: `Delete “${set.name}”?`,
			message: 'Its values and files are deleted. Cards and playbooks that use it will fail to start until you choose another set.',
			confirmLabel: 'Delete set'
		});
		if (!ok) return;
		try {
			await api().variables.removeSet(set.id);
			ondeleted?.(set.id);
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete');
		}
	}

	async function reveal(f: VarField) {
		if (!set) return;
		try {
			const r = await api().variables.reveal(set.id, f.name);
			revealed[f.name] = String(r.value ?? '');
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not reveal');
		}
	}

	async function download(f: VarField) {
		if (!set) return;
		try {
			const r = await api().variables.reveal(set.id, f.name);
			const data = Uint8Array.from(atob(String(r.value)), (c) => c.charCodeAt(0));
			const url = URL.createObjectURL(new Blob([data]));
			const a = document.createElement('a');
			a.href = url;
			a.download = r.filename ?? f.name;
			a.click();
			URL.revokeObjectURL(url);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not download');
		}
	}

	async function copy(text: string) {
		await navigator.clipboard.writeText(text);
		snackbar.show('Copied');
	}

	function pickFile(field: string, e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		if (!file) return;
		if (file.size > 1024 * 1024) {
			snackbar.error(`${file.name} is ${bytes(file.size)}; files are limited to 1 MB`);
			return;
		}
		pendingFiles = { ...pendingFiles, [field]: file };
		removingFiles = { ...removingFiles, [field]: false };
	}
</script>

<Dialog
	bind:open
	title={oneOff ? `Manual entry · ${spec?.name ?? kind}` : set ? set.name : 'New variable set'}
	icon={kindIcon(kind)}
	size="md"
	initialFocus="dialog"
>
	<div class="flex flex-col gap-4 pt-2 pb-2">
		{#if error}
			<div class="rounded-md bg-error-container p-3 body-medium text-on-error-container">{error}</div>
		{/if}

		{#if oneOff}
			<p class="body-small text-on-surface-variant">
				Used only by {scopeLabel}: encrypted like any set, but not added to Variables and deleted with {scopeLabel}. Turn on
				<b>Save to Variables</b> to keep it for reuse.
			</p>
		{/if}
		{#if showLibraryFields}
			<div class="grid gap-3 sm:grid-cols-2">
				{#if set || oneOff}
					<TextField label="Kind" value={spec?.name ?? kind} readonly supporting={oneOff ? spec?.description : "A set's kind can't change"} />
				{:else}
					<Select label="Kind" value={kind} options={kinds.map((k) => ({ value: k.id, label: k.name }))} onchange={chooseKind} supporting={spec?.description}>
						{#snippet leading(id)}<Icon name={kindIcon(id)} size={20} />{/snippet}
					</Select>
				{/if}
				<TextField label="Name" bind:value={name} required placeholder="e.g. Corp domain admin" supporting="Unique; playbooks can refer to it by name" />
			</div>
			<TextField label="Description" bind:value={description} placeholder="What it's for, where it came from" />
		{/if}

		{#if spec}
			<div class="flex flex-col gap-3 rounded-md bg-surface-container p-4">
				<span class="title-small text-on-surface">Values</span>
				{#each spec.fields as f (f.name)}
					{@const label = fieldLabel(f) + (f.required ? ' *' : '')}
					<div class="flex flex-col gap-1">
						{#if f.type === 'boolean'}
							<div class="flex items-center justify-between gap-3">
								<div>
									<p class="body-medium text-on-surface">{label}</p>
									{#if f.description}<p class="body-small text-on-surface-variant">{f.description}</p>{/if}
								</div>
								<Switch checked={Boolean(values[f.name])} onchange={(v) => (values[f.name] = v)} />
							</div>
						{:else if f.type === 'secret'}
							{#if set?.secrets[f.name] && !replacing[f.name] && !clearing[f.name]}
								<div class="flex items-center gap-2 rounded-sm border border-outline-variant px-3 py-2">
									<Icon name="lock" size={18} class="text-on-surface-variant" />
									<div class="min-w-0 flex-1">
										<p class="label-medium text-on-surface-variant">{label}</p>
										{#if revealed[f.name] !== undefined}
											<p class="truncate font-mono body-medium text-on-surface">{revealed[f.name]}</p>
										{:else}
											<p class="font-mono body-medium text-on-surface">••••••••••</p>
										{/if}
									</div>
									{#if revealed[f.name] !== undefined}
										<IconButton icon="content_copy" label="Copy" size="sm" onclick={() => copy(revealed[f.name])} />
										<IconButton icon="visibility_off" label="Hide" size="sm" onclick={() => delete revealed[f.name]} />
									{:else}
										<IconButton icon="visibility" label="Reveal (recorded in the activity feed)" size="sm" onclick={() => reveal(f)} />
									{/if}
									<IconButton icon="edit" label="Replace" size="sm" onclick={() => (replacing[f.name] = true)} />
									{#if !f.required}<IconButton icon="backspace" label="Clear" size="sm" onclick={() => (clearing[f.name] = true)} />{/if}
								</div>
							{:else if clearing[f.name]}
								<div class="flex items-center gap-2 rounded-sm border border-dashed border-outline-variant px-3 py-2 body-medium text-on-surface-variant">
									<span class="flex-1">{fieldLabel(f)} will be cleared</span>
									<Button variant="text" onclick={() => (clearing[f.name] = false)}>Undo</Button>
								</div>
							{:else}
								<TextField
									{label}
									type="password"
									autocomplete="new-password"
									value={secretInput[f.name] ?? ''}
									oninput={(e) => (secretInput[f.name] = (e.currentTarget as HTMLInputElement).value)}
									supporting={[f.description, 'Encrypted at rest; jobs receive it as an environment variable'].filter(Boolean).join(' · ')}
									trailingIcon={set?.secrets[f.name] ? 'close' : undefined}
									ontrailing={() => { replacing[f.name] = false; delete secretInput[f.name]; }}
								/>
							{/if}
						{:else if isFile(f.type)}
							{@const stored = set?.files[f.name]}
							{@const pending = pendingFiles[f.name]}
							<div class="flex items-center gap-2 rounded-sm border border-outline-variant px-3 py-2">
								<Icon name={f.type === 'secret_file' ? 'encrypted' : 'draft'} size={18} class="text-on-surface-variant" />
								<div class="min-w-0 flex-1">
									<p class="label-medium text-on-surface-variant">{label}{f.type === 'secret_file' ? ' · secret file' : ''}</p>
									{#if pending}
										<p class="truncate body-medium text-primary">{pending.name} ({bytes(pending.size)}) · uploads on save</p>
									{:else if stored && !removingFiles[f.name]}
										<p class="truncate body-medium text-on-surface">{stored.filename} <span class="text-on-surface-variant">({bytes(stored.size)})</span></p>
									{:else if removingFiles[f.name]}
										<p class="body-medium text-on-surface-variant">Removed on save</p>
									{:else}
										<p class="body-medium text-on-surface-variant">No file</p>
									{/if}
									{#if f.description}<p class="body-small text-on-surface-variant">{f.description}</p>{/if}
								</div>
								{#if stored && !pending && !removingFiles[f.name]}
									<IconButton icon="download" label={f.type === 'secret_file' ? 'Download (recorded in the activity feed)' : 'Download'} size="sm" onclick={() => download(f)} />
									<IconButton icon="delete" label="Remove" size="sm" onclick={() => (removingFiles = { ...removingFiles, [f.name]: true })} />
								{/if}
								<label class="state-layer inline-flex h-9 cursor-pointer items-center gap-1 rounded-full px-3 label-large text-primary">
									<Icon name="upload_file" size={18} />{stored || pending ? 'Replace' : 'Choose file'}
									<input type="file" class="hidden" onchange={(e) => pickFile(f.name, e)} />
								</label>
							</div>
						{:else if f.type === 'text' && f.choices?.length}
							<Select
								{label}
								value={String(values[f.name] ?? '')}
								options={[{ value: '', label: f.required ? 'Choose…' : '(none)' }, ...f.choices.map((c) => ({ value: c, label: c }))]}
								supporting={f.description}
								onchange={(v) => (values[f.name] = v)}
							/>
						{:else if f.type === 'list' && f.choices?.length}
							{@const picked = listItems(values[f.name])}
							<div class="flex flex-col gap-1.5">
								<span class="label-medium text-on-surface-variant">{label}</span>
								<div class="flex flex-wrap gap-1.5">
									{#each f.choices as c (c)}
										<Chip variant="filter" selected={picked.includes(c)} onclick={() => toggleItem(f.name, c)}>{c}</Chip>
									{/each}
								</div>
								{#if f.description}<span class="body-small text-on-surface-variant">{f.description}</span>{/if}
							</div>
						{:else}
							<TextField
								{label}
								value={String(values[f.name] ?? '')}
								oninput={(e) => (values[f.name] = (e.currentTarget as HTMLInputElement).value)}
								type={f.type === 'port' || f.type === 'number' ? 'number' : 'text'}
								mono={f.type === 'host' || f.type === 'port' || f.type === 'list'}
								multiline={f.type === 'list'}
								rows={4}
								supporting={[f.description, f.type === 'list' ? 'One per line (commas work too)' : ''].filter(Boolean).join(' · ')}
							/>
						{/if}
					</div>
				{/each}
			</div>
		{/if}

		{#if oneOff}
			<div class="flex items-center justify-between gap-3">
				<div>
					<p class="body-medium text-on-surface">Save to Variables</p>
					<p class="body-small text-on-surface-variant">Off: these values are used only by {scopeLabel}.</p>
				</div>
				<Switch bind:checked={saveToLibrary} />
			</div>
		{/if}

		{#if showLibraryFields}
		<div class="flex flex-col gap-2">
			<div class="flex items-center justify-between gap-3">
				<div>
					<p class="body-medium text-on-surface">Usable in every project</p>
					<p class="body-small text-on-surface-variant">Turn off to limit this set to specific projects, e.g. one client's credentials.</p>
				</div>
				<Switch bind:checked={allProjects} />
			</div>
			{#if !allProjects}
				<div class="flex flex-wrap gap-1.5">
					{#each projects as p (p.id)}
						<Chip variant="filter" selected={projectIds.includes(p.id)} onclick={() => toggleProject(p.id)}>{p.codename}</Chip>
					{:else}
						<p class="body-small text-on-surface-variant">No projects yet.</p>
					{/each}
				</div>
			{/if}
		</div>
		{/if}
	</div>
	{#snippet actions()}
		{#if set && !oneOff}
			<Button variant="text" icon="delete" class="mr-auto text-error" onclick={remove}>Delete</Button>
		{/if}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="check" loading={saving} disabled={(showLibraryFields && !name.trim()) || !kind} onclick={save}>
			{oneOff && !saveToLibrary ? 'Use these values' : set && !set.ephemeral ? 'Save' : oneOff ? 'Save to Variables' : 'Add set'}
		</Button>
	{/snippet}
</Dialog>
