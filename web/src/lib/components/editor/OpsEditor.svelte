<script lang="ts">
	import type { TransformSpec } from '$lib/api';
	import { Icon, IconButton, Select, Switch, TextField } from '$lib/m3';
	import { describeOp } from '$lib/playbook';

	/** A transform's input references, operations and output, as form rows. */
	interface Props {
		transform: TransformSpec;
		inputs: { value: string; label: string }[];
		onchange: (t: TransformSpec) => void;
	}
	let { transform, inputs, onchange }: Props = $props();

	type Op = Record<string, unknown> | string;
	const OPS = ['filter', 'pluck', 'format', 'unique', 'sort', 'limit'];
	const TESTS = ['equals', 'not_equals', 'in', 'not_in', 'contains', 'matches', 'exists'];
	const TEMPLATES: Record<string, Op> = {
		filter: { filter: { field: 'status', equals: 'up' } },
		pluck: { pluck: 'ip' },
		format: { format: 'http://{{ host }}:{{ port }}/' },
		unique: { unique: true },
		sort: { sort: true },
		limit: { limit: 10 }
	};

	let inputList = $derived(Array.isArray(transform.input) ? transform.input : transform.input ? [transform.input] : []);
	const name = (op: Op) => (typeof op === 'string' ? op : Object.keys(op)[0]);
	const arg = (op: Op) => (typeof op === 'string' ? true : Object.values(op)[0]);

	function emit(patch: Partial<TransformSpec>) {
		onchange({ ...transform, ...patch });
	}
	function setInputs(list: string[]) {
		emit({ input: list.length === 1 ? list[0] : list });
	}
	function setOp(i: number, op: Op) {
		emit({ ops: transform.ops.map((o, j) => (j === i ? op : o)) });
	}
	function move(i: number, by: number) {
		const ops = [...transform.ops];
		const [o] = ops.splice(i, 1);
		ops.splice(i + by, 0, o);
		emit({ ops });
	}

	/** Parse a value typed in a filter: numbers and booleans as such, lists for in/not_in. */
	function typed(text: string, test: string): unknown {
		const one = (t: string) => (t === 'true' ? true : t === 'false' ? false : t.trim() !== '' && !isNaN(Number(t)) ? Number(t) : t.trim());
		return test === 'in' || test === 'not_in' ? text.split(',').map(one).filter((x) => x !== '') : test === 'exists' ? text !== 'false' : one(text);
	}
	const shown = (v: unknown) => (Array.isArray(v) ? v.join(', ') : v == null ? '' : String(v));
</script>

