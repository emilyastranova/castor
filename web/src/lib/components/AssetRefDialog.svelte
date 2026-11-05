<script lang="ts">
	import type { Asset, AssetRef, UUID } from '$lib/api';
	import { api } from '$lib/api';
	import { Button, Dialog, Select, TextField } from '$lib/m3';
	import { assetTypes, show } from '$lib/assetTypes.svelte';

	/** Fill an argument from the project's assets: "hosts where smb_signing:false → ip". The
	 *  card keeps the query, so each run uses the assets as they are then. */
	interface Props {
		open: boolean;
		projectId: UUID;
		initial?: AssetRef | null;
		onuse: (ref: AssetRef) => void;
	}
	let { open = $bindable(false), projectId, initial = null, onuse }: Props = $props();

	let type = $state('host');
	let where = $state('');
	let field = $state('ip');
	$effect(() => {
		if (!open) return;
		void assetTypes.load();
		type = initial?.assets.type ?? 'host';
		where = initial?.assets.where ?? '';
		field = initial?.assets.field ?? 'ip';
	});
	let def = $derived(assetTypes.get(type));
	let fields = $derived([...(def?.fields ?? []).map((f) => ({ value: f.name, label: f.label })), { value: 'key', label: 'Identity (key)' }]);
	$effect(() => {
		if (def && !fields.some((f) => f.value === field)) field = def.default_field;
	});

	// Preview: what the argument would get right now.
	let matches = $state<Asset[]>([]);
	let total = $state(0);
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		if (!open) return;
		const q = { type, where, limit: 50 };
		clearTimeout(timer);
		timer = setTimeout(async () => {
			const res = await api().assets.list(projectId, q);
			matches = res.items;
			total = res.total;
		}, 200);
	});
	let values = $derived([...new Set(matches.map((a) => (field === 'key' ? a.key : show(a.fields[field]))).filter(Boolean))]);
</script>

<Dialog bind:open title="Use the project's assets" icon="radar" size="sm">
	<div class="flex flex-col gap-3 pt-2 pb-2">
		<Select label="Assets" bind:value={type} options={assetTypes.list.map((t) => ({ value: t.name, label: t.plural }))} />
		<TextField label="Where (optional)" bind:value={where} mono placeholder="smb_signing:false port:445" supporting="field:value, field!=value, port>1000, or words" />
		<Select label="Value to use" bind:value={field} options={fields} />
		<div class="rounded-md bg-surface-container px-3 py-2">
			<p class="body-small text-on-surface-variant">{total} match{total === 1 ? '' : 'es'} now{values.length ? ':' : ''}</p>
			{#if values.length}<p class="font-mono text-[12px] break-words text-on-surface">{values.slice(0, 20).join(', ')}{values.length > 20 || total > 50 ? ', …' : ''}</p>{/if}
		</div>
		<p class="body-small text-on-surface-variant">The card keeps this query: each run uses the assets as they are at that moment.</p>
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="check" onclick={() => { onuse({ assets: { type, where: where.trim(), field } }); open = false; }}>Use these</Button>
	{/snippet}
</Dialog>
