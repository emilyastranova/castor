/**
 * Markdown formatting as pure text edits, for the editor's toolbar and shortcuts
 * (MarkdownEditor.svelte). Each takes the text and the selection and returns what to replace
 * and what to select afterwards (apply it with `applyEdit`, which keeps the browser's undo).
 */
import { backtickEdit, type Edit } from '$lib/m3/markdownKeys';

export type { Edit };

const lineStart = (text: string, at: number) => text.lastIndexOf('\n', at - 1) + 1;
const lineEnd = (text: string, at: number) => {
	const i = text.indexOf('\n', at);
	return i < 0 ? text.length : i;
};

/** **bold**, _italic_, ~~strike~~: wrap the selection, or unwrap it when it's already wrapped
 *  (inside or outside the selection); with nothing selected, insert a placeholder to type over. */
export function wrapInline(text: string, start: number, end: number, mark: string, placeholder: string): Edit {
	const sel = text.slice(start, end);
	const n = mark.length;
	if (text.slice(start - n, start) === mark && text.slice(end, end + n) === mark) {
		return { from: start - n, to: end + n, insert: sel, select: [start - n, end - n] };
	}
	if (sel.length >= 2 * n && sel.startsWith(mark) && sel.endsWith(mark)) {
		const inner = sel.slice(n, -n);
		return { from: start, to: end, insert: inner, select: [start, start + inner.length] };
	}
	const body = sel || placeholder;
	// Markers hug the words: spaces selected with them stay outside.
	const lead = body.length - body.trimStart().length;
	const trail = body.length - body.trimEnd().length;
	const core = body.trim() || placeholder;
	const insert = `${body.slice(0, lead)}${mark}${core}${mark}${trail ? body.slice(-trail) : ''}`;
	const at = start + lead + n;
	return { from: start, to: end, insert, select: [at, at + core.length] };
}

/** `code`: the backtick rule (one line inline, several lines a fenced block); a placeholder
 *  with nothing selected. */
export function inlineCode(text: string, start: number, end: number): Edit {
	return backtickEdit(text, start, end) ?? { from: start, to: end, insert: '`code`', select: [start + 1, start + 5] };
}

/** A fenced code block on lines of its own, around the selection (or empty, caret inside). */
export function codeBlock(text: string, start: number, end: number): Edit {
	const sel = text.slice(start, end).replace(/\n+$/, '');
	const before = start > 0 && text[start - 1] !== '\n' ? '\n' : '';
	const after = end < text.length && text[end] !== '\n' ? '\n' : '';
	const open = `${before}\`\`\`\n`;
	const insert = `${open}${sel}\n\`\`\`${after}`;
	const at = start + open.length;
	return { from: start, to: end, insert, select: [at, at + sel.length] };
}

const URL_LIKE = /^(https?:\/\/|mailto:|\/)\S+$/i;

/** [text](url): selected text becomes the link text with the URL to fill in; a selected URL
 *  becomes the target with the text to fill in. */
export function link(text: string, start: number, end: number): Edit {
	const sel = text.slice(start, end).trim();
	if (sel && URL_LIKE.test(sel)) {
		const insert = `[link text](${sel})`;
		return { from: start, to: end, insert, select: [start + 1, start + 10] };
	}
	const label = sel || 'link text';
	const insert = `[${label}](https://)`;
	const urlAt = start + label.length + 3;
	return sel
		? { from: start, to: end, insert, select: [urlAt, urlAt + 8] }
		: { from: start, to: end, insert, select: [start + 1, start + 1 + label.length] };
}

export type LineKind = 'quote' | 'bullet' | 'number' | 'task';
const MARKER = /^(\s*)(?:>\s?|[-*+]\s(?:\[[ xX]\]\s)?|\d+[.)]\s)/;
const HAS: Record<LineKind, RegExp> = {
	quote: /^\s*>\s?/,
	bullet: /^\s*[-*+]\s(?!\[[ xX]\]\s)/,
	number: /^\s*\d+[.)]\s/,
	task: /^\s*[-*+]\s\[[ xX]\]\s/
};

/** Quote or list the selected lines (each gets the marker; numbered lists count up), or take
 *  the marker off when every line already has it. Another list kind is swapped for this one. */
export function toggleLines(text: string, start: number, end: number, kind: LineKind): Edit {
	const from = lineStart(text, start);
	// A selection ending at the start of a line doesn't include that line.
	const to = lineEnd(text, end > start && text[end - 1] === '\n' ? end - 1 : end);
	const lines = text.slice(from, to).split('\n');
	const content = lines.filter((l) => l.trim());
	const removing = content.length > 0 && content.every((l) => HAS[kind].test(l));
	let n = 0;
	const out = lines.map((line) => {
		if (!line.trim() && lines.length > 1) return line;
		if (removing) return line.replace(HAS[kind], (m) => m.match(/^\s*/)![0]);
		const [, indent] = line.match(/^(\s*)/)!;
		const rest = line.replace(MARKER, '').trimStart();
		n += 1;
		const marker = kind === 'quote' ? '> ' : kind === 'bullet' ? '- ' : kind === 'task' ? '- [ ] ' : `${n}. `;
		return indent + marker + rest;
	});
	const insert = out.join('\n');
	// One empty line: put the caret after the new marker, ready to type.
	if (lines.length === 1 && !lines[0].trim() && !removing) return { from, to, insert, select: [from + insert.length, from + insert.length] };
	return { from, to, insert, select: start === end ? [from + insert.length, from + insert.length] : [from, from + insert.length] };
}

const LIST_ITEM = /^(\s*)(?:([-*+])\s(\[[ xX]\]\s)?|(\d+)([.)])\s)/;

/** Enter in a list item: the next line starts with the next marker (an unticked box, the next
 *  number). Enter on an empty item ends the list instead. Null when it isn't a list line. */
export function continueList(text: string, start: number, end: number): Edit | null {
	if (start !== end) return null;
	const from = lineStart(text, start);
	const line = text.slice(from, lineEnd(text, start));
	const m = line.match(LIST_ITEM);
	if (!m || start < from + m[0].length) return null;
	if (!line.slice(m[0].length).trim() && start === from + line.length) {
		return { from, to: from + line.length, insert: '', select: [from, from] }; // end the list
	}
	const [, indent, bullet, box, num, delim] = m;
	const marker = bullet ? `${bullet} ${box ? '[ ] ' : ''}` : `${Number(num) + 1}${delim} `;
	const insert = `\n${indent}${marker}`;
	return { from: start, to: end, insert, select: [start + insert.length, start + insert.length] };
}

/** Text inserted at the caret on a line of its own (an image, say), and where the caret goes. */
export function insertBlock(text: string, start: number, end: number, block: string): Edit {
	const before = start > 0 && text[start - 1] !== '\n' ? '\n' : '';
	const after = end < text.length && text[end] !== '\n' ? '\n' : '';
	const insert = before + block + after;
	return { from: start, to: end, insert, select: [start + insert.length, start + insert.length] };
}

/** An @ to start a mention (with a space before it when it would join a word). */
export function mentionStart(text: string, start: number, end: number): Edit {
	const lead = start > 0 && /\S/.test(text[start - 1]) ? ' ' : '';
	const insert = `${lead}@`;
	return { from: start, to: end, insert, select: [start + insert.length, start + insert.length] };
}
