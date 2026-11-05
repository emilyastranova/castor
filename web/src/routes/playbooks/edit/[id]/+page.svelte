<script lang="ts">
	import { beforeNavigate, goto } from '$app/navigation';
	import type { PlaybookSpec, PlaybookStepSpec } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon, IconButton, Tabs, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import PlaybookVisualizer from '$lib/components/PlaybookVisualizer.svelte';
	import StepEditor from '$lib/components/editor/StepEditor.svelte';
	import PlaybookSettings from '$lib/components/editor/PlaybookSettings.svelte';
	import { BLOCK_MIME } from '$lib/playbook';
	import {
		connectOptions,
		deleteStep,
		disconnect,
		errorsByStep,
		forDrawing,
		newCommandStep,
		newTransformStep,
		renameStep,
		toAuthoring,
		type ConnectOption
	} from '$lib/playbookEditor';

	let { data } = $props();

	const commandsById = $derived(new Map(data.commands.map((c) => [c.id, c])));

	function initialSpec(): PlaybookSpec {
		const pb = data.playbook;
		if (!pb) return { id: 'new-playbook', name: 'New playbook', schema_version: 1, description: '', tags: [], inputs: [], steps: [] };
		const spec = toAuthoring(pb.spec);
		// Built-ins are read-only, and ?from= makes a copy: either way this saves as a new one.
		if (data.isNew) return { ...spec, id: `${pb.id}-copy`.slice(0, 63), name: `${pb.name} (copy)` };
		return spec;
	}

	// svelte-ignore state_referenced_locally
	let spec = $state<PlaybookSpec>(initialSpec());
	// svelte-ignore state_referenced_locally
	let savedId = $state<string | null>(data.isNew ? null : (data.playbook?.id ?? null));
	let dirty = $state(false);
	let saving = $state(false);
	let selectedId = $state<string | null>(null);
	let tab = $state('visual');
	let viz: PlaybookVisualizer | undefined = $state();
	let paletteQuery = $state('');

	/** A plain copy of the spec (reading it all also makes an effect track every field). */
	const plain = (): PlaybookSpec => JSON.parse(JSON.stringify(spec));

	// Server check: YAML rendering + validation, debounced after every change.
	let yaml = $state('');
	let errors = $state<string[]>([]);
	let checking = $state(false);
	let checkTimer: ReturnType<typeof setTimeout> | undefined;
	let checkSeq = 0;
	$effect(() => {
		const snapshot = plain();
		clearTimeout(checkTimer);
		checking = true;
		checkTimer = setTimeout(async () => {
			const seq = ++checkSeq;
			try {
				const res = await api().playbooks.toYaml(snapshot);
				if (seq !== checkSeq) return;
				yaml = res.yaml;
				errors = res.errors;
				if (!yamlDirty) yamlDraft = res.yaml;
			} catch (err) {
				if (seq === checkSeq) errors = [err instanceof ApiError ? err.message : 'Could not check the playbook'];
			} finally {
				if (seq === checkSeq) checking = false;
			}
		}, 350);
	});

	let problems = $derived(errorsByStep(errors, spec));
	let drawn = $derived(forDrawing(spec));
	let selected = $derived(spec.steps.find((st) => st.id === selectedId) ?? null);
	let hasComments = $derived(!!data.playbook && !data.isNew && /^\s*#/m.test(data.playbook.source_yaml));

	function update(next: PlaybookSpec) {
		spec = next;
		dirty = true;
	}

	function updateStep(step: PlaybookStepSpec) {
		update({ ...spec, steps: spec.steps.map((st) => (st.id === step.id ? step : st)) });
	}

	/** Add a step where it was dropped, or (clicked in the palette) to the right of the
	 *  selected step, or the last one added. Everything else keeps its place. */
	function addStep(step: PlaybookStepSpec, at?: { x: number; y: number }) {
		const frozen = { ...(viz?.positions() ?? {}), ...spec.layout };
		const anchor = frozen[selectedId ?? ''] ?? frozen[spec.steps.at(-1)?.id ?? ''];
		let pos: { x: number; y: number };
		if (at) pos = { x: at.x - 130, y: at.y - 50 };
		else if (anchor) {
			pos = { x: anchor.x + 410, y: anchor.y };
			// Step down until the spot is free.
			while (Object.values(frozen).some((p) => Math.abs(p.x - pos.x) < 200 && Math.abs(p.y - pos.y) < 100)) pos = { ...pos, y: pos.y + 140 };
		} else {
			const c = viz?.visibleCenter() ?? { x: 200, y: 150 };
			pos = { x: c.x - 130, y: c.y - 50 };
		}
		update({ ...spec, steps: [...spec.steps, step], layout: { ...frozen, [step.id]: { x: Math.round(pos.x), y: Math.round(pos.y) } } });
		selectedId = step.id;
		if (!at) setTimeout(() => viz?.fitView(), 30);
	}

	function addBlock(data: string, at?: { x: number; y: number }) {
		if (data === 'transform') return addStep(newTransformStep(spec), at);
		const cmd = commandsById.get(data.replace(/^command:/, ''));
		if (cmd) addStep(newCommandStep(spec, cmd), at);
	}

	function removeSelected() {
		if (!selectedId) return;
		const name = selected?.name;
		update(deleteStep(spec, selectedId));
		selectedId = null;
		snackbar.show(`Deleted “${name}”`);
	}

	// Dropping a connection opens a menu of what it can mean.
	let connectMenu = $state<{ x: number; y: number; title: string; options: ConnectOption[] } | null>(null);
	function onConnect(fromId: string, toId: string, at: { x: number; y: number }) {
		const options = connectOptions(spec, fromId, toId, commandsById);
		const from = spec.steps.find((st) => st.id === fromId)?.name;
		const to = spec.steps.find((st) => st.id === toId)?.name;
		connectMenu = { x: Math.min(at.x, window.innerWidth - 340), y: Math.min(at.y, window.innerHeight - 360), title: `${from} → ${to}`, options };
	}
	function choose(option: ConnectOption) {
		update(option.apply(spec));
		connectMenu = null;
	}
	function onEdgeDelete(fromId: string, toId: string) {
		const res = disconnect(spec, fromId, toId);
		update(res.spec);
		if (res.conditionLeft) snackbar.show(`“${toId}” still waits for ${fromId}: its condition refers to it`);
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape') connectMenu = null;
		const target = e.target as HTMLElement;
		if (tab !== 'visual' || target.closest('input, textarea, select, [contenteditable], [role="listbox"]')) return;
		if ((e.key === 'Delete' || e.key === 'Backspace') && selectedId) {
			e.preventDefault();
			removeSelected();
		}
	}

	// ---------- YAML tab ----------
	let yamlDraft = $state('');
	let yamlDirty = $state(false);
	let yamlErrors = $state<string[]>([]);
	async function applyYaml() {
		const res = await api().playbooks.validate(yamlDraft);
		if (!res.spec) {
			yamlErrors = res.errors;
			return;
		}
		yamlErrors = [];
		yamlDirty = false;
		update(toAuthoring(res.spec));
		if (selectedId && !res.spec.steps.some((st) => st.id === selectedId)) selectedId = null;
		snackbar.show(res.ok ? 'Applied' : `Applied, with ${res.errors.length} problem${res.errors.length === 1 ? '' : 's'}`);
	}

	// ---------- save ----------
	async function save() {
		saving = true;
		try {
			const res = await api().playbooks.toYaml(plain());
			if (!res.ok) {
				errors = res.errors;
				snackbar.error('Fix the problems before saving');
				return;
			}
			if (savedId) await api().playbooks.update(savedId, res.yaml);
			else {
				await api().playbooks.create(res.yaml);
				savedId = spec.id;
			}
			dirty = false;
			snackbar.show(`Saved “${spec.name}”`);
			if (location.pathname !== `/playbooks/edit/${spec.id}`) await goto(`/playbooks/edit/${spec.id}`, { replaceState: true });
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save');
		} finally {
			saving = false;
		}
	}

	beforeNavigate(({ cancel, to }) => {
		if (dirty && to?.url.pathname !== `/playbooks/edit/${spec.id}` && !confirm('Leave without saving? Your changes will be lost.')) cancel();
	});

	let palette = $derived(
		data.commands.filter((c) => {
			const q = paletteQuery.trim().toLowerCase();
			return !q || c.name.toLowerCase().includes(q) || c.id.includes(q) || c.tags.some((t) => t.includes(q));
		})
	);
</script>

<svelte:window onkeydown={onKey} onbeforeunload={(e) => dirty && e.preventDefault()} />
<svelte:head><title>{spec.name || 'Playbook'} · Editor · Castor</title></svelte:head>

<div class="flex h-full min-h-0 flex-col">
	<header class="flex items-center gap-3 border-b border-outline-variant px-4 py-2.5">
		<IconButton icon="arrow_back" label="Back to playbooks" onclick={() => goto('/playbooks')} />
		<div class="min-w-0">
			<h1 class="truncate title-large text-on-surface">{spec.name || 'Untitled playbook'}</h1>
			<p class="truncate body-small text-on-surface-variant">
				{savedId ? 'Editing' : data.playbook?.origin === 'builtin' ? 'Copy of a built-in playbook' : 'New playbook'} · <span class="font-mono">{spec.id}</span>
			</p>
		</div>
		<span class="ml-2 flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 label-medium {errors.length ? 'bg-error-container text-on-error-container' : 'bg-surface-container-high text-on-surface-variant'}">
			{#if checking}
				<Icon name="progress_activity" size={16} class="animate-spin" />Checking
			{:else if errors.length}
				<Icon name="error" size={16} />{errors.length} problem{errors.length === 1 ? '' : 's'}
			{:else}
				<Icon name="check_circle" size={16} class="text-primary" />Valid
			{/if}
		</span>
		<div class="ml-auto flex items-center gap-2">
			<Tabs
				tabs={[
					{ id: 'visual', label: 'Visual', icon: 'account_tree' },
					{ id: 'yaml', label: 'YAML', icon: 'code' }
				]}
				bind:value={tab}
				class="border-b-0"
			/>
			<Button icon="save" loading={saving} disabled={!dirty && !!savedId} onclick={save}>{savedId ? 'Save' : 'Create'}</Button>
		</div>
	</header>

	{#if hasComments || data.playbook?.origin === 'builtin'}
		<div class="flex items-center gap-2 border-b border-outline-variant bg-tertiary-container/30 px-4 py-1.5 body-small text-on-surface">
			<Icon name="info" size={16} class="text-tertiary" />
			{#if data.playbook?.origin === 'builtin'}
				Built-in playbooks are read-only, so this saves as a new playbook.
			{:else}
				This playbook's YAML has comments. Saving from the editor rewrites the YAML, so they'll be lost.
			{/if}
		</div>
	{/if}

	{#if tab === 'visual'}
		<div class="grid min-h-0 flex-1 grid-cols-[15rem_minmax(0,1fr)_24rem]">
			<!-- palette -->
			<aside class="flex min-h-0 flex-col gap-2 border-r border-outline-variant p-3">
				<p class="label-large text-on-surface">Add a step</p>
				<p class="body-small text-on-surface-variant">Drag onto the canvas, or click to add.</p>
				<button
					type="button"
					draggable="true"
					class="flex items-center gap-2 rounded-lg border border-dashed border-tertiary/70 bg-surface-container-low px-3 py-2 text-left hover:bg-surface-container"
					ondragstart={(e) => e.dataTransfer?.setData(BLOCK_MIME, 'transform')}
					onclick={() => addBlock('transform')}
				>
					<Icon name="filter_alt" size={18} class="text-tertiary" />
					<span class="min-w-0">
						<span class="block title-small text-on-surface">Transform</span>
						<span class="block body-small text-on-surface-variant">Filter or reshape results</span>
					</span>
				</button>
				<label class="mt-1 flex items-center gap-2 rounded-full bg-surface-container-high px-3">
					<Icon name="search" size={18} class="text-on-surface-variant" />
					<input class="h-9 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Search commands" bind:value={paletteQuery} />
				</label>
				<ul class="-mr-1 flex min-h-0 flex-1 flex-col gap-1.5 overflow-auto pr-1">
					{#each palette as c (c.id)}
						<li>
							<button
								type="button"
								draggable="true"
								class="flex w-full items-start gap-2 rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-left hover:border-outline hover:bg-surface-container"
								title={c.spec.description}
								ondragstart={(e) => e.dataTransfer?.setData(BLOCK_MIME, `command:${c.id}`)}
								onclick={() => addBlock(`command:${c.id}`)}
							>
								<Icon name="terminal" size={18} class="mt-0.5 shrink-0 text-on-surface-variant" />
								<span class="min-w-0">
									<span class="block truncate title-small text-on-surface">{c.name}</span>
									<span class="block truncate font-mono text-[11px] text-on-surface-variant">{c.id}</span>
								</span>
							</button>
						</li>
					{:else}
						<li class="px-2 py-4 text-center body-small text-on-surface-variant">No command matches.</li>
					{/each}
				</ul>
			</aside>

			<!-- canvas -->
			<section class="relative min-h-0 p-3">
				<PlaybookVisualizer
					bind:this={viz}
					spec={drawn}
					commands={data.commands}
					editable
					fill
					errors={problems.byStep}
					selectedStepId={selectedId}
					onStepSelect={(st) => (selectedId = st.id)}
					oncanvasclick={() => (selectedId = null)}
					onconnect={onConnect}
					onedgedelete={onEdgeDelete}
					onlayout={(positions) => update({ ...spec, layout: positions })}
					ondropblock={addBlock}
				/>
				{#if !spec.steps.length}
					<div class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-center text-on-surface-variant">
						<Icon name="add_box" size={40} class="opacity-50" />
						<p class="title-medium text-on-surface">Start by adding a step</p>
						<p class="max-w-sm body-small">Drag commands from the left. Connect steps by dragging from a step's <Icon name="add" size={14} /> handle onto another step.</p>
					</div>
				{/if}
			</section>

			<!-- properties -->
			<aside class="min-h-0 overflow-auto border-l border-outline-variant p-4">
				{#if selected}
					{#key selected.id}
						<StepEditor
							{spec}
							step={selected}
							commands={commandsById}
							errors={problems.byStep[selected.id] ?? []}
							onchange={updateStep}
							onrename={(from, to) => {
								update(renameStep(spec, from, to));
								selectedId = to;
							}}
							ondelete={removeSelected}
						/>
					{/key}
				{:else}
					<PlaybookSettings {spec} idEditable={!savedId} kinds={data.kinds} errors={problems.general} onchange={update} />
				{/if}
			</aside>
		</div>
	{:else}
		<div class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_22rem] gap-4 p-4">
			<div class="flex min-h-0 flex-col gap-2">
				<textarea
					class="min-h-0 flex-1 resize-none rounded-md border border-outline-variant bg-surface-container-lowest p-3 font-mono text-[12.5px] leading-5 text-on-surface outline-none focus:border-primary"
					spellcheck="false"
					bind:value={yamlDraft}
					oninput={() => (yamlDirty = true)}
				></textarea>
				<div class="flex items-center gap-2">
					<span class="flex-1 body-small text-on-surface-variant">
						{yamlDirty ? 'Edited here: apply to update the visual editor.' : 'This is what saving writes. Edit it here and apply, or keep working visually.'}
					</span>
					{#if yamlDirty}
						<Button variant="text" onclick={() => { yamlDraft = yaml; yamlDirty = false; yamlErrors = []; }}>Discard</Button>
					{/if}
					<Button variant="tonal" icon="check" disabled={!yamlDirty} onclick={applyYaml}>Apply</Button>
				</div>
			</div>
			<aside class="flex min-h-0 flex-col gap-2 overflow-auto">
				<p class="label-large text-on-surface">Problems</p>
				{#each [...yamlErrors, ...(yamlErrors.length ? [] : errors)] as e (e)}
					<p class="flex gap-1.5 rounded-md bg-error-container/40 px-3 py-2 body-small text-on-surface"><Icon name="error" size={16} class="mt-px shrink-0 text-error" />{e}</p>
				{:else}
					<p class="body-small text-on-surface-variant">None. See docs/PLAYBOOK_SPEC.md for the format.</p>
				{/each}
			</aside>
		</div>
	{/if}
</div>

{#if connectMenu}
	<!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
	<div class="fixed inset-0 z-50" onclick={() => (connectMenu = null)}>
		<div
			class="absolute flex max-h-[22rem] w-80 flex-col overflow-hidden rounded-lg bg-surface-container shadow-e3"
			style="left: {connectMenu.x}px; top: {connectMenu.y}px;"
			role="menu"
			tabindex="-1"
			onclick={(e) => e.stopPropagation()}
		>
			<p class="truncate border-b border-outline-variant px-4 py-2.5 title-small text-on-surface">{connectMenu.title}</p>
			<ul class="overflow-auto py-1">
				{#each connectMenu.options as o (o.label)}
					<li>
						<button type="button" role="menuitem" class="state-layer flex w-full items-start gap-3 px-4 py-2 text-left" onclick={() => choose(o)}>
							<Icon name={o.icon} size={18} class="mt-0.5 shrink-0 text-on-surface-variant" />
							<span class="min-w-0">
								<span class="block body-medium text-on-surface">{o.label}</span>
								{#if o.detail}<span class="block truncate body-small text-on-surface-variant">{o.detail}</span>{/if}
							</span>
						</button>
					</li>
				{/each}
			</ul>
		</div>
	</div>
{/if}
