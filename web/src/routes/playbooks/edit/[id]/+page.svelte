<script lang="ts">
	import { pluginOf, readOnlyOrigin } from '$lib/origin';
	import { beforeNavigate, goto } from '$app/navigation';
	import { tick } from 'svelte';
	import type { PlaybookSpec, PlaybookStepSpec } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, IconButton, Tabs, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import PlaybookVisualizer from '$lib/components/PlaybookVisualizer.svelte';
	import StepEditor from '$lib/components/editor/StepEditor.svelte';
	import PlaybookSettings from '$lib/components/editor/PlaybookSettings.svelte';
	import StepPalette from '$lib/components/editor/StepPalette.svelte';
	import {
		bindToInputs,
		connectOptions,
		deleteStep,
		disconnect,
		duplicateStep,
		errorsByStep,
		forDrawing,
		newCommandStep,
		newTransformStep,
		renameStep,
		toAuthoring,
		wireStep,
		wireTransform,
		wiringFor,
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
	let palette: StepPalette | undefined = $state();

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

	// ---------- undo / redo ----------
	// Every change goes through update(), which keeps the spec as it was before. Edits in one
	// form in quick succession (typing in a field) are one step, so undo doesn't go a letter at a time.
	// svelte-ignore state_referenced_locally
	let savedSnapshot = JSON.stringify(spec);
	let past = $state<string[]>([]);
	let future = $state<string[]>([]);
	let lastGroup: string | null = null;
	let lastAt = 0;
	const HISTORY = 100;

	function update(next: PlaybookSpec, group: string | null = null) {
		const now = Date.now();
		if (!(group && group === lastGroup && now - lastAt < 1500)) past = [...past.slice(1 - HISTORY), JSON.stringify(spec)];
		lastGroup = group;
		lastAt = now;
		future = [];
		spec = next;
		dirty = true;
	}
	function travel(from: string[], to: string[]): [string[], string[]] | null {
		const prev = from.at(-1);
		if (prev === undefined) return null;
		to = [...to, JSON.stringify(spec)];
		spec = JSON.parse(prev);
		lastGroup = null;
		dirty = JSON.stringify(spec) !== savedSnapshot;
		if (selectedId && !spec.steps.some((st) => st.id === selectedId)) selectedId = null;
		return [from.slice(0, -1), to];
	}
	function undo() {
		const r = travel(past, future);
		if (r) [past, future] = r;
		else snackbar.show('Nothing to undo');
	}
	function redo() {
		const r = travel(future, past);
		if (r) [future, past] = r;
		else snackbar.show('Nothing to redo');
	}

	function updateStep(step: PlaybookStepSpec) {
		update({ ...spec, steps: spec.steps.map((st) => (st.id === step.id ? step : st)) }, `step:${step.id}`);
	}

	/** Add a step where it was dropped, or (clicked in the palette) to the right of the
	 *  selected step, or the last one added. Everything else keeps its place, and the view pans
	 *  to the new step rather than zooming out to fit them all. */
	function addStep(step: PlaybookStepSpec, at?: { x: number; y: number }, base: PlaybookSpec = spec) {
		const frozen = { ...(viz?.positions() ?? {}), ...base.layout };
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
		update({ ...base, steps: [...base.steps, step], layout: { ...frozen, [step.id]: { x: Math.round(pos.x), y: Math.round(pos.y) } } });
		selectedId = step.id;
		if (!at) setTimeout(() => viz?.reveal(step.id), 30);
	}

	/** `connect`: after the selected step, fed by its output (as the palette suggested). */
	function addBlock(data: string, at?: { x: number; y: number }, connect = false) {
		const from = connect ? selected : null;
		if (data === 'transform') {
			const step = newTransformStep(spec);
			return addStep(from ? wireTransform(step, from, commandsById) : step, at);
		}
		const cmd = commandsById.get(data.replace(/^command:/, ''));
		if (!cmd) return;
		const step = newCommandStep(spec, cmd);
		const wiring = from ? wiringFor(from, cmd, commandsById) : null;
		if (from && wiring) return addStep(wireStep(step, from.id, wiring), at);
		// Nothing feeds it: its required targets (and the like) come from the run's inputs.
		const bound = bindToInputs(spec, step, cmd);
		addStep(bound.step, at, bound.spec);
		const added = (bound.spec.inputs ?? []).filter((i) => !(spec.inputs ?? []).some((x) => x.name === i.name)).map((i) => i.name);
		if (added.length) snackbar.show(`Added playbook input${added.length === 1 ? '' : 's'} ${added.join(', ')}: a run asks for ${added.length === 1 ? 'it' : 'them'}`);
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

	function duplicateSelected() {
		if (!selectedId) return;
		const copy = duplicateStep(spec, selectedId);
		if (!copy) return;
		const frozen = { ...(viz?.positions() ?? {}), ...spec.layout };
		const from = frozen[selectedId] ?? { x: 0, y: 0 };
		let pos = { x: from.x, y: from.y + 140 };
		while (Object.values(frozen).some((p) => Math.abs(p.x - pos.x) < 200 && Math.abs(p.y - pos.y) < 100)) pos = { ...pos, y: pos.y + 140 };
		update({ ...spec, steps: [...spec.steps, copy], layout: { ...frozen, [copy.id]: pos } });
		selectedId = copy.id;
		setTimeout(() => viz?.reveal(copy.id), 30);
	}

	/** Arrow keys walk the graph: ← what the step reads from, → what reads from it, ↑↓ the
	 *  nearest step above or below. With nothing selected, any arrow picks the first step. */
	function walk(dir: 'left' | 'right' | 'up' | 'down') {
		const pos = viz?.positions() ?? {};
		const steps = drawn.steps;
		if (!steps.length) return;
		const at = (id: string) => pos[id] ?? { x: 0, y: 0 };
		let next: string | undefined;
		if (!selectedId || !pos[selectedId]) {
			next = [...steps].sort((a, b) => at(a.id).x - at(b.id).x || at(a.id).y - at(b.id).y)[0]?.id;
		} else {
			const cur = at(selectedId);
			const me = steps.find((st) => st.id === selectedId);
			let pool =
				dir === 'right'
					? steps.filter((st) => st.depends_on?.includes(selectedId!))
					: dir === 'left'
						? steps.filter((st) => me?.depends_on?.includes(st.id))
						: steps.filter((st) => st.id !== selectedId && Math.abs(at(st.id).x - cur.x) < 200 && (dir === 'up' ? at(st.id).y < cur.y : at(st.id).y > cur.y));
			// No connection that way: the nearest step in that direction on the canvas.
			if (!pool.length && (dir === 'left' || dir === 'right'))
				pool = steps.filter((st) => (dir === 'right' ? at(st.id).x > cur.x + 20 : at(st.id).x < cur.x - 20));
			const dist = (id: string) => Math.hypot(at(id).x - cur.x, (at(id).y - cur.y) * 1.5);
			next = pool.sort((a, b) => dist(a.id) - dist(b.id))[0]?.id;
		}
		if (next) {
			selectedId = next;
			viz?.reveal(next);
		}
	}

	// Side panels fold away to give the canvas room ([ and ]); remembered in this browser.
	let panels = $state(loadPanels());
	function loadPanels(): { left: boolean; right: boolean } {
		try {
			return { left: true, right: true, ...JSON.parse(localStorage.getItem('castor.editor.panels') ?? '{}') };
		} catch {
			return { left: true, right: true };
		}
	}
	function togglePanel(side: 'left' | 'right', open = !panels[side]) {
		panels = { ...panels, [side]: open };
		try {
			localStorage.setItem('castor.editor.panels', JSON.stringify(panels));
		} catch {
			/* only for this tab */
		}
	}
	async function openPalette() {
		if (!panels.left) togglePanel('left', true);
		await tick();
		palette?.focusSearch();
	}

	let shortcutsOpen = $state(false);
	const SHORTCUTS: [string[], string][] = [
		[['/'], 'Search the steps to add'],
		[['Enter'], 'In the list: add after the selected step, connected'],
		[['Shift', 'Enter'], 'In the list: add on its own'],
		[['icon:arrow_back', 'icon:arrow_forward'], 'Select what the step reads from, or what reads from it'],
		[['icon:arrow_upward', 'icon:arrow_downward'], 'Select the step above or below'],
		[['Esc'], 'Deselect (the playbook’s settings show)'],
		[['Ctrl', 'D'], 'Duplicate the selected step'],
		[['Delete'], 'Delete the selected step'],
		[['Ctrl', 'Z'], 'Undo'],
		[['Ctrl', 'Shift', 'Z'], 'Redo'],
		[['Ctrl', 'S'], 'Save'],
		[['F'], 'Fit the graph in view'],
		[['['], 'Show or hide the step list'],
		[[']'], 'Show or hide the step’s settings'],
		[['?'], 'This list']
	];
	function onKey(e: KeyboardEvent) {
		const target = e.target as HTMLElement;
		const mod = e.ctrlKey || e.metaKey;
		const key = e.key.toLowerCase();
		if (e.key === 'Escape' && connectMenu) {
			connectMenu = null;
			return;
		}
		// Save works everywhere, even while typing in a field.
		if (mod && key === 's') {
			e.preventDefault();
			void saveFromKeys();
			return;
		}
		if (tab !== 'visual' || shortcutsOpen) return;
		// Typing keeps its own keys (a field's own undo, too); the palette list keeps the plain
		// keys (its arrows and Enter) but not Ctrl/Cmd ones.
		if (target.closest('input, textarea, select, [contenteditable], [role="dialog"], [role="menu"]')) return;
		if (!mod && target.closest('[role="listbox"]')) return;
		if (mod && key === 'z') {
			e.preventDefault();
			if (e.shiftKey) redo();
			else undo();
		} else if (mod && key === 'y') {
			e.preventDefault();
			redo();
		} else if (mod && key === 'd') {
			e.preventDefault();
			duplicateSelected();
		} else if (mod || e.altKey) {
			return;
		} else if (e.key === '/') {
			e.preventDefault();
			void openPalette();
		} else if (e.key === '[' || e.key === ']') {
			e.preventDefault();
			togglePanel(e.key === '[' ? 'left' : 'right');
		} else if ((e.key === 'Delete' || e.key === 'Backspace') && selectedId) {
			e.preventDefault();
			removeSelected();
		} else if (e.key.startsWith('Arrow')) {
			e.preventDefault();
			walk(e.key.slice(5).toLowerCase() as 'left' | 'right' | 'up' | 'down');
		} else if (key === 'f') {
			viz?.fitView();
		} else if (e.key === '?') {
			shortcutsOpen = true;
		} else if (e.key === 'Escape') {
			selectedId = null;
		}
	}

	// ---------- YAML tab ----------
	let yamlDraft = $state('');
	let yamlDirty = $state(false);
	let yamlErrors = $state<string[]>([]);
	async function applyYaml(): Promise<boolean> {
		const res = await api().playbooks.validate(yamlDraft);
		if (!res.spec) {
			yamlErrors = res.errors;
			return false;
		}
		yamlErrors = [];
		yamlDirty = false;
		update(toAuthoring(res.spec));
		if (selectedId && !res.spec.steps.some((st) => st.id === selectedId)) selectedId = null;
		snackbar.show(res.ok ? 'Applied' : `Applied, with ${res.errors.length} problem${res.errors.length === 1 ? '' : 's'}`);
		return true;
	}

	/** Ctrl/Cmd+S: in the YAML tab, apply what was typed there first. */
	async function saveFromKeys() {
		if (saving) return;
		if (tab === 'yaml' && yamlDirty && !(await applyYaml())) return;
		if (dirty || !savedId) await save();
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
			savedSnapshot = JSON.stringify(spec);
			snackbar.show(`Saved “${spec.name}”`);
			if (location.pathname !== `/playbooks/edit/${spec.id}`) await goto(`/playbooks/edit/${spec.id}`, { replaceState: true });
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save');
		} finally {
			saving = false;
		}
	}

	// Leaving with unsaved changes asks first (in the app's own dialog; closing the tab or
	// reloading is the browser's own prompt, from onbeforeunload below).
	let leaving = false;
	beforeNavigate(({ cancel, to, type }) => {
		if (!dirty || leaving || type === 'leave' || !to || to.url.pathname === `/playbooks/edit/${spec.id}`) return;
		cancel();
		void confirmAction({
			title: 'Leave without saving?',
			message: 'Your changes to this playbook will be lost.',
			confirmLabel: 'Leave',
			danger: true
		}).then((ok) => {
			if (!ok) return;
			leaving = true;
			void goto(to.url);
		});
	});

</script>

<svelte:window onkeydown={onKey} onbeforeunload={(e) => dirty && e.preventDefault()} />
<svelte:head><title>{spec.name || 'Playbook'} · Editor · Castor</title></svelte:head>

<div class="flex h-full min-h-0 flex-col">
	<header class="flex items-center gap-3 border-b border-outline-variant px-4 py-2.5">
		<IconButton icon="arrow_back" label="Back to playbooks" onclick={() => goto('/playbooks')} />
		<div class="min-w-0">
			<h1 class="truncate title-large text-on-surface">{spec.name || 'Untitled playbook'}</h1>
			<p class="truncate body-small text-on-surface-variant">
				{savedId ? 'Editing' : readOnlyOrigin(data.playbook?.origin) ? 'Copy of a read-only playbook' : 'New playbook'} · <span class="font-mono">{spec.id}</span>
			</p>
		</div>
		<span class="ml-2 flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 label-medium {!checking && errors.length ? 'bg-error-container text-on-error-container' : 'bg-surface-container-high text-on-surface-variant'}">
			{#if checking}
				<Icon name="progress_activity" size={16} class="animate-spin" />Checking
			{:else if errors.length}
				<Icon name="error" size={16} />{errors.length} problem{errors.length === 1 ? '' : 's'}
			{:else}
				<Icon name="check_circle" size={16} class="text-primary" />Valid
			{/if}
		</span>
		<div class="ml-auto hidden items-center gap-2 md:flex">
			{#if tab === 'visual'}
				<span class="flex items-center">
					<IconButton icon="undo" label="Undo (Ctrl+Z)" disabled={!past.length} onclick={undo} />
					<IconButton icon="redo" label="Redo (Ctrl+Shift+Z)" disabled={!future.length} onclick={redo} />
					<IconButton icon="keyboard" label="Keyboard shortcuts (?)" onclick={() => (shortcutsOpen = true)} />
					<IconButton icon={panels.left ? 'left_panel_close' : 'left_panel_open'} label="{panels.left ? 'Hide' : 'Show'} the step list ([)" onclick={() => togglePanel('left')} />
					<IconButton icon={panels.right ? 'right_panel_close' : 'right_panel_open'} label="{panels.right ? 'Hide' : 'Show'} the settings (])" onclick={() => togglePanel('right')} />
				</span>
			{/if}
			<Tabs
				tabs={[
					{ id: 'visual', label: 'Visual', icon: 'account_tree' },
					{ id: 'yaml', label: 'YAML', icon: 'code' }
				]}
				bind:value={tab}
				class="border-b-0"
			/>
			<Button
				icon="save"
				loading={saving}
				disabled={(!dirty && !!savedId) || (!checking && errors.length > 0)}
				title={!checking && errors.length ? `Fix the ${errors.length === 1 ? 'problem' : `${errors.length} problems`} first` : 'Ctrl+S'}
				onclick={save}>{savedId ? 'Save' : 'Create'}</Button
			>
		</div>
	</header>

	{#if hasComments || readOnlyOrigin(data.playbook?.origin)}
		<div class="flex items-center gap-2 border-b border-outline-variant bg-tertiary-container/30 px-4 py-1.5 body-small text-on-surface">
			<Icon name="info" size={16} class="text-tertiary" />
			{#if readOnlyOrigin(data.playbook?.origin)}
				{pluginOf(data.playbook?.origin) ? `This playbook comes with the plugin ${pluginOf(data.playbook?.origin)}` : 'Built-in playbooks are read-only'}, so this saves as a new playbook.
			{:else}
				This playbook's YAML has comments. Saving from the editor rewrites the YAML, so they'll be lost.
			{/if}
		</div>
	{/if}

	{#if tab === 'visual'}
		<!-- Phones: the graph to look at; editing needs the room of a laptop. -->
		<div class="flex min-h-0 flex-1 flex-col gap-2 p-3 md:hidden">
			<p class="flex items-center gap-2 rounded-md bg-surface-container-high px-3 py-2 body-small text-on-surface-variant">
				<Icon name="desktop_windows" size={18} />Editing playbooks needs a larger screen. Here you can look at the graph.
			</p>
			<div class="min-h-0 flex-1"><PlaybookVisualizer spec={drawn} commands={data.commands} fill /></div>
		</div>
		<div
			class="hidden min-h-0 flex-1 md:grid"
			style:grid-template-columns="{panels.left ? '18rem' : '3.5rem'} minmax(0,1fr) {panels.right ? '24rem' : '3.5rem'}"
		>
			<!-- palette -->
			{#if panels.left}
				<aside class="flex min-h-0 flex-col border-r border-outline-variant p-3">
					<StepPalette bind:this={palette} {spec} commands={data.commands} tools={data.tools} {selected} onadd={(block, connect) => addBlock(block, undefined, connect)} />
				</aside>
			{:else}
				<aside class="flex flex-col items-center gap-1 border-r border-outline-variant py-3">
					<IconButton icon="add" label="Add a step (/)" onclick={openPalette} />
				</aside>
			{/if}

			<!-- canvas -->
			<section class="relative min-h-0 p-3">
				<PlaybookVisualizer
					bind:this={viz}
					spec={drawn}
					commands={data.commands}
					editable
					fill
					minFit={0.65}
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
						<p class="max-w-sm body-small">
							Press <kbd class="rounded-xs border border-outline-variant px-1 font-mono">/</kbd>, type a command and press <kbd class="rounded-xs border border-outline-variant px-1 font-mono">Enter</kbd>.
							Each Enter after that adds the next step, already connected. Or drag commands from the left.
						</p>
					</div>
				{/if}
			</section>

			<!-- properties -->
			{#if !panels.right}
				<aside class="flex flex-col items-center gap-1 border-l border-outline-variant py-3">
					<IconButton icon="tune" label="Show the settings (])" onclick={() => togglePanel('right', true)} />
				</aside>
			{:else}
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
					<PlaybookSettings {spec} idEditable={!savedId} kinds={data.kinds} errors={problems.general} onchange={(next) => update(next, 'settings')} />
				{/if}
			</aside>
			{/if}
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
					<p class="body-small text-on-surface-variant">
							None. The format is in the <a class="text-primary underline underline-offset-4" href="https://github.com/emilyastranova/castor/blob/main/docs/PLAYBOOK_SPEC.md" target="_blank" rel="noopener noreferrer">playbook spec</a>.
						</p>
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

<Dialog bind:open={shortcutsOpen} title="Keyboard shortcuts" icon="keyboard" size="sm">
	<dl class="grid grid-cols-[max-content_minmax(0,1fr)] items-baseline gap-x-6 gap-y-2 pt-2 body-medium">
		{#each SHORTCUTS as [keys, what] (what)}
			<dt class="flex flex-wrap gap-1">{#each keys as k (k)}<kbd class="flex h-6 min-w-6 items-center justify-center rounded-xs border border-outline-variant bg-surface-container px-1.5 font-mono text-[12px] text-on-surface">{#if k.startsWith('icon:')}<Icon name={k.slice(5)} size={14} /><span class="sr-only">{k.slice(5).replace('arrow_', '').replace('ward', '')} arrow</span>{:else}{k}{/if}</kbd>{/each}</dt>
			<dd class="text-on-surface-variant">{what}</dd>
		{/each}
	</dl>
	{#snippet actions()}<Button variant="text" onclick={() => (shortcutsOpen = false)}>Close</Button>{/snippet}
</Dialog>
