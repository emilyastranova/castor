/** Display helpers shared by the playbook visualizer and the playbooks page. */
import type { PlaybookCondition, PlaybookStepSpec, PlaybookStepStatus, TransformSpec } from '$lib/api';

export interface StatusMeta {
	label: string;
	icon: string;
	/** Text colour class for the label and icon. */
	text: string;
	/** Background class for the node's status strip. */
	strip: string;
}

export const STEP_STATUS: Record<PlaybookStepStatus, StatusMeta> = {
	pending: { label: 'Waiting', icon: 'schedule', text: 'text-on-surface-variant', strip: 'bg-outline-variant' },
	queued: { label: 'Queued', icon: 'hourglass_empty', text: 'text-tertiary', strip: 'bg-tertiary' },
	running: { label: 'Running', icon: 'progress_activity', text: 'text-primary', strip: 'bg-primary' },
	succeeded: { label: 'Done', icon: 'check_circle', text: 'text-primary', strip: 'bg-primary' },
	failed: { label: 'Failed', icon: 'error', text: 'text-error', strip: 'bg-error' },
	cancelled: { label: 'Cancelled', icon: 'cancel', text: 'text-on-surface-variant', strip: 'bg-outline' },
	skipped: { label: 'Skipped', icon: 'redo', text: 'text-on-surface-variant', strip: 'bg-outline' },
	not_taken: { label: 'Not taken', icon: 'alt_route', text: 'text-on-surface-variant', strip: 'bg-outline-variant' },
	attention: { label: 'Needs you', icon: 'front_hand', text: 'text-tertiary', strip: 'bg-tertiary' },
	blocked: { label: 'Blocked', icon: 'block', text: 'text-error', strip: 'bg-error/40' }
};

/** Statuses after which a step never moves again (attention is waiting, not ended). */
export const FINAL: ReadonlySet<PlaybookStepStatus> = new Set(['succeeded', 'failed', 'cancelled', 'skipped', 'not_taken', 'blocked']);

/** The MIME type the editor's palette blocks use for drag and drop onto the canvas. */
export const BLOCK_MIME = 'application/x-castor-block';

/** Statuses that let dependent steps go ahead. */
export const SATISFIED: ReadonlySet<PlaybookStepStatus> = new Set(['succeeded', 'skipped']);

const REF_RE = /steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_]+)/g;

/** `{ step, output }` for every `steps.<id>.outputs.<name>` inside a binding value. */
export function stepRefs(value: unknown): { step: string; output: string }[] {
	const texts = Array.isArray(value) ? value : [value];
	const out: { step: string; output: string }[] = [];
	for (const t of texts) {
		if (typeof t !== 'string') continue;
		for (const m of t.matchAll(REF_RE)) out.push({ step: m[1], output: m[2] });
	}
	return out;
}

/** Where a step takes its values from: arg name → binding text (transforms: `input`). */
export function stepBindings(step: PlaybookStepSpec): [string, unknown][] {
	if (step.transform) return [['input', step.transform.input]];
	return Object.entries(step.args || {});
}

const FILTER_SYMBOLS: Record<string, string> = {
	equals: '=',
	not_equals: '≠',
	in: '∈',
	not_in: '∉',
	contains: 'contains',
	matches: '~'
};

const show = (v: unknown) => (Array.isArray(v) ? v.join(', ') : String(v));

