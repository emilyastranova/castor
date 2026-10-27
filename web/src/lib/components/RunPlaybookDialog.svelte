<script lang="ts">
	import type { PlaybookInputSpec, PlaybookSpec, PlaybookRun, Project, VariableKind, VariableSet } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Select, Switch, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import VariableSetDialog from './VariableSetDialog.svelte';
	import ListArgInput from './ListArgInput.svelte';

	interface Props {
		open: boolean;
		spec: PlaybookSpec | null;
		projects: Project[];
		onRun?: (run: PlaybookRun) => void;
	}

	let { open = $bindable(false), spec, projects, onRun }: Props = $props();

	// Variable sets the chosen project may use, for varset inputs.
	let varSets = $state<VariableSet[]>([]);
	let kinds = $state<VariableKind[]>([]);
	// Values entered by hand for a varset input: a one-off set the run will own.
	let manualSets = $state<Record<string, VariableSet>>({});
	let manualInput = $state<PlaybookInputSpec | null>(null);
	let manualOpen = $state(false);
	const MANUAL = '__manual__';

	function chooseVarset(inp: PlaybookInputSpec, value: string) {
		if (value === MANUAL) {
			manualInput = inp;
			manualOpen = true;
			if (!kinds.length) api().variables.kinds().then((k) => (kinds = k));
			return;
		}
		inputValues = { ...inputValues, [inp.name]: value };
	}

	function manualSaved(set: VariableSet) {
		if (!manualInput) return;
		if (set.ephemeral) manualSets = { ...manualSets, [manualInput.name]: set };
		else varSets = [...varSets, set];
		inputValues = { ...inputValues, [manualInput.name]: set.id };
	}
	$effect(() => {
		const project = selectedProjectId;
		if (!open || !project || !spec?.inputs?.some((i) => i.type === 'varset')) return;
		api()
			.variables.sets({ project_id: project })
			.then((s) => (varSets = s))
			.catch(() => (varSets = []));
	});

	let selectedProjectId = $state<string>('');
	let inputValues = $state<Record<string, unknown>>({});
	let running = $state(false);
	let error = $state<string | null>(null);

	$effect(() => {
		if (open && spec) {
			error = null;
			if (!selectedProjectId && projects.length > 0) {
				selectedProjectId = projects[0].id;
			}
			const initial: Record<string, unknown> = {};
			for (const inp of spec.inputs || []) {
				if (inp.default !== undefined && inp.default !== null) {
					initial[inp.name] = inp.default;
				} else if (inp.example !== undefined && inp.example !== null) {
					initial[inp.name] = inp.example;
				} else {
					initial[inp.name] = '';
				}
			}
			inputValues = initial;
		}
	});

	let projectOptions = $derived(
		projects.map((p) => ({
			value: p.id,
			label: `${p.codename} — ${p.name}`
		}))
	);

	async function handleSubmit() {
		if (!spec || !selectedProjectId) return;
		running = true;
		error = null;
		try {
			const run = await api().playbooks.run(spec.id, selectedProjectId, inputValues);
			snackbar.show(`Started playbook “${spec.name}”`);
			open = false;
			onRun?.(run);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Failed to launch playbook';
		} finally {
			running = false;
		}
	}
</script>

<Dialog
	bind:open
	title="Run Playbook"
>
	{#if spec}
		<p class="body-small text-on-surface-variant -mt-2 mb-3">{spec.name}</p>
		<form
			onsubmit={(e) => {
				e.preventDefault();
				handleSubmit();
			}}
			class="flex flex-col gap-4 py-1"
		>
			{#if error}
				<div class="rounded-lg bg-error-container p-3 body-medium text-on-error-container">
					{error}
				</div>
			{/if}

			<div>
				<Select
					label="Target Project"
					options={projectOptions}
					bind:value={selectedProjectId}
				/>
				<p class="mt-1 body-small text-on-surface-variant">
					Tasks and cards created by this playbook will run on this project board.
				</p>
			</div>

			{#if spec.inputs && spec.inputs.length > 0}
				<div class="flex flex-col gap-3 border-t border-outline-variant/60 pt-3">
					<span class="label-large text-on-surface">Playbook Parameters</span>
					{#each spec.inputs as inp (inp.name)}
						{#if inp.type === 'boolean'}
							<div class="flex items-center justify-between py-1">
								<div>
									<span class="body-medium text-on-surface">{inp.name}</span>
									{#if inp.description}
										<p class="body-small text-on-surface-variant">{inp.description}</p>
									{/if}
								</div>
								<Switch
									checked={Boolean(inputValues[inp.name])}
									onchange={(v) => (inputValues = { ...inputValues, [inp.name]: v })}
								/>
							</div>
						{:else if inp.type === 'varset'}
							{@const matching = varSets.filter((s) => s.kind_id === inp.kind)}
							{@const manual = manualSets[inp.name]}
							<Select
								label="{inp.name} · {inp.kind}{inp.required ? ' *' : ''}"
								options={[
									{ value: '', label: matching.length ? 'Choose a set…' : `No ${inp.kind} sets for this project` },
									...matching.map((s) => ({ value: s.id, label: s.missing.length ? `${s.name} (incomplete)` : s.name })),
									...(manual ? [{ value: manual.id, label: 'Manual entry (this run only)' }] : []),
									{ value: MANUAL, label: 'Enter manually…' }
								]}
								value={String(inputValues[inp.name] ?? '')}
								supporting={inp.description}
								onchange={(v) => chooseVarset(inp, v)}
							/>
						{:else if inp.choices && inp.choices.length > 0}
							<Select
								label={inp.name}
								options={inp.choices.map((c) => ({ value: c, label: c }))}
								value={String(inputValues[inp.name] ?? '')}
								supporting={inp.description}
								onchange={(v) => (inputValues = { ...inputValues, [inp.name]: v })}
							/>
						{:else if inp.type === 'string' || inp.type === 'list'}
							<ListArgInput
								label={inp.name}
								type={inp.type}
								value={inputValues[inp.name]}
								required={inp.required}
								mono={false}
								projectId={selectedProjectId}
								supporting={[inp.description, inp.example ? `e.g. ${inp.example}` : ''].filter(Boolean).join(' · ')}
								onchange={(v) => (inputValues = { ...inputValues, [inp.name]: v })}
							/>
						{:else}
							<TextField
								label={inp.name}
								value={inputValues[inp.name] !== undefined ? String(inputValues[inp.name]) : ''}
								oninput={(e) =>
									(inputValues = {
										...inputValues,
										[inp.name]: (e.target as HTMLInputElement).value
									})}
								required={inp.required}
								supporting={[inp.description, inp.example ? `e.g. ${inp.example}` : '']
									.filter(Boolean)
									.join(' · ')}
							/>
						{/if}
					{/each}
				</div>
			{/if}
		</form>
	{/if}

	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)} disabled={running}>
			Cancel
		</Button>
		<Button
			variant="filled"
			icon="play_arrow"
			onclick={handleSubmit}
			loading={running}
			disabled={!selectedProjectId || running}
		>
			Launch Playbook
		</Button>
	{/snippet}
</Dialog>

<VariableSetDialog
	bind:open={manualOpen}
	set={manualInput ? (manualSets[manualInput.name] ?? null) : null}
	{kinds}
	{projects}
	kindId={manualInput?.kind ?? ''}
	manual
	scopeLabel="this run"
	onsaved={manualSaved}
/>
