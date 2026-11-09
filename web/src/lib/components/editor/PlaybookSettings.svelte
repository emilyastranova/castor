<script lang="ts">
	import type { PlaybookInputSpec, PlaybookSpec, VariableKind } from '$lib/api';
	import { Icon, IconButton, Select, Switch, TagInput, TextField } from '$lib/m3';
	import { loadTags, type TagSuggestion } from '$lib/tags';

	/** The playbook's own fields and the inputs asked for when a run starts. */
	interface Props {
		spec: PlaybookSpec;
		/** The id can only be set before the first save. */
		idEditable: boolean;
		kinds: VariableKind[];
		errors?: string[];
		onchange: (spec: PlaybookSpec) => void;
	}
	let { spec, idEditable, kinds, errors = [], onchange }: Props = $props();

	let tagSuggestions = $state<TagSuggestion[]>([]);
	$effect(() => {
		void loadTags('library').then((t) => (tagSuggestions = t));
	});

	const TYPES = ['string', 'number', 'boolean', 'list', 'varset'].map((t) => ({ value: t, label: t }));

	function set<K extends keyof PlaybookSpec>(key: K, value: PlaybookSpec[K]) {
		onchange({ ...spec, [key]: value });
	}
	function setInput(i: number, patch: Partial<PlaybookInputSpec>) {
		set('inputs', spec.inputs.map((inp, j) => (j === i ? { ...inp, ...patch } : inp)));
	}
	function addInput() {
		const taken = new Set(spec.inputs.map((i) => i.name));
		let name = 'targets';
		for (let n = 2; taken.has(name); n++) name = `input_${n}`;
		set('inputs', [...spec.inputs, { name, type: 'string', description: '', required: false, default: null, example: null }]);
	}
</script>

<div class="flex flex-col gap-3">
	<div class="flex items-center gap-2">
		<Icon name="account_tree" class="text-primary" />
		<p class="title-medium text-on-surface">Playbook</p>
	</div>
	{#if errors.length}
		<ul class="flex flex-col gap-1 rounded-md bg-error-container/40 px-3 py-2 body-small text-on-surface">
			{#each errors as e (e)}<li class="flex gap-1.5"><Icon name="error" size={16} class="mt-px shrink-0 text-error" />{e}</li>{/each}
		</ul>
	{/if}
	<TextField label="Name" required value={spec.name} oninput={(e) => set('name', (e.currentTarget as HTMLInputElement).value)} />
	<TextField
		label="Id"
		mono
		required
		value={spec.id}
		disabled={!idEditable}
		supporting={idEditable ? 'lowercase letters, digits and hyphens; can’t change after saving' : 'can’t change after saving'}
		oninput={(e) => set('id', (e.currentTarget as HTMLInputElement).value)}
	/>
	<TextField label="Description" multiline rows={3} value={spec.description} oninput={(e) => set('description', (e.currentTarget as HTMLTextAreaElement).value)} />
	<TagInput label="Tags" value={spec.tags} suggestions={tagSuggestions} noun="uses" placeholder="Add a tag" onchange={(next) => set('tags', next)} />

	<div class="mt-2 flex items-center gap-2 border-t border-outline-variant/60 pt-3">
		<Icon name="input" size={18} class="text-on-surface-variant" />
		<span class="flex-1 label-large text-on-surface">Inputs</span>
		<button type="button" class="label-medium text-primary hover:underline" onclick={addInput}>+ Add input</button>
	</div>
	<p class="body-small text-on-surface-variant">Asked for when a run starts; steps use them as <span class="font-mono">{'{{ inputs.<name> }}'}</span>.</p>
	{#each spec.inputs as inp, i (i)}
		<div class="flex flex-col gap-2 rounded-md border border-outline-variant/60 p-2.5">
			<div class="flex items-center gap-2">
				<div class="min-w-0 flex-1">
					<TextField label="Name" mono value={inp.name} oninput={(e) => setInput(i, { name: (e.currentTarget as HTMLInputElement).value })} />
				</div>
				<div class="w-32"><Select label="Type" value={inp.type} options={TYPES} onchange={(t) => setInput(i, { type: t as PlaybookInputSpec['type'] })} /></div>
				<IconButton icon="delete" label="Remove input" size="sm" onclick={() => set('inputs', spec.inputs.filter((_, j) => j !== i))} />
			</div>
			{#if inp.type === 'varset'}
				<Select label="Kind" value={inp.kind ?? ''} options={kinds.map((k) => ({ value: k.id, label: k.name }))} onchange={(k) => setInput(i, { kind: k })} />
			{/if}
			<TextField label="Description" value={inp.description} oninput={(e) => setInput(i, { description: (e.currentTarget as HTMLInputElement).value })} />
			{#if inp.type !== 'varset'}
				<div class="grid grid-cols-2 gap-2">
					<TextField label="Default" mono value={inp.default == null ? '' : String(inp.default)} oninput={(e) => setInput(i, { default: (e.currentTarget as HTMLInputElement).value || null })} />
					<TextField label="Example" mono value={inp.example == null ? '' : String(inp.example)} oninput={(e) => setInput(i, { example: (e.currentTarget as HTMLInputElement).value || null })} />
				</div>
				<TextField
					label="Choices (optional)"
					value={(inp.choices ?? []).join(', ')}
					supporting="comma separated; shown as a dropdown"
					oninput={(e) => {
						const list = (e.currentTarget as HTMLInputElement).value.split(',').map((c) => c.trim()).filter(Boolean);
						setInput(i, { choices: list.length ? list : null });
					}}
				/>
			{/if}
			<Switch checked={inp.required} label="Required" onchange={(c) => setInput(i, { required: c })} />
		</div>
	{:else}
		<p class="body-small text-on-surface-variant">No inputs.</p>
	{/each}
</div>
