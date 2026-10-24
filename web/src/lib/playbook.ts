/** Display helpers shared by the playbook visualizer and the playbooks page. */
import type { PlaybookStepSpec, PlaybookStepStatus, TransformSpec } from '$lib/api';

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
	blocked: { label: 'Blocked', icon: 'block', text: 'text-error', strip: 'bg-error/40' }
};

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
