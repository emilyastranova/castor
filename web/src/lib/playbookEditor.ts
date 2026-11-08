/** Pure operations on a playbook spec for the visual editor. The editor keeps the spec the
 *  way an author writes it: `depends_on` holds only ordering the references don't already
 *  imply (the server adds those), and drawing uses `forDrawing` to put them back. */
import type { ArgSpec, CommandDefinition, PlaybookCondition, PlaybookSpec, PlaybookStepSpec } from '$lib/api';
import { conditionSteps, stepRefs } from '$lib/playbook';

export const STEP_ID_RE = /^[a-z0-9][a-z0-9_-]{0,62}$/;
const ref = (step: string, output: string) => `{{ steps.${step}.outputs.${output} }}`;
/** A deep copy; works on Svelte state proxies, which structuredClone can't copy. */
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));

/** Steps a step references through its args, transform input, for_each or condition. */
export function impliedDeps(step: PlaybookStepSpec): Set<string> {
	const out = new Set<string>();
	const values: unknown[] = step.transform ? [step.transform.input] : Object.values(step.args ?? {});
	if (step.for_each) values.push(step.for_each.items);
	for (const v of values) for (const r of stepRefs(Array.isArray(v) ? v : [v])) out.add(r.step);
	for (const id of conditionSteps(step.when)) out.add(id);
	return out;
}

/** The spec as authored: drop the dependencies references already imply. */
export function toAuthoring(spec: PlaybookSpec): PlaybookSpec {
	return {
		...clone(spec),
		steps: spec.steps.map((st) => {
			const implied = impliedDeps(st);
			return { ...clone(st), depends_on: (st.depends_on ?? []).filter((d) => !implied.has(d)) };
		})
	};
}

/** What the canvas draws: kinds filled in and every dependency, implied or not. */
export function forDrawing(spec: PlaybookSpec): PlaybookSpec {
	const ids = new Set(spec.steps.map((st) => st.id));
	return {
		...spec,
		steps: spec.steps.map((st) => ({
			...st,
			kind: st.transform ? 'transform' : 'command',
			depends_on: [...new Set([...(st.depends_on ?? []), ...impliedDeps(st)])].filter((d) => ids.has(d) && d !== st.id)
		}))
	};
}

export function uniqueStepId(spec: PlaybookSpec, base: string): string {
	const clean = base.toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^[^a-z0-9]+/, '').slice(0, 50) || 'step';
	const taken = new Set(spec.steps.map((st) => st.id));
	if (!taken.has(clean)) return clean;
	for (let i = 2; ; i++) if (!taken.has(`${clean}_${i}`)) return `${clean}_${i}`;
}

export function newCommandStep(spec: PlaybookSpec, cmd: CommandDefinition): PlaybookStepSpec {
	return {
		id: uniqueStepId(spec, cmd.id.replace(/-/g, '_')),
		name: cmd.name.replace(/^Demo: /, ''),
		kind: 'command',
		command_ref: cmd.id,
		transform: null,
		description: '',
		depends_on: [],
		args: {},
		skip_if_empty: false
	};
}

export function newTransformStep(spec: PlaybookSpec): PlaybookStepSpec {
	return {
		id: uniqueStepId(spec, 'transform'),
		name: 'Transform',
		kind: 'transform',
		command_ref: null,
		transform: { input: [], ops: [], output: { name: 'items', type: null } },
		description: '',
		depends_on: [],
		args: {},
		skip_if_empty: false
	};
}

export interface OutputPort {
	name: string;
	type: string | null;
}

/** The values a step publishes for others to bind to. */
export function outputsOf(step: PlaybookStepSpec, commands: Map<string, CommandDefinition>): OutputPort[] {
	if (step.transform) return [{ name: step.transform.output?.name || 'items', type: step.transform.output?.type ?? null }];
	const cmd = step.command_ref ? commands.get(step.command_ref) : undefined;
	return (cmd?.spec.outputs ?? []).filter((o) => o.parser).map((o) => ({ name: o.name, type: o.type }));
}

/** Every value other steps' outputs offer to `stepId` (it can't take its own). */
export function availableOutputs(spec: PlaybookSpec, stepId: string, commands: Map<string, CommandDefinition>) {
	return spec.steps
		.filter((st) => st.id !== stepId)
		.flatMap((st) => outputsOf(st, commands).map((o) => ({ step: st, output: o, ref: ref(st.id, o.name) })));
}

/** The arguments (best first) that take items of each output type. A generic `targets` takes
 *  URLs or plain strings only when its description says it does (nuclei's does; NetExec's,
 *  which wants hosts, doesn't). */
const ACCEPTS: Record<string, string[]> = {
	host: ['targets', 'target', 'hosts', 'host'],
	service: ['targets', 'target'],
	smb_host: ['targets', 'target'],
	url: ['urls', 'url', 'targets?url', 'target?url'],
	string: ['names', 'domains', 'domain', 'targets?domain']
};
const elementType = (type: string | null) => type?.match(/^list<(\w+)>$/)?.[1] ?? null;

