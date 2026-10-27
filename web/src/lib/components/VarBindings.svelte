<script lang="ts">
	import type { ProjectSummary, UUID, VarSlot, VariableKind, VariableSet } from '$lib/api';
	import { api } from '$lib/api';
	import { Icon, IconButton, Select } from '$lib/m3';
	import { kindIcon } from '$lib/variables';
	import VariableSetDialog from './VariableSetDialog.svelte';

	/** Pick a variable set for each of a library command's `vars` slots, or enter values
	 *  by hand (a one-off set for this card, optionally saved to the library). */
	interface Props {
		slots: VarSlot[];
		bindings: Record<string, UUID>;
		projectId: UUID;
		onchange: (bindings: Record<string, UUID>) => void;
	}
	let { slots, bindings, projectId, onchange }: Props = $props();

	const MANUAL = '__manual__';

	let sets = $state<VariableSet[]>([]);
	let kinds = $state<VariableKind[]>([]);
	let projects = $state<Pick<ProjectSummary, 'id' | 'codename' | 'name'>[]>([]);
	// One-off sets bound to this card (not in the library listing), by id.
	let oneOffs = $state<Record<string, VariableSet>>({});
	let loaded = $state(false);

	let dialogOpen = $state(false);
	let dialogSlot = $state<VarSlot | null>(null);
	let dialogSet = $state<VariableSet | null>(null);

	$effect(() => {
		const project = projectId;
		Promise.all([api().variables.sets({ project_id: project }), api().variables.kinds(), api().projects.list()])
			.then(([s, k, p]) => {
				sets = s;
				kinds = k;
				projects = p;
			})
			.finally(() => (loaded = true));
	});

	// Fetch any bound set that isn't in the library listing: a one-off entry.
	$effect(() => {
		for (const id of Object.values(bindings)) {
			if (!loaded || sets.some((s) => s.id === id) || oneOffs[id]) continue;
			api()
				.variables.getSet(id)
				.then((s) => (oneOffs = { ...oneOffs, [id]: s }))
				.catch(() => {});
		}
	});

	function options(slot: VarSlot) {
		const matching = sets.filter((s) => s.kind_id === slot.kind);
		const bound = bindings[slot.name];
		const manual = bound && oneOffs[bound]?.ephemeral ? [{ value: bound, label: 'Manual entry' }] : [];
		return [
			{ value: '', label: slot.required ? 'Choose a set…' : 'None' },
			...matching.map((s) => ({ value: s.id, label: s.missing.length ? `${s.name} (incomplete)` : s.name })),
			...manual,
			{ value: MANUAL, label: 'Enter manually…' }
		];
	}

	function choose(slot: VarSlot, id: string) {
		if (id === MANUAL) {
			openManual(slot, null);
			return;
		}
		const next = { ...bindings };
		if (id) next[slot.name] = id;
		else delete next[slot.name];
		onchange(next);
	}

	function openManual(slot: VarSlot, existing: VariableSet | null) {
		dialogSlot = slot;
		dialogSet = existing;
		dialogOpen = true;
	}

	function saved(set: VariableSet) {
		if (set.ephemeral) oneOffs = { ...oneOffs, [set.id]: set };
		else sets = [...sets.filter((s) => s.id !== set.id), set].sort((a, b) => a.name.localeCompare(b.name));
		if (dialogSlot && bindings[dialogSlot.name] !== set.id) onchange({ ...bindings, [dialogSlot.name]: set.id });
	}
</script>

<div class="flex flex-col gap-2">
	<div class="flex items-center gap-2">
		<Icon name="key" size={18} class="text-primary" />
		<span class="label-large text-on-surface">Variables</span>
		<a href="/variables" class="ml-auto body-small text-primary hover:underline">Manage sets</a>
	</div>
	<div class="grid gap-3 sm:grid-cols-2">
		{#each slots as slot (slot.name)}
			{@const bound = bindings[slot.name]}
			{@const oneOff = bound ? oneOffs[bound] : undefined}
			{@const unbound = slot.required && !bound}
			<div class="flex items-start gap-1">
				<Select
					class="min-w-0 flex-1"
					label="{slot.name} · {slot.kind}{slot.required ? ' *' : ''}"
					value={bound ?? ''}
					options={options(slot)}
					supporting={oneOff?.ephemeral
						? 'Entered for this card only'
						: unbound
							? 'Required: the job fails without a set'
							: slot.description}
					onchange={(v) => choose(slot, v)}
				>
					{#snippet leading(value)}<Icon name={value === MANUAL || oneOff?.ephemeral ? 'edit_note' : kindIcon(slot.kind)} size={20} />{/snippet}
				</Select>
				{#if oneOff?.ephemeral}
					<div class="flex h-16 items-end pb-1">
						<IconButton icon="edit" label="Edit the values for this card" size="sm" onclick={() => openManual(slot, oneOff)} />
					</div>
				{/if}
			</div>
		{/each}
	</div>
</div>

<VariableSetDialog
	bind:open={dialogOpen}
	set={dialogSet}
	{kinds}
	{projects}
	kindId={dialogSlot?.kind ?? ''}
	manual
	scopeLabel="this card"
	onsaved={saved}
/>