<div class="flex flex-col gap-3">
	<div class="flex flex-col gap-1.5">
		<span class="body-small text-on-surface-variant">Input (several are concatenated)</span>
		{#each inputList as ref, i (ref + i)}
			<div class="flex items-center gap-1">
				<div class="min-w-0 flex-1">
					<Select label="Input {i + 1}" value={ref} options={inputs.some((o) => o.value === ref) ? inputs : [{ value: ref, label: ref }, ...inputs]} onchange={(x) => setInputs(inputList.map((r, j) => (j === i ? x : r)))} />
				</div>
				<IconButton icon="close" label="Remove input" size="sm" onclick={() => setInputs(inputList.filter((_, j) => j !== i))} />
			</div>
		{:else}
			<p class="body-small text-error">No input yet: connect a step to it, or add one.</p>
		{/each}
		{#if inputs.length}
			<button type="button" class="self-start label-medium text-primary hover:underline" onclick={() => setInputs([...inputList, inputs[0].value])}>+ Add input</button>
		{/if}
	</div>

	<div class="flex flex-col gap-2">
		<span class="body-small text-on-surface-variant">Operations, in order</span>
		{#each transform.ops as op, i (i)}
			{@const n = name(op)}
			{@const a = arg(op)}
			<div class="flex flex-col gap-2 rounded-md border border-tertiary/40 p-2.5">
				<div class="flex items-center gap-1">
					<span class="size-1.5 rounded-full bg-tertiary"></span>
					<span class="flex-1 truncate font-mono text-[12px] text-on-surface" title={describeOp(op)}>{describeOp(op)}</span>
					<IconButton icon="arrow_upward" label="Move up" size="sm" disabled={i === 0} onclick={() => move(i, -1)} />
					<IconButton icon="arrow_downward" label="Move down" size="sm" disabled={i === transform.ops.length - 1} onclick={() => move(i, 1)} />
					<IconButton icon="delete" label="Remove operation" size="sm" onclick={() => emit({ ops: transform.ops.filter((_, j) => j !== i) })} />
				</div>
				{#if n === 'filter'}
					{@const f = (a && typeof a === 'object' ? a : {}) as Record<string, unknown>}
					{@const test = TESTS.find((t) => t in f) ?? 'equals'}
					<div class="grid grid-cols-[1fr_1fr] gap-2">
						<TextField label="Field" mono value={String(f.field ?? '')} placeholder="value" oninput={(e) => setOp(i, { filter: { ...f, field: (e.currentTarget as HTMLInputElement).value || undefined } })} />
						<Select
							label="Test"
							value={test}
							options={TESTS.map((t) => ({ value: t, label: t.replace('_', ' ') }))}
							onchange={(t) => {
								const { [test]: old, ...rest } = f;
								setOp(i, { filter: { ...rest, [t]: typed(shown(old), t) } });
							}}
						/>
					</div>
					<TextField
						label={test === 'in' || test === 'not_in' ? 'Values (comma separated)' : test === 'exists' ? 'true or false' : 'Value'}
						mono
						value={shown(f[test])}
						supporting="numbers and true/false are typed; port is a number, so 445 not &quot;445&quot;"
						oninput={(e) => setOp(i, { filter: { ...f, [test]: typed((e.currentTarget as HTMLInputElement).value, test) } })}
					/>
					<Switch checked={!!f.ignore_case} label="Ignore case" onchange={(c) => setOp(i, { filter: { ...f, ignore_case: c || undefined } })} />
				{:else if n === 'pluck' || n === 'format'}
					<TextField
						label={n === 'pluck' ? 'Field' : 'Template'}
						mono
						value={typeof a === 'string' ? a : String((a as Record<string, unknown>)?.field ?? (a as Record<string, unknown>)?.template ?? '')}
						supporting={n === 'format' ? 'fields in {{ }}, e.g. http://{{ host }}:{{ port }}/' : ''}
						oninput={(e) => setOp(i, { [n]: (e.currentTarget as HTMLInputElement).value })}
					/>
				{:else if n === 'unique' || n === 'sort'}
					<TextField
						label="By field (optional{n === 'sort' ? ', -field for descending' : ''})"
						mono
						value={typeof a === 'string' ? a : ''}
						oninput={(e) => setOp(i, { [n]: (e.currentTarget as HTMLInputElement).value || true })}
					/>
				{:else if n === 'limit'}
					<TextField label="How many" type="number" value={typeof a === 'number' ? a : 10} oninput={(e) => setOp(i, { limit: Number((e.currentTarget as HTMLInputElement).value) || 1 })} />
				{/if}
			</div>
		{:else}
			<p class="body-small text-on-surface-variant">No operations: the input passes through.</p>
		{/each}
		<div class="flex flex-wrap gap-1.5">
			{#each OPS as op (op)}
				<button type="button" class="flex items-center gap-1 rounded-full border border-outline-variant px-2.5 py-1 label-medium text-on-surface hover:bg-surface-container-high" onclick={() => emit({ ops: [...transform.ops, structuredClone(TEMPLATES[op])] })}>
					<Icon name="add" size={14} />{op}
				</button>
			{/each}
		</div>
	</div>

	<div class="grid grid-cols-2 gap-2">
		<TextField label="Output name" mono value={transform.output?.name ?? 'items'} oninput={(e) => emit({ output: { ...transform.output, name: (e.currentTarget as HTMLInputElement).value || 'items' } })} />
		<TextField label="Output type (optional)" mono value={transform.output?.type ?? ''} placeholder="inferred" oninput={(e) => emit({ output: { ...transform.output, type: (e.currentTarget as HTMLInputElement).value || null } })} />
	</div>
</div>
