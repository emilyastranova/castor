/** Comparing runs (pure): how a compared record reads in a line, counts in words, and the
 *  changes as Markdown for a report. */
import type { ChangeCounts, OutputChange } from '$lib/api';

type Rec = Record<string, unknown>;

const isRec = (v: unknown): v is Rec => !!v && typeof v === 'object' && !Array.isArray(v);

/** A value in a few characters: lists joined, nothing as a dash. */
export function fmt(v: unknown): string {
	if (v === null || v === undefined || v === '') return '—';
	if (Array.isArray(v)) return v.length ? v.map(fmt).join(', ') : '—';
	if (typeof v === 'object') return JSON.stringify(v);
	return String(v);
}

const short = (v: unknown) => (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') && String(v).length <= 60;

/** A record as a title (what it is) and detail (what else is worth a glance). */
export function describe(item: unknown, key: string[] | null, type: string | null): { title: string; detail: string } {
	if (!isRec(item)) return { title: fmt(item), detail: '' };
	const r = item;
	const join = (...xs: unknown[]) => xs.filter((x) => x !== undefined && x !== null && x !== '' && !(Array.isArray(x) && !x.length)).map(fmt).join(' · ');
	if (type === 'list<service>' || ('host' in r && 'port' in r && ('proto' in r || 'state' in r)))
		return { title: `${r.host}:${r.port}${r.proto ? `/${r.proto}` : ''}`, detail: join(r.state !== 'open' ? r.state : '', r.name, r.product, r.version) };
	if (type === 'list<host>' || ('ip' in r && Object.keys(r).length <= 4)) return { title: fmt(r.ip), detail: join(r.hostnames, r.status !== 'up' ? r.status : '') };
	if (type === 'list<url>' || 'url' in r) return { title: fmt(r.url), detail: join(r.status, r.title, r.webserver) };
	if (type === 'list<smb_host>') return { title: `${r.host}:${r.port}`, detail: join(r.name, r.domain, r.os, r.signing === false ? 'signing off' : '', r.smbv1 ? 'SMBv1' : '') };
	const keys = key?.length ? key : Object.keys(r).filter((k) => short(r[k])).slice(0, 1);
	const rest = Object.keys(r).filter((k) => !keys.includes(k) && short(r[k]) && r[k] !== '').slice(0, 3);
	return { title: keys.map((k) => fmt(r[k])).join(' · '), detail: rest.map((k) => `${k} ${fmt(r[k])}`).join(' · ') };
}

/** "2 new · 1 gone · 3 changed", or "No changes". */
export function countWords(c: ChangeCounts): string {
	const parts = [c.added && `${c.added} new`, c.removed && `${c.removed} gone`, c.changed && `${c.changed} changed`].filter(Boolean);
	return parts.length ? parts.join(' · ') : 'No changes';
}

export const anyChange = (c: ChangeCounts) => c.added + c.removed + c.changed > 0;

/** The changes of some outputs as Markdown, for pasting into a report. */
export function toMarkdown(outputs: OutputChange[], heading = ''): string {
	const lines: string[] = heading ? [`## ${heading}`, ''] : [];
	for (const o of outputs) {
		if (o.status === 'error' || o.status === 'same') continue;
		const cut = (n: number, shown: number) => (n > shown ? [`- … and ${n - shown} more`] : []);
		lines.push(`### ${o.name} (${countWords(o.counts)})`, '');
		if (o.after !== null && !Array.isArray(o.after) && !o.changed.length && o.status === 'changed')
			lines.push(`- ${fmt(o.before)} → ${fmt(o.after)}`);
		for (const r of o.added) {
			const d = describe(r, o.key, o.type);
			lines.push(`- **New:** ${d.title}${d.detail ? ` (${d.detail})` : ''}`);
		}
		lines.push(...cut(o.counts.added, o.added.length));
		for (const r of o.removed) {
			const d = describe(r, o.key, o.type);
			lines.push(`- **Gone:** ${d.title}${d.detail ? ` (${d.detail})` : ''}`);
		}
		lines.push(...cut(o.counts.removed, o.removed.length));
		for (const c of o.changed) {
			const d = describe(c.item, o.key, o.type);
			lines.push(`- **Changed:** ${d.title}: ${c.fields.map((f) => `${f.field} ${fmt(f.before)} → ${fmt(f.after)}`).join('; ')}`);
		}
		lines.push(...cut(o.counts.changed, o.changed.length), '');
	}
	return lines.join('\n').trim() + '\n';
}
