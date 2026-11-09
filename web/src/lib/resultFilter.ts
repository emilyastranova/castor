/**
 * Filtering and sorting parsed results (OutputCard): words match anywhere in a row,
 * `field:value` contains, `field=value` / `field!=value` equal or not, and `field>n`,
 * `field>=n`, `field<n`, `field<=n` compare numbers. Fields are a column's key or its label
 * without spaces (`signing`, `netbiosname`), matched on the raw value, so `signing:false`
 * works whatever the cell shows. Values with spaces go in quotes: `os:"Windows Server"`.
 */

export interface ResultColumn {
	key: string;
	label: string;
	mono?: boolean;
	/** How the cell shows the value (the filter and sort use the raw value). */
	format?: (value: unknown, row: Record<string, unknown>) => string;
}

type Op = ':' | '=' | '!=' | '>' | '>=' | '<' | '<=';
interface Condition {
	field: string;
	op: Op;
	value: string;
}
export interface Filter {
	words: string[];
	conditions: Condition[];
	/** Field names that match no column, to say so instead of quietly showing nothing. */
	unknown: string[];
}

const norm = (s: string) => s.toLowerCase().replace(/[\s_-]+/g, '');

export function parseFilter(text: string, columns: ResultColumn[]): Filter {
	const words: string[] = [];
	const conditions: Condition[] = [];
	const unknown: string[] = [];
	for (const m of text.matchAll(/(\w[\w-]*)(!=|>=|<=|[:=><])("[^"]*"|\S*)|"([^"]*)"|(\S+)/g)) {
		if (m[1]) {
			const field = norm(m[1]);
			const value = (m[3] ?? '').replace(/^"|"$/g, '');
			const col = columns.find((c) => norm(c.key) === field || norm(c.label) === field);
			if (!col) unknown.push(m[1]);
			else if (value) conditions.push({ field: col.key, op: m[2] as Op, value });
		} else {
			const w = (m[4] ?? m[5] ?? '').trim();
			if (w) words.push(w.toLowerCase());
		}
	}
	return { words, conditions, unknown };
}

const text = (v: unknown): string => (v === null || v === undefined ? '' : Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? JSON.stringify(v) : String(v));

function test(raw: unknown, c: Condition): boolean {
	const want = c.value.toLowerCase();
	if (c.op === '>' || c.op === '>=' || c.op === '<' || c.op === '<=') {
		const n = Number(raw);
		const w = Number(c.value);
		if (raw === null || raw === undefined || raw === '' || Number.isNaN(n) || Number.isNaN(w)) return false;
		return c.op === '>' ? n > w : c.op === '>=' ? n >= w : c.op === '<' ? n < w : n <= w;
	}
	const values = Array.isArray(raw) ? raw.map((x) => text(x).toLowerCase()) : [text(raw).toLowerCase()];
	if (c.op === '=') return values.some((v) => v === want);
	if (c.op === '!=') return values.every((v) => v !== want);
	// ':' contains; for booleans and empty values, an exact answer (signing:false, name:)
	if (typeof raw === 'boolean') return String(raw) === want;
	return values.some((v) => v.includes(want));
}

export function matches(row: Record<string, unknown>, f: Filter, columns: ResultColumn[]): boolean {
	if (!f.conditions.every((c) => test(row[c.field], c))) return false;
	if (!f.words.length) return true;
	const hay = columns.map((c) => `${text(row[c.key])} ${c.format ? c.format(row[c.key], row) : ''}`).join(' ').toLowerCase();
	return f.words.every((w) => hay.includes(w));
}

export type Sort = { key: string; dir: 1 | -1 } | null;

/** Numbers as numbers, IP addresses part by part, text naturally (host2 before host10);
 *  empty values last whichever way. */
export function compareValues(a: unknown, b: unknown): number {
	const empty = (v: unknown) => v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length);
	if (empty(a) || empty(b)) return empty(a) === empty(b) ? 0 : empty(a) ? 1 : -1;
	if (typeof a === 'number' && typeof b === 'number') return a - b;
	if (typeof a === 'boolean' && typeof b === 'boolean') return Number(a) - Number(b);
	const sa = text(a);
	const sb = text(b);
	const ipa = sa.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)/);
	const ipb = sb.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)/);
	if (ipa && ipb) {
		for (let i = 1; i <= 4; i++) if (Number(ipa[i]) !== Number(ipb[i])) return Number(ipa[i]) - Number(ipb[i]);
	}
	return sa.localeCompare(sb, undefined, { numeric: true, sensitivity: 'base' });
}

export function sortRows<T extends Record<string, unknown>>(rows: T[], sort: Sort): T[] {
	if (!sort) return rows;
	return rows
		.map((row, i) => ({ row, i }))
		.sort((x, y) => {
			const a = x.row[sort.key];
			const b = y.row[sort.key];
			const bothSet = ![a, b].some((v) => v === null || v === undefined || v === '');
			const c = compareValues(a, b);
			return (bothSet ? c * sort.dir : c) || x.i - y.i; // empty values stay last; ties keep their order
		})
		.map((x) => x.row);
}
