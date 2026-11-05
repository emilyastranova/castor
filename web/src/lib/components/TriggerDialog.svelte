<script lang="ts">
	import type { AssetCondition, AssetTrigger, CommandDefinition, PlaybookDefinition, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Chip, Dialog, IconButton, Select, Switch, TextField } from '$lib/m3';
	import { assetTypes } from '$lib/assetTypes.svelte';

	/** Create or edit a trigger: when an asset of a type appears or changes (and matches the
	 *  conditions), run a command or a playbook with values taken from the asset. */
	interface Props {
		open: boolean;
		projectId: UUID;
		trigger: AssetTrigger | null;
		onsaved: () => void;
	}
	let { open = $bindable(false), projectId, trigger, onsaved }: Props = $props();

	let commands = $state<CommandDefinition[]>([]);
	let playbooks = $state<PlaybookDefinition[]>([]);
	$effect(() => {
		if (!open) return;
		void assetTypes.load();
		if (!commands.length) api().commands.list().then((c) => (commands = c));
		if (!playbooks.length) api().playbooks.list().then((p) => (playbooks = p));
	});

	let name = $state('');
	let enabled = $state(true);
	let assetType = $state('host');
	let onEvent = $state<'created' | 'updated' | 'any'>('created');
	let conditions = $state<AssetCondition[]>([]);
	let action = $state<'command' | 'playbook'>('command');
	let target = $state('');
	let args = $state<Record<string, string>>({});
	let once = $state(true);
	let maxPerHour = $state('20');
	let error = $state<string | null>(null);
	let saving = $state(false);

	$effect(() => {
		if (!open) return;
		const t = trigger;
		name = t?.name ?? '';
		enabled = t?.enabled ?? true;
		assetType = t?.asset_type ?? 'host';
		onEvent = t?.on_event ?? 'created';
		conditions = t ? JSON.parse(JSON.stringify(t.conditions)) : [];
		action = t?.action ?? 'command';
		target = t?.target ?? '';
		args = Object.fromEntries(Object.entries(t?.args ?? {}).map(([k, v]) => [k, String(v ?? '')]));
		once = t?.once_per_asset ?? true;
		maxPerHour = String(t?.max_per_hour ?? 20);
		error = null;
	});

	let type = $derived(assetTypes.get(assetType));
	let fieldOptions = $derived([
		...(type?.fields ?? []).map((f) => ({ value: f.name, label: f.label })),
		{ value: 'key', label: 'Identity (key)' },
		{ value: 'label', label: 'Name (label)' }
	]);
	/** What the chosen command or playbook takes, to map from the asset. */
	let params = $derived.by(() => {
		if (action === 'command') {
			const c = commands.find((x) => x.id === target);
			return (c?.spec.args ?? []).map((a) => ({ name: a.name, label: `${a.name}${a.required ? ' *' : ''}`, hint: a.description ?? '' }));
		}
		const p = playbooks.find((x) => x.id === target);
		return (p?.spec.inputs ?? []).map((i) => ({ name: i.name, label: `${i.name}${i.required ? ' *' : ''}`, hint: i.description ?? '' }));
	});
	// Picking a target pre-fills its first argument with the asset's main value.
	let filledFor = '';
	$effect(() => {
		const key = `${action}:${target}`;
		if (!target || key === filledFor || trigger) return;
		filledFor = key;
		const first = params[0];
		if (first && !args[first.name]) args = { ...args, [first.name]: `{{ asset.${type?.default_field ?? 'key'} }}` };
	});

	const OPS = [
		{ value: 'equals', label: 'is' },
		{ value: 'not_equals', label: 'is not' },
		{ value: 'contains', label: 'contains' },
		{ value: 'exists', label: 'is set' },
		{ value: 'missing', label: 'is empty' },
		{ value: 'gt', label: '>' },
		{ value: 'lt', label: '<' }
	];

	async function save() {
		saving = true;
		error = null;
		const body = {
			name: name.trim(),
			enabled,
			asset_type: assetType,
			on_event: onEvent,
			conditions: conditions.filter((c) => c.field).map((c) => ({ ...c, value: ['exists', 'missing'].includes(c.op) ? null : c.value })),
			action,
			target,
			args: Object.fromEntries(Object.entries(args).filter(([, v]) => v !== '')),
			once_per_asset: once,
			max_per_hour: Math.max(1, Number(maxPerHour) || 20)
		};
		try {
			if (trigger) await api().assets.updateTrigger(trigger.id, body);
			else await api().assets.createTrigger(projectId, body);
			open = false;
			onsaved();
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save the trigger';
		} finally {
			saving = false;
		}
	}