/** One op as a short phrase, matching the server's describe(). */
export function describeOp(raw: Record<string, unknown> | string): string {
	const [name, arg] = typeof raw === 'string' ? [raw, true] : Object.entries(raw)[0] ?? ['?', null];
	const opts = (arg && typeof arg === 'object' && !Array.isArray(arg) ? arg : {}) as Record<string, unknown>;
	switch (name) {
		case 'filter': {
			const subject = (opts.field as string) || 'value';
			if ('exists' in opts) return `${subject} ${opts.exists ? 'exists' : 'missing'}`;
			const test = Object.keys(FILTER_SYMBOLS).find((t) => t in opts);
			return test ? `${subject} ${FILTER_SYMBOLS[test]} ${show(opts[test])}` : 'filter';
		}
		case 'pluck':
			return `pluck ${typeof arg === 'string' ? arg : opts.field}`;
		case 'format':
			return `format ${typeof arg === 'string' ? arg : opts.template}`;
		case 'unique': {
			const field = typeof arg === 'string' ? arg : opts.field;
			return field ? `unique by ${field}` : 'unique';
		}
		case 'sort': {
			let field = typeof arg === 'string' ? arg : (opts.field as string | undefined);
			let desc = Boolean(opts.desc);
			if (field?.startsWith('-')) {
				field = field.slice(1);
				desc = true;
			}
			return `sort ${field || 'value'}${desc ? ' desc' : ''}`;
		}
		case 'limit':
			return `first ${typeof arg === 'number' ? arg : opts.count}`;
		default:
			return name;
	}
}

/** describeOp without the leading op name ("pluck host" → "host"), for name/detail layouts. */
export function opDetail(raw: Record<string, unknown> | string): string {
	const name = opName(raw);
	const text = describeOp(raw);
	return text === name ? '' : text.startsWith(name + ' ') ? text.slice(name.length + 1) : text;
}

/** The op's name alone, for compact chips. */
export function opName(raw: Record<string, unknown> | string): string {
	return typeof raw === 'string' ? raw : (Object.keys(raw)[0] ?? '?');
}

export function transformInputs(t: TransformSpec): string[] {
	return Array.isArray(t.input) ? t.input : [t.input];
}

// ---------- conditions ----------

const VALUE_TESTS: [string, (v: unknown) => string][] = [
	['empty', (v) => (v ? 'is empty' : 'is not empty')],
	['count', (v) => `count ${countText(v)}`],
	['equals', (v) => `= ${show(v)}`],
	['not_equals', (v) => `≠ ${show(v)}`],
	['in', (v) => `∈ [${show(v)}]`],
	['not_in', (v) => `∉ [${show(v)}]`],
	['contains', (v) => `contains ${show(v)}`],
	['not_contains', (v) => `lacks ${show(v)}`],
	['matches', (v) => `~ /${v}/`],
	['not_matches', (v) => `!~ /${v}/`],
	['some', (v) => `some ${describeOp({ filter: v as Record<string, unknown> })}`],
	['every', (v) => `every ${describeOp({ filter: v as Record<string, unknown> })}`],
	['none', (v) => `no ${describeOp({ filter: v as Record<string, unknown> })}`]
];
const COUNT_SYMBOLS: Record<string, string> = { eq: '=', ne: '≠', gt: '>', gte: '≥', lt: '<', lte: '≤' };

function countText(v: unknown): string {
	if (typeof v === 'number') return `= ${v}`;
	if (!v || typeof v !== 'object') return String(v);
	return Object.entries(v as Record<string, unknown>)
		.map(([k, n]) => `${COUNT_SYMBOLS[k] ?? k} ${n}`)
		.join(' and ');
}

/** `{{ steps.a.outputs.hosts }}` → `a.hosts`, `{{ inputs.x }}` → `inputs.x`. */
export function shortRef(value: unknown): string {
	if (typeof value !== 'string') return show(value);
	const m = value.trim().match(/^\{\{\s*(?:steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_]+)|(inputs\.[A-Za-z0-9_]+))\s*\}\}$/);
	if (!m) return JSON.stringify(value);
	return m[3] ?? `${m[1]}.${m[2]}`;
}

type Node = Record<string, unknown>;

