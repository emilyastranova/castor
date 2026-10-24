<script lang="ts">
	import type { PlaybookSpec, PlaybookRun, Project } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Select, Switch, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	interface Props {
		open: boolean;
		spec: PlaybookSpec | null;
		projects: Project[];
		onRun?: (run: PlaybookRun) => void;
	}

	let { open = $bindable(false), spec, projects, onRun }: Props = $props();

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
						{:else if inp.choices && inp.choices.length > 0}
							<Select
								label={inp.name}
								options={inp.choices.map((c) => ({ value: c, label: c }))}
								value={String(inputValues[inp.name] ?? '')}
								supporting={inp.description}
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
