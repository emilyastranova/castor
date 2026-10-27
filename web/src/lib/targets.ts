/** Parsing pasted or uploaded target lists, and recognising what each entry is. */

export type Separator = 'auto' | 'newline' | 'comma' | 'semicolon' | 'whitespace' | 'custom';

export const SEPARATORS: { value: Separator; label: string }[] = [
	{ value: 'auto', label: 'Auto (lines, commas, semicolons, spaces)' },
	{ value: 'newline', label: 'One per line' },
	{ value: 'comma', label: 'Comma' },
	{ value: 'semicolon', label: 'Semicolon' },
	{ value: 'whitespace', label: 'Spaces or tabs' },
	{ value: 'custom', label: 'Custom…' }
];

export interface ParseOptions {
	separator: Separator;
	custom?: string;
	/** Drop lines starting with # and text after " #". */
	stripComments: boolean;
	dedupe: boolean;
}

export interface ParseResult {
	entries: string[];
	duplicates: number;
	comments: number;
}

function escapeRegex(text: string) {
	return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function parseEntries(text: string, options: ParseOptions): ParseResult {
	let comments = 0;
	let lines = text.replace(/\r\n?/g, '\n').split('\n');
	if (options.stripComments) {
		lines = lines.map((line) => {
			const trimmed = line.trim();
			if (trimmed.startsWith('#') || trimmed.startsWith('//')) {
				if (trimmed) comments++;
				return '';
			}
			const at = line.search(/\s#/);
			if (at >= 0) {
				comments++;
				return line.slice(0, at);
			}
			return line;
		});
	}
	const joined = lines.join('\n');
	const splitter = {
		auto: /[\n,;\s]+/,
		newline: /\n+/,
		comma: /,/,
		semicolon: /;/,
		whitespace: /[ \t]+|\n/,
		custom: options.custom ? new RegExp(`${escapeRegex(options.custom)}|\\n`) : /\n/
	}[options.separator];
	const raw = joined
		.split(splitter)
		.map((e) => e.trim())
		.filter(Boolean);
	if (!options.dedupe) return { entries: raw, duplicates: 0, comments };
	const entries = [...new Set(raw)];
	return { entries, duplicates: raw.length - entries.length, comments };
}

export type EntryType = 'ip' | 'cidr' | 'range' | 'url' | 'host' | 'other';

export const ENTRY_TYPES: Record<EntryType, { label: string; icon: string; tone: string }> = {
	ip: { label: 'IP', icon: 'lan', tone: 'bg-primary-container text-on-primary-container' },
	cidr: { label: 'CIDR', icon: 'hub', tone: 'bg-primary-container text-on-primary-container' },
	range: { label: 'IP range', icon: 'linear_scale', tone: 'bg-primary-container text-on-primary-container' },
	url: { label: 'URL', icon: 'link', tone: 'bg-tertiary-container text-on-tertiary-container' },
	host: { label: 'Domain / host', icon: 'dns', tone: 'bg-secondary-container text-on-secondary-container' },
	other: { label: 'Unrecognised', icon: 'help', tone: 'bg-error-container text-on-error-container' }
};

const OCTET = '(25[0-5]|2[0-4]\\d|1?\\d?\\d)';
const IPV4 = new RegExp(`^${OCTET}(\\.${OCTET}){3}$`);
const IPV4_CIDR = new RegExp(`^${OCTET}(\\.${OCTET}){3}/(3[0-2]|[12]?\\d)$`);
// 10.0.0.1-50, 10.0.0-3.1, 10.0.0.1-10.0.0.9 (nmap-style and full ranges)
const IPV4_RANGE = /^(\d{1,3}(-\d{1,3})?\.){3}\d{1,3}(-\d{1,3})?$|^(\d{1,3}\.){3}\d{1,3}-(\d{1,3}\.){3}\d{1,3}$/;
const IPV6 = /^[0-9a-fA-F:]+:[0-9a-fA-F:.]*$/;
const IPV6_CIDR = /^[0-9a-fA-F:]+:[0-9a-fA-F:.]*\/\d{1,3}$/;
const HOST = /^(?=.{1,253}$)(\*\.)?([A-Za-z0-9_]([A-Za-z0-9_-]{0,62})?)(\.[A-Za-z0-9_-]{1,63})*\.?(:\d{1,5})?$/;

export function entryType(entry: string): EntryType {
	if (IPV4.test(entry) || IPV6.test(entry)) return 'ip';
	if (IPV4_CIDR.test(entry) || IPV6_CIDR.test(entry)) return 'cidr';
	if (IPV4_RANGE.test(entry)) return 'range';
	if (/^[a-z][a-z0-9+.-]*:\/\/\S+$/i.test(entry)) return 'url';
	if (HOST.test(entry) && /[A-Za-z]/.test(entry)) return 'host';
	return 'other';
}

export function summarise(entries: string[]): Partial<Record<EntryType, number>> {
	const counts: Partial<Record<EntryType, number>> = {};
	for (const e of entries) {
		const t = entryType(e);
		counts[t] = (counts[t] ?? 0) + 1;
	}
	return counts;
}

/** Biggest file the import dialog reads (target lists are text; this is generous). */
export const MAX_IMPORT_BYTES = 2 * 1024 * 1024;