export interface Wiring {
	output: OutputPort;
	arg: ArgSpec;
	/** A single-value argument fed a list: one card per item (`for_each`). */
	each: boolean;
}

/** How `cmd` would take `from`'s output, if it obviously can: the first output whose items
 *  match one of its arguments by name (targets, urls, names…). */
export function wiringFor(from: PlaybookStepSpec, cmd: CommandDefinition, commands: Map<string, CommandDefinition>): Wiring | null {
	for (const output of outputsOf(from, commands)) {
		const names = ACCEPTS[elementType(output.type) ?? ''] ?? [];
		for (const want of names) {
			const [name, mention] = want.split('?');
			const arg = cmd.spec.args.find(
				(a) =>
					a.name === name &&
					(a.type === 'list' || a.type === 'string') &&
					(!mention || a.description.toLowerCase().includes(mention))
			);
			if (arg) return { output, arg, each: arg.type === 'string' && !/s$/.test(arg.name) };
		}
	}
	return null;
}

/** Bind a new step to `fromId` the way `w` says. */
export function wireStep(step: PlaybookStepSpec, fromId: string, w: Wiring): PlaybookStepSpec {
	const next = clone(step);
	if (w.each) {
		next.for_each = { items: ref(fromId, w.output.name) };
		next.args = { ...next.args, [w.arg.name]: '{{ item }}' };
	} else {
		next.args = { ...next.args, [w.arg.name]: ref(fromId, w.output.name) };
		if (w.arg.required) next.skip_if_empty = true;
	}
	return next;
}

/** A transform fed by `fromId`'s first output. */
export function wireTransform(step: PlaybookStepSpec, from: PlaybookStepSpec, commands: Map<string, CommandDefinition>): PlaybookStepSpec {
	const out = outputsOf(from, commands)[0];
	if (!out || !step.transform) return step;
	const next = clone(step);
	next.transform!.input = ref(from.id, out.name);
	return next;
}

export interface ConnectOption {
	label: string;
	detail?: string;
	icon: string;
	apply: (spec: PlaybookSpec) => PlaybookSpec;
}

function updateStep(spec: PlaybookSpec, id: string, fn: (st: PlaybookStepSpec) => PlaybookStepSpec): PlaybookSpec {
	return { ...spec, steps: spec.steps.map((st) => (st.id === id ? fn(clone(st)) : st)) };
}

/** What dropping a connection from `fromId` onto `toId` can mean. */
export function connectOptions(
	spec: PlaybookSpec,
	fromId: string,
	toId: string,
	commands: Map<string, CommandDefinition>
): ConnectOption[] {
	const from = spec.steps.find((st) => st.id === fromId);
	const to = spec.steps.find((st) => st.id === toId);
	if (!from || !to) return [];
	const outputs = outputsOf(from, commands);
	const options: ConnectOption[] = [];
	if (to.transform) {
		for (const o of outputs)
			options.push({
				label: `Add ${o.name} to its input`,
				detail: o.type ?? undefined,
				icon: 'input',
				apply: (sp) =>
					updateStep(sp, toId, (st) => {
						const t = st.transform!;
						const list = Array.isArray(t.input) ? t.input : t.input ? [t.input] : [];
						t.input = [...list.filter((x) => x !== ref(fromId, o.name)), ref(fromId, o.name)];
						if (t.input.length === 1) t.input = t.input[0];
						return st;
					})
			});
	} else {
		const cmd = to.command_ref ? commands.get(to.command_ref) : undefined;
		// Required arguments first: they're what a connection usually feeds.
		const args = (cmd?.spec.args ?? [])
			.filter((a) => a.type === 'string' || a.type === 'list')
			.sort((a, b) => Number(b.required) - Number(a.required));
		for (const o of outputs) {
			for (const a of args)
				options.push({
					label: `${o.name} → ${a.name}`,
					detail: [o.type, a.description].filter(Boolean).join(' · '),
					icon: 'arrow_forward',
					apply: (sp) =>
						updateStep(sp, toId, (st) => {
							st.args = { ...st.args, [a.name]: ref(fromId, o.name) };
							if (a.required && !st.for_each) st.skip_if_empty = true;
							return st;
						})
				});
			if (!to.for_each)
				options.push({
					label: `Once for each item of ${o.name}`,
					detail: 'one card per item; bind {{ item }} to an argument',
					icon: 'stacks',
					apply: (sp) =>
						updateStep(sp, toId, (st) => {
							st.for_each = { items: ref(fromId, o.name) };
							const target = args.find((x) => x.required) ?? args[0];
							if (target) st.args = { ...st.args, [target.name]: '{{ item }}' };
							return st;
						})
				});
		}
	}
	options.push({
		label: 'Run after it (no data)',
		icon: 'schedule',
		apply: (sp) => updateStep(sp, toId, (st) => ({ ...st, depends_on: [...new Set([...(st.depends_on ?? []), fromId])] }))
	});
	options.push({
		label: 'Run only if it fails',
		detail: 'a failure branch',
		icon: 'alt_route',
		apply: (sp) => updateStep(sp, toId, (st) => ({ ...st, when: { step: fromId, status: 'failed' } }))
	});
	return options;
}