/** A `when:` condition as one short line, e.g. "unsigned.hosts count > 0". */
export function describeCondition(c: PlaybookCondition | null | undefined): string {
	if (!c) return '';
	if (Array.isArray(c)) return c.map(describeCondition).join(' and ');
	const o = c as Node;
	if (Array.isArray(o.all)) return (o.all as PlaybookCondition[]).map((x) => wrap(x, 'and')).join(' and ');
	if (Array.isArray(o.any)) return (o.any as PlaybookCondition[]).map((x) => wrap(x, 'or')).join(' or ');
	if ('not' in o) return `not (${describeCondition(o.not as PlaybookCondition)})`;
	if (typeof o.step === 'string' && 'status' in o) {
		const statuses = (Array.isArray(o.status) ? o.status : [o.status]) as string[];
		return `${o.step} ${statuses.map((st) => st.replace('_', ' ')).join(' or ')}`;
	}
	const parts = VALUE_TESTS.filter(([k]) => k in o).map(([k, f]) => f(o[k]));
	return `${shortRef(o.value)} ${parts.join(', ')}${o.ignore_case ? ' (any case)' : ''}`;
}

function wrap(c: PlaybookCondition, joiner: 'and' | 'or'): string {
	const text = describeCondition(c);
	const o = c as Node;
	const nested = !Array.isArray(c) && ((joiner === 'or' && 'all' in o) || (joiner === 'and' && 'any' in o));
	return nested ? `(${text})` : text;
}

/** Steps a condition looks at: through referenced outputs or status tests. */
export function conditionSteps(c: PlaybookCondition | null | undefined): Set<string> {
	const out = new Set<string>();
	const walk = (x: PlaybookCondition) => {
		if (Array.isArray(x)) return x.forEach(walk);
		const o = x as Node;
		if (Array.isArray(o.all)) (o.all as PlaybookCondition[]).forEach(walk);
		else if (Array.isArray(o.any)) (o.any as PlaybookCondition[]).forEach(walk);
		else if ('not' in o) walk(o.not as PlaybookCondition);
		else if (typeof o.step === 'string' && 'status' in o) out.add(o.step);
		else for (const ref of stepRefs(o.value)) out.add(ref.step);
	};
	if (c) walk(c);
	return out;
}

/** Short labels for how a step behaves: condition, join, failure policy, expectations. */
export function stepFlags(step: PlaybookStepSpec): { label: string; title: string; tone: 'gate' | 'warn' | 'plain' }[] {
	const flags: { label: string; title: string; tone: 'gate' | 'warn' | 'plain' }[] = [];
	if (step.when) flags.push({ label: 'if', title: `Runs only if ${describeCondition(step.when)}`, tone: 'gate' });
	if (step.for_each)
		flags.push({
			label: 'for each',
			title: `Runs once per item of ${shortRef(step.for_each.items)}${step.for_each.concurrency ? `, ${step.for_each.concurrency} at a time` : ''}`,
			tone: 'plain'
		});
	if (step.join && step.join !== 'all')
		flags.push({
			label: `join ${step.join}`,
			title: step.join === 'any' ? 'Runs once its inputs have ended and at least one worked' : 'Runs once its inputs have ended, whatever happened',
			tone: 'plain'
		});
	if (step.expect) flags.push({ label: 'expect', title: `Expects: ${describeExpect(step.expect).join('; ')}`, tone: 'plain' });
	if (step.on_failure === 'ask') flags.push({ label: 'ask on fail', title: 'Pauses and notifies people to decide if it fails or its output is unexpected', tone: 'warn' });
	if (step.on_failure === 'ai') flags.push({ label: 'AI on fail', title: 'Pauses if it fails or its output is unexpected, and the AI decides what to do (or leaves it to people)', tone: 'warn' });
	if (step.on_failure === 'continue') flags.push({ label: 'continue on fail', title: 'If it fails, the steps after it go ahead anyway', tone: 'plain' });
	if (step.skip_if_empty) flags.push({ label: 'skip if empty', title: 'Skipped when an upstream value is empty', tone: 'plain' });
	return flags;
}

export function describeExpect(e: NonNullable<PlaybookStepSpec['expect']>): string[] {
	const out: string[] = [];
	for (const [name, tests] of Object.entries(e.outputs ?? {})) out.push(describeCondition({ value: `{{ steps.x.outputs.${name} }}`, ...tests }).replace(/^x\./, ''));
	if (e.log) out.push(describeCondition({ value: 'log', ...e.log }).replace(/^"log"/, 'log'));
	return out;
}