</script>

<Dialog bind:open title={trigger ? `Edit “${trigger.name}”` : 'New trigger'} icon="bolt" size="md">
	<div class="flex flex-col gap-4 pt-2 pb-2">
		<TextField label="Name" bind:value={name} placeholder="e.g. Sweep new hosts" required />

		<div>
			<p class="mb-1 label-large text-on-surface-variant">When</p>
			<div class="grid gap-3 sm:grid-cols-2">
				<Select label="Asset type" bind:value={assetType} options={assetTypes.list.map((t) => ({ value: t.name, label: t.plural }))} />
				<Select label="Event" value={onEvent} onchange={(v) => (onEvent = v as typeof onEvent)} options={[{ value: 'created', label: 'is found (new)' }, { value: 'updated', label: 'changes' }, { value: 'any', label: 'is found or changes' }]} />
			</div>
		</div>

		<div>
			<p class="mb-1 label-large text-on-surface-variant">Only if (all of these)</p>
			{#each conditions as c, i (i)}
				<div class="mb-2 flex items-start gap-2">
					<Select class="w-44" label="Field" value={c.field} onchange={(v) => (c.field = v)} options={fieldOptions} />
					<Select class="w-32" label="Test" value={c.op} onchange={(v) => (c.op = v as AssetCondition['op'])} options={OPS} />
					{#if !['exists', 'missing'].includes(c.op)}
						<TextField label="Value" value={String(c.value ?? '')} class="flex-1" oninput={(e) => (c.value = (e.currentTarget as HTMLInputElement).value)} />
					{/if}
					<IconButton icon="close" label="Remove condition" class="mt-3" onclick={() => (conditions = conditions.filter((_, j) => j !== i))} />
				</div>
			{/each}
			<Button variant="text" icon="add" onclick={() => (conditions = [...conditions, { field: type?.fields[0]?.name ?? 'key', op: 'equals', value: '' }])}>Add a condition</Button>
		</div>

		<div>
			<p class="mb-1 label-large text-on-surface-variant">Run</p>
			<div class="mb-2 flex gap-2">
				<Chip variant="filter" icon="terminal" selected={action === 'command'} onclick={() => { action = 'command'; target = ''; args = {}; }}>A command</Chip>
				<Chip variant="filter" icon="account_tree" selected={action === 'playbook'} onclick={() => { action = 'playbook'; target = ''; args = {}; }}>A playbook</Chip>
			</div>
			{#if action === 'command'}
				<Select label="Command" bind:value={target} options={commands.map((c) => ({ value: c.id, label: c.name, description: c.description, keywords: [c.id, ...c.tags].join(' ') }))} />
			{:else}
				<Select label="Playbook" bind:value={target} options={playbooks.map((p) => ({ value: p.id, label: p.name, description: p.description, keywords: p.id }))} />
			{/if}
			{#if params.length}
				<div class="mt-3 flex flex-col gap-2">
					{#each params as p (p.name)}
						<TextField label={p.label} mono value={args[p.name] ?? ''} supporting={p.hint} oninput={(e) => (args = { ...args, [p.name]: (e.currentTarget as HTMLInputElement).value })} />
					{/each}
					<p class="px-1 body-small text-on-surface-variant">
						Use the asset's values: {['key', 'label', ...(type?.fields ?? []).map((f) => f.name)].slice(0, 7).map((f) => `{{ asset.${f} }}`).join(', ')}
					</p>
				</div>
			{/if}
		</div>

		<div>
			<p class="mb-1 label-large text-on-surface-variant">Guards</p>
			<div class="flex flex-wrap items-center gap-x-6 gap-y-2">
				<Switch bind:checked={once} label="Once per asset" />
				<TextField label="At most per hour" bind:value={maxPerHour} type="number" class="w-40" />
				<Switch bind:checked={enabled} label="Enabled" />
			</div>
			<p class="mt-2 px-1 body-small text-on-surface-variant">Runs start by themselves only for assets inside the project's scope; anything else waits for someone to approve it.</p>
		</div>
		{#if error}<p class="body-small text-error">{error}</p>{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="check" loading={saving} disabled={!name.trim() || !target} onclick={save}>{trigger ? 'Save' : 'Add trigger'}</Button>
	{/snippet}
</Dialog>
