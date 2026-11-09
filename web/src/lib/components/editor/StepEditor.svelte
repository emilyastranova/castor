<script lang="ts">
	import type { ArgSpec, CommandDefinition, PlaybookCondition, PlaybookSpec, PlaybookStepSpec } from '$lib/api';
	import { Button, Icon, IconButton, Select, Switch, TextField } from '$lib/m3';
	import { describeCondition, shortRef } from '$lib/playbook';
	import { STEP_ID_RE, availableOutputs, outputsOf } from '$lib/playbookEditor';
	import OpsEditor from './OpsEditor.svelte';

	/** Properties of one step in the visual editor. Every change is emitted at once. */
	interface Props {
		spec: PlaybookSpec;
		step: PlaybookStepSpec;
		commands: Map<string, CommandDefinition>;
		errors?: string[];
		onchange: (step: PlaybookStepSpec) => void;
		onrename: (from: string, to: string) => void;
		ondelete: () => void;
	}
	let { spec, step, commands, errors = [], onchange, onrename, ondelete }: Props = $props();

	let cmd = $derived(step.command_ref ? commands.get(step.command_ref) : undefined);
	let upstream = $derived(availableOutputs(spec, step.id, commands));
	let refOptions = $derived([
		...spec.inputs.map((i) => ({ value: `{{ inputs.${i.name} }}`, label: `input · ${i.name}` })),
		...upstream.map((u) => ({ value: u.ref, label: `${u.step.name} · ${u.output.name}` }))
	]);

	function set<K extends keyof PlaybookStepSpec>(key: K, value: PlaybookStepSpec[K]) {
		onchange({ ...step, [key]: value });
	}

	// ---------- id ----------
	let idDraft = $state('');
	let idFor = '';
	$effect(() => {
		if (step.id !== idFor) {
			idFor = step.id;
			idDraft = step.id;
		}
	});
	let idError = $derived(
		idDraft === step.id
			? null
			: !STEP_ID_RE.test(idDraft)
				? 'lowercase letters, digits, - and _'
				: spec.steps.some((s) => s.id === idDraft)
					? 'another step has this id'
					: null
	);
	function commitId() {
		if (idDraft !== step.id && !idError) onrename(step.id, idDraft);
		else idDraft = step.id;
	}

	// ---------- arguments ----------
	type Mode = 'value' | 'input' | 'output' | 'item';
	const EXACT = /^\{\{\s*(inputs\.[A-Za-z0-9_]+|steps\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_]+)\s*\}\}$/;
	function modeOf(v: unknown): Mode {
		if (typeof v !== 'string') return 'value';
		if (/\{\{\s*item/.test(v)) return 'item';
		const m = v.trim().match(EXACT);
		return m ? (m[1].startsWith('inputs.') ? 'input' : 'output') : 'value';
	}
	function setArg(name: string, value: unknown) {
		const args = { ...step.args };
		if (value === '' || value === undefined || value === null) delete args[name];
		else args[name] = value;
		set('args', args);
	}
	function setMode(a: ArgSpec, mode: Mode) {
		if (mode === 'value') setArg(a.name, a.default ?? '');
		else if (mode === 'input') setArg(a.name, `{{ inputs.${spec.inputs[0]?.name ?? 'name'} }}`);
		else if (mode === 'output') setArg(a.name, upstream[0]?.ref ?? '');
		else setArg(a.name, '{{ item }}');
	}
	const modeOptions = (a: ArgSpec) => [
		{ value: 'value', label: 'Value' },
		...(spec.inputs.length ? [{ value: 'input', label: 'Playbook input' }] : []),
		...(upstream.length && (a.type === 'string' || a.type === 'list') ? [{ value: 'output', label: 'Step output' }] : []),
		...(step.for_each ? [{ value: 'item', label: 'Current item' }] : [])
	];

	// ---------- condition (simple forms; anything else is edited in YAML) ----------
	type CondForm = { kind: 'none' } | { kind: 'value'; value: string; test: string; arg: string } | { kind: 'status'; step: string; status: string } | { kind: 'complex' };
	const VALUE_TESTS = [
		{ value: 'not_empty', label: 'is not empty' },
		{ value: 'empty', label: 'is empty' },
		{ value: 'count_gt', label: 'has more than' },
		{ value: 'count_lt', label: 'has fewer than' },
		{ value: 'equals', label: 'equals' },
		{ value: 'contains', label: 'contains' },
		{ value: 'matches', label: 'matches regex' }
	];
	function condForm(c: PlaybookCondition | null | undefined): CondForm {
		if (!c) return { kind: 'none' };
		if (Array.isArray(c)) return { kind: 'complex' };
		const o = c as Record<string, unknown>;
		if (typeof o.step === 'string' && 'status' in o && Object.keys(o).length === 2 && typeof o.status === 'string') return { kind: 'status', step: o.step, status: o.status };
		if ('value' in o) {
			const keys = Object.keys(o).filter((k) => k !== 'value');
			if (keys.length !== 1) return { kind: 'complex' };
			const k = keys[0];
			const v = o[k];
			if (k === 'empty') return { kind: 'value', value: String(o.value), test: v ? 'empty' : 'not_empty', arg: '' };
			if (k === 'count' && v && typeof v === 'object' && Object.keys(v).length === 1) {
				const [op, n] = Object.entries(v)[0];
				if (op === 'gt' || op === 'lt') return { kind: 'value', value: String(o.value), test: `count_${op}`, arg: String(n) };
			}
			if (['equals', 'contains', 'matches'].includes(k) && (typeof v === 'string' || typeof v === 'number')) return { kind: 'value', value: String(o.value), test: k, arg: String(v) };
		}
		return { kind: 'complex' };
	}
	function buildCond(f: CondForm): PlaybookCondition | null {
		if (f.kind === 'status') return { step: f.step, status: f.status };
		if (f.kind !== 'value') return null;
		const t = f.test;
		const n = Number(f.arg);
		const test: Record<string, unknown> =
			t === 'empty' ? { empty: true } : t === 'not_empty' ? { empty: false } : t === 'count_gt' ? { count: { gt: n || 0 } } : t === 'count_lt' ? { count: { lt: n || 0 } } : { [t]: f.arg };
		return { value: f.value, ...test };
	}
	let cond = $derived(condForm(step.when));
	function setCond(f: CondForm) {
		set('when', buildCond(f));
	}

	// ---------- expect (simple: outputs that must not be empty, a log regex) ----------
	let expectSimple = $derived.by(() => {
		const e = step.expect;
		if (!e) return true;
		const outs = Object.values(e.outputs ?? {}).every((t) => Object.keys(t).length === 1 && t.empty === false);
		const log = !e.log || (Object.keys(e.log).length === 1 && typeof e.log.not_matches === 'string');
		return outs && log;
	});
	function setExpect(outputs: string[], logRegex: string) {
		const next: NonNullable<PlaybookStepSpec['expect']> = {};
		if (outputs.length) next.outputs = Object.fromEntries(outputs.map((o) => [o, { empty: false }]));
		if (logRegex.trim()) next.log = { not_matches: logRegex.trim() };
		set('expect', next.outputs || next.log ? next : null);
	}
	let expectOutputs = $derived(Object.keys(step.expect?.outputs ?? {}));
	let expectLog = $derived(typeof step.expect?.log?.not_matches === 'string' ? (step.expect.log.not_matches as string) : '');

	const otherSteps = $derived(spec.steps.filter((s) => s.id !== step.id).map((s) => ({ value: s.id, label: s.name })));

	// The sections that change how a step behaves fold to one line saying what's set, so the
	// arguments stay in view. Which are open is remembered in this browser, for every step.
	let open = $state<Set<string>>(loadOpen());
	function loadOpen(): Set<string> {
		try {
			return new Set(JSON.parse(localStorage.getItem('castor.inspector.open') ?? '[]'));
		} catch {
			return new Set();
		}
	}
	function toggle(key: string) {
		const next = new Set(open);
		if (!next.delete(key)) next.add(key);
		open = next;
		try {
			localStorage.setItem('castor.inspector.open', JSON.stringify([...next]));
		} catch {
			/* only for this tab */
		}
	}
	const stepName = (id: string) => spec.steps.find((s) => s.id === id)?.name ?? id;
	let whenSummary = $derived.by(() => {
		let text =
			cond.kind === 'complex'
				? `if ${describeCondition(step.when)}`
				: cond.kind === 'value'
					? `if ${shortRef(cond.value)} ${VALUE_TESTS.find((t) => t.value === cond.test)?.label ?? cond.test}${cond.arg ? ` ${cond.arg}` : ''}`
					: cond.kind === 'status'
						? `if ${stepName(cond.step)} ${cond.status.replace('_', ' ')}`
						: 'Always';
		if (step.join === 'any') text += ', when one input worked';
		if (step.join === 'always') text += ', whatever happened before';
		if (step.depends_on?.length) text += ` · after ${step.depends_on.map(stepName).join(', ')}`;
		return text;
	});
	let failureSummary = $derived.by(() => {
		const parts = [{ stop: 'Stop', continue: 'Continue', ask: 'Ask a person', ai: 'AI decides' }[step.on_failure ?? 'stop']];
		if (step.skip_if_empty) parts.push('skips when empty');
		if (expectOutputs.length || expectLog) parts.push('has expectations');
		return parts.join(' · ');
	});
	let eachSummary = $derived(step.for_each ? `one card per item of ${shortRef(step.for_each.items)}` : 'Off');
</script>

{#snippet section(title: string, icon: string)}
	<div class="mt-2 flex items-center gap-2 border-t border-outline-variant/60 pt-3">
		<Icon name={icon} size={18} class="text-on-surface-variant" />
		<span class="label-large text-on-surface">{title}</span>
	</div>
{/snippet}

{#snippet fold(key: string, title: string, icon: string, summary: string)}
	{@const isOpen = open.has(key)}
	<button
		type="button"
		class="state-layer -mx-2 mt-1 flex items-center gap-2 rounded-md border-t border-outline-variant/60 px-2 pt-3 pb-1.5 text-left"
		aria-expanded={isOpen}
		onclick={() => toggle(key)}
	>
		<Icon name={icon} size={18} class="shrink-0 text-on-surface-variant" />
		<span class="min-w-0 flex-1">
			<span class="block label-large text-on-surface">{title}</span>
			{#if !isOpen}<span class="block truncate body-small text-on-surface-variant">{summary}</span>{/if}
		</span>
		<Icon name="expand_more" size={20} class="shrink-0 text-on-surface-variant transition-transform duration-150 {isOpen ? 'rotate-180' : ''}" />
	</button>
{/snippet}

<div class="flex flex-col gap-3">
	<div class="flex items-start gap-2">
		<Icon name={step.transform ? 'filter_alt' : 'terminal'} class="mt-1 {step.transform ? 'text-tertiary' : 'text-primary'}" />
		<div class="min-w-0 flex-1">
			<p class="title-medium text-on-surface">{step.transform ? 'Transform' : (cmd?.name ?? step.command_ref)}</p>
			{#if cmd}<p class="body-small text-on-surface-variant">{cmd.spec.description}</p>{/if}
			{#if step.command_ref && !cmd}<p class="body-small text-error">{step.command_ref} isn't in the command library</p>{/if}
		</div>
		<IconButton icon="delete" label="Delete step" onclick={ondelete} />
	</div>

	{#if errors.length}
		<ul class="flex flex-col gap-1 rounded-md bg-error-container/40 px-3 py-2 body-small text-on-surface">
			{#each errors as e (e)}<li class="flex gap-1.5"><Icon name="error" size={16} class="mt-px shrink-0 text-error" />{e.replace(/^step [a-z0-9_-]+: /, '').replace(/^\w/, (c) => c.toUpperCase())}</li>{/each}
		</ul>
	{/if}

	<TextField label="Name" value={step.name} oninput={(e) => set('name', (e.currentTarget as HTMLInputElement).value)} />
	{@render fold('details', 'Id and description', 'badge', `${step.id}${step.description ? ` · ${step.description}` : ''}`)}
	{#if open.has('details') || idError}
		<TextField
			label="Id"
			mono
			bind:value={idDraft}
			error={idError}
			supporting={idError ? '' : 'used in references; renaming updates them'}
			onblur={commitId}
			onkeydown={(e) => e.key === 'Enter' && commitId()}
		/>
		<TextField label="Description" multiline rows={2} value={step.description} oninput={(e) => set('description', (e.currentTarget as HTMLTextAreaElement).value)} />
	{/if}

	{#if step.transform}
		{@render section('Transform', 'filter_alt')}
		<OpsEditor
			transform={step.transform}
			inputs={refOptions}
			onchange={(t) => set('transform', t)}
		/>
	{:else if cmd}
		{@render section('Arguments', 'tune')}
		{#each cmd.spec.args as a (a.name)}
			{@const v = step.args?.[a.name]}
			{@const mode = modeOf(v)}
			<div class="flex flex-col gap-1.5 rounded-md border border-outline-variant/60 p-2.5">
				<div class="flex items-center gap-2">
					<span class="font-mono text-[12.5px] text-on-surface">{a.name}{a.required && a.default == null ? ' *' : ''}</span>
					<span class="body-small text-on-surface-variant">{a.type}</span>
					<div class="ml-auto w-40">
						<Select label="From" value={mode} options={modeOptions(a)} onchange={(m) => setMode(a, m as Mode)} />
					</div>
				</div>
				{#if a.description}<p class="body-small text-on-surface-variant">{a.description}</p>{/if}
				{#if mode === 'input'}
					<Select label="Input" value={String(v)} options={spec.inputs.map((i) => ({ value: `{{ inputs.${i.name} }}`, label: i.name }))} onchange={(x) => setArg(a.name, x)} />
				{:else if mode === 'output'}
					<Select label="Output" value={String(v)} options={upstream.map((u) => ({ value: u.ref, label: `${u.step.name} · ${u.output.name}` }))} onchange={(x) => setArg(a.name, x)} />
				{:else if a.type === 'boolean' && mode === 'value'}
					<Switch checked={v === true || v === 'true'} label={v === true || v === 'true' ? 'On' : 'Off'} onchange={(c) => setArg(a.name, c)} />
				{:else if a.choices?.length && mode === 'value'}
					<Select label="Value" value={v == null ? '' : String(v)} options={[{ value: '', label: a.default != null ? `default (${a.default})` : '—' }, ...a.choices.map((c) => ({ value: c, label: c }))]} onchange={(x) => setArg(a.name, x)} />
				{:else}
					<TextField
						label={mode === 'item' ? 'Template' : 'Value'}
						mono
						value={v == null ? '' : String(v)}
						placeholder={a.default != null ? `default: ${a.default}` : String(a.example ?? '')}
						supporting={mode === 'item' ? '{{ item }} or {{ item.<field> }}' : ''}
						oninput={(e) => setArg(a.name, (e.currentTarget as HTMLInputElement).value)}
					/>
				{/if}
			</div>
		{/each}
		{#if !cmd.spec.args.length}<p class="body-small text-on-surface-variant">This command takes no arguments.</p>{/if}

		{#if cmd.spec.vars?.length}
			{@render section('Variables', 'key')}
			{#each cmd.spec.vars as slot (slot.name)}
				{@const bound = step.vars?.[slot.name] ?? ''}
				{@const matching = spec.inputs.filter((i) => i.type === 'varset' && i.kind === slot.kind)}
				<Select
					label="{slot.name} ({slot.kind}){slot.required ? ' *' : ''}"
					value={bound}
					options={[
						{ value: '', label: 'Not bound' },
						...matching.map((i) => ({ value: `{{ inputs.${i.name} }}`, label: `input · ${i.name}` })),
						...(bound && !bound.startsWith('{{') ? [{ value: bound, label: `set · ${bound}` }] : [])
					]}
					supporting={matching.length ? '' : `add a varset input of kind ${slot.kind} under Playbook to choose it per run`}
					onchange={(x) => set('vars', Object.fromEntries(Object.entries({ ...step.vars, [slot.name]: x }).filter(([, val]) => val)))}
				/>
			{/each}
		{/if}

		{@render fold('each', 'Run once per item', 'stacks', eachSummary)}
		{#if open.has('each')}
		<Switch
			checked={!!step.for_each}
			label={step.for_each ? 'One card and job per item' : 'Off'}
			onchange={(on) => set('for_each', on ? { items: upstream[0]?.ref ?? `{{ inputs.${spec.inputs[0]?.name ?? 'items'} }}` } : null)}
		/>
		{#if step.for_each}
			<Select label="Items" value={step.for_each.items} options={refOptions} onchange={(x) => set('for_each', { ...step.for_each!, items: x })} />
			<div class="grid grid-cols-2 gap-2">
				<TextField label="At most at once" type="number" value={step.for_each.concurrency ?? ''} placeholder="no limit" oninput={(e) => set('for_each', { ...step.for_each!, concurrency: Number((e.currentTarget as HTMLInputElement).value) || null })} />
				<TextField label="Max items" type="number" value={step.for_each.max_items ?? 100} oninput={(e) => set('for_each', { ...step.for_each!, max_items: Number((e.currentTarget as HTMLInputElement).value) || 100 })} />
			</div>
			<p class="body-small text-on-surface-variant">Use “Current item” for the argument each card should get.</p>
		{/if}
		{/if}
	{/if}

	{@render fold('when', 'When it runs', 'alt_route', whenSummary)}
	{#if open.has('when')}
	{#if cond.kind === 'complex'}
		<p class="rounded-md bg-surface-container px-3 py-2 font-mono text-[12px] text-on-surface">if {describeCondition(step.when)}</p>
		<div class="flex gap-2">
			<span class="flex-1 body-small text-on-surface-variant">This condition is edited in the YAML tab.</span>
			<Button variant="text" onclick={() => set('when', null)}>Remove</Button>
		</div>
	{:else}
		<Select
			label="Condition"
			value={cond.kind}
			options={[
				{ value: 'none', label: 'Always (when its inputs are ready)' },
				{ value: 'value', label: 'Only if a value…' },
				...(otherSteps.length ? [{ value: 'status', label: 'Only if a step ended as…' }] : [])
			]}
			onchange={(k) =>
				setCond(
					k === 'value'
						? { kind: 'value', value: refOptions[0]?.value ?? '', test: 'not_empty', arg: '' }
						: k === 'status'
							? { kind: 'status', step: otherSteps[0].value, status: 'failed' }
							: { kind: 'none' }
				)}
		/>
		{#if cond.kind === 'value'}
			{@const c = cond}
			<Select label="Value" value={c.value} options={refOptions} onchange={(x) => setCond({ ...c, value: x })} />
			<div class="grid grid-cols-2 gap-2">
				<Select label="Test" value={c.test} options={VALUE_TESTS} onchange={(x) => setCond({ ...c, test: x })} />
				{#if c.test !== 'empty' && c.test !== 'not_empty'}
					<TextField label={c.test.startsWith('count') ? 'Items' : 'Text'} value={c.arg} mono oninput={(e) => setCond({ ...c, arg: (e.currentTarget as HTMLInputElement).value })} />
				{/if}
			</div>
		{:else if cond.kind === 'status'}
			{@const c = cond}
			<div class="grid grid-cols-2 gap-2">
				<Select label="Step" value={c.step} options={otherSteps} onchange={(x) => setCond({ ...c, step: x })} />
				<Select
					label="Ended as"
					value={c.status}
					options={['failed', 'succeeded', 'skipped', 'not_taken', 'cancelled'].map((x) => ({ value: x, label: x.replace('_', ' ') }))}
					onchange={(x) => setCond({ ...c, status: x })}
				/>
			</div>
		{/if}
	{/if}
	<Select
		label="Starts when its inputs"
		value={step.join ?? 'all'}
		options={[
			{ value: 'all', label: 'all succeeded (default)' },
			{ value: 'any', label: 'have ended, one or more worked (branches meet)' },
			{ value: 'always', label: 'have ended, whatever happened' }
		]}
		onchange={(x) => set('join', x as PlaybookStepSpec['join'])}
	/>
	{#if step.depends_on?.length}
		<div class="flex flex-wrap items-center gap-1.5">
			<span class="body-small text-on-surface-variant">Also runs after:</span>
			{#each step.depends_on as d (d)}
				<span class="flex items-center gap-1 rounded-full bg-surface-container-high py-0.5 pr-1 pl-2.5 label-medium text-on-surface">
					{spec.steps.find((s) => s.id === d)?.name ?? d}
					<button type="button" class="rounded-full p-0.5 hover:bg-surface-container-highest" aria-label="Remove {d}" onclick={() => set('depends_on', step.depends_on.filter((x) => x !== d))}>
						<Icon name="close" size={14} />
					</button>
				</span>
			{/each}
		</div>
	{/if}
	{/if}

	{@render fold('failure', 'If something goes wrong', 'report', failureSummary)}
	{#if open.has('failure')}
	<Select
		label="On failure"
		value={step.on_failure ?? 'stop'}
		options={[
			{ value: 'stop', label: 'Stop: block the steps after it' },
			{ value: 'continue', label: 'Continue with whatever exists' },
			{ value: 'ask', label: 'Pause and ask (notifies people)' },
			{ value: 'ai', label: 'Pause and let AI decide (people if it can’t)' }
		]}
		onchange={(x) => set('on_failure', x as PlaybookStepSpec['on_failure'])}
	/>
	{#if step.on_failure === 'ai'}
		{@const ai = step.ai ?? {}}
		{@const allow = ai.allow ?? ['retry', 'skip']}
		<div class="flex flex-col gap-2 rounded-md border border-outline-variant p-3">
			<p class="body-small text-on-surface-variant">The AI may, on its own:</p>
			<div class="flex flex-wrap gap-x-4 gap-y-1">
				{#each ['retry', 'continue', 'skip', 'fail'] as const as a (a)}
					<Switch checked={allow.includes(a)} label={a} onchange={(on) => set('ai', { ...ai, allow: on ? [...allow, a] : allow.filter((x) => x !== a) })} />
				{/each}
			</div>
			<Switch checked={!!ai.edit_command} label="Retry with a corrected command" onchange={(on) => set('ai', { ...ai, edit_command: on })} />
			<p class="-mt-1 px-1 body-small text-on-surface-variant">Off: a changed command waits for a person. The output it reads can come from the target, which could try to steer it.</p>
			<Switch checked={ai.draft_parsers ?? true} label="Read unexpected output with a parser it drafts" onchange={(on) => set('ai', { ...ai, draft_parsers: on })} />
			<p class="-mt-1 px-1 body-small text-on-surface-variant">When the output isn’t in the format its parser reads, the AI writes a parser, checks it on that output and the run carries on. For this run only: keeping it is up to a person.</p>
			<div class="flex gap-2">
				<TextField label="Sure at least (%)" type="number" class="w-36" value={String(Math.round((ai.confidence ?? 0.7) * 100))} oninput={(e) => set('ai', { ...ai, confidence: Math.min(100, Math.max(0, Number((e.currentTarget as HTMLInputElement).value) || 0)) / 100 })} />
				<TextField label="Retries at most" type="number" class="w-36" value={String(ai.max_retries ?? 2)} oninput={(e) => set('ai', { ...ai, max_retries: Math.min(10, Math.max(0, Math.round(Number((e.currentTarget as HTMLInputElement).value) || 0))) })} />
			</div>
			<TextField label="Guidance for the AI (optional)" multiline rows={2} value={ai.instructions ?? ''} oninput={(e) => set('ai', { ...ai, instructions: (e.currentTarget as HTMLTextAreaElement).value })} placeholder="e.g. a timeout here usually means retry; no hosts up is fine" />
		</div>
	{/if}
	{#if !step.transform}
		<Switch checked={step.skip_if_empty} label="Skip when an upstream value is empty" onchange={(c) => set('skip_if_empty', c)} />
		{#if expectSimple}
			{@const outs = cmd ? outputsOf(step, commands) : []}
			{#if outs.length}
				<div class="flex flex-col gap-1.5">
					<span class="body-small text-on-surface-variant">Unexpected (counts as a failure) when these outputs are empty:</span>
					<div class="flex flex-wrap gap-x-4 gap-y-1.5">
						{#each outs as o (o.name)}
							<Switch
								checked={expectOutputs.includes(o.name)}
								label={o.name}
								onchange={(c) => setExpect(c ? [...expectOutputs, o.name] : expectOutputs.filter((x) => x !== o.name), expectLog)}
							/>
						{/each}
					</div>
				</div>
			{/if}
			<TextField
				label="…or when the log matches"
				mono
				value={expectLog}
				placeholder="STATUS_ACCESS_DENIED|Connection refused"
				oninput={(e) => setExpect(expectOutputs, (e.currentTarget as HTMLInputElement).value)}
			/>
		{:else}
			<p class="body-small text-on-surface-variant">This step's <span class="font-mono">expect</span> is edited in the YAML tab.</p>
		{/if}
	{/if}
	{/if}

	{#if step.for_each}
		<p class="body-small text-on-surface-variant">Items come from <span class="font-mono">{shortRef(step.for_each.items)}</span>.</p>
	{/if}
</div>