const mentions = (v: unknown, id: string) => typeof v === 'string' && v.includes(`steps.${id}.`);

/** Remove everything that connects `fromId` to `toId`. Returns whether a condition still
 *  refers to it (conditions are left alone). */
export function disconnect(spec: PlaybookSpec, fromId: string, toId: string): { spec: PlaybookSpec; conditionLeft: boolean } {
	let conditionLeft = false;
	const next = updateStep(spec, toId, (st) => {
		st.args = Object.fromEntries(Object.entries(st.args ?? {}).filter(([, v]) => !mentions(v, fromId)));
		if (st.transform) {
			const list = (Array.isArray(st.transform.input) ? st.transform.input : [st.transform.input]).filter((x) => x && !mentions(x, fromId));
			st.transform.input = list.length === 1 ? list[0] : list;
		}
		if (st.for_each && mentions(st.for_each.items, fromId)) {
			st.for_each = null;
			st.args = Object.fromEntries(Object.entries(st.args).filter(([, v]) => !(typeof v === 'string' && v.includes('{{ item'))));
		}
		st.depends_on = (st.depends_on ?? []).filter((d) => d !== fromId);
		conditionLeft = conditionSteps(st.when).has(fromId);
		return st;
	});
	return { spec: next, conditionLeft };
}

/** A copy of a step that reads the same inputs (nothing depends on the copy yet). */
export function duplicateStep(spec: PlaybookSpec, id: string): PlaybookStepSpec | null {
	const st = spec.steps.find((x) => x.id === id);
	if (!st) return null;
	const copy = clone(st);
	copy.id = uniqueStepId(spec, `${st.id}_copy`);
	copy.name = `${st.name} (copy)`;
	return copy;
}

export function deleteStep(spec: PlaybookSpec, id: string): PlaybookSpec {
	let next: PlaybookSpec = { ...spec, steps: spec.steps.filter((st) => st.id !== id) };
	for (const st of next.steps) {
		next = disconnect(next, id, st.id).spec;
		if (conditionSteps(st.when).has(id)) next = updateStep(next, st.id, (x) => ({ ...x, when: null }));
	}
	if (next.layout) next = { ...next, layout: Object.fromEntries(Object.entries(next.layout).filter(([k]) => k !== id)) };
	return next;
}

function mapStrings<T>(value: T, fn: (s: string) => string): T {
	if (typeof value === 'string') return fn(value) as T;
	if (Array.isArray(value)) return value.map((v) => mapStrings(v, fn)) as T;
	if (value && typeof value === 'object')
		return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, mapStrings(v, fn)])) as T;
	return value;
}

function renameInCondition(c: PlaybookCondition | null | undefined, from: string, to: string): PlaybookCondition | null | undefined {
	if (!c) return c;
	if (Array.isArray(c)) return c.map((x) => renameInCondition(x, from, to)!) as PlaybookCondition;
	const o = { ...(c as Record<string, unknown>) };
	if (o.step === from) o.step = to;
	for (const key of ['all', 'any'])
		if (Array.isArray(o[key])) o[key] = (o[key] as PlaybookCondition[]).map((x) => renameInCondition(x, from, to));
	if (o.not) o.not = renameInCondition(o.not as PlaybookCondition, from, to);
	return o as PlaybookCondition;
}

/** Rename a step and every reference to it. */
export function renameStep(spec: PlaybookSpec, from: string, to: string): PlaybookSpec {
	const pattern = new RegExp(`steps\\.${from.replace(/[-]/g, '\\-')}\\.`, 'g');
	const steps = spec.steps.map((st) => {
		const next = mapStrings(st, (s) => s.replace(pattern, `steps.${to}.`));
		next.id = st.id === from ? to : st.id;
		next.depends_on = (next.depends_on ?? []).map((d) => (d === from ? to : d));
		next.when = renameInCondition(next.when, from, to) ?? null;
		return next;
	});
	const layout = spec.layout ? Object.fromEntries(Object.entries(spec.layout).map(([k, v]) => [k === from ? to : k, v])) : spec.layout;
	return { ...spec, steps, layout };
}

/** Problems from the server, by step id ("step x: …" or "steps.3.…"), plus the rest. */
export function errorsByStep(errors: string[], spec: PlaybookSpec): { byStep: Record<string, string[]>; general: string[] } {
	const byStep: Record<string, string[]> = {};
	const general: string[] = [];
	for (const e of errors) {
		// Parse errors look like "steps.2.args: …" or "steps.2: step x: …"; library checks
		// like "step x: …".
		const indexed = e.match(/^steps\.(\d+)[.:]/);
		const rest = e.replace(/^steps\.\d+(\.[\w.]+)?: /, '');
		const named = rest.match(/^step ([a-z0-9][a-z0-9_-]*)\b/);
		const id = named?.[1] ?? (indexed ? spec.steps[Number(indexed[1])]?.id : undefined);
		if (id && spec.steps.some((st) => st.id === id)) (byStep[id] ??= []).push(rest);
		else general.push(e);
	}
	return { byStep, general };
}
