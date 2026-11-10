/**
 * Markdown shortcuts for text boxes (TextField `markdown`): typing ` with text selected
 * makes the selection code instead of replacing it.
 *
 *   one line          → `code`       (``code`` with spaces when it has backticks itself)
 *   already `code`    → back to plain text (the same key toggles)
 *   several lines     → a ``` fenced block, on lines of its own
 */

export interface Edit {
	/** The range of the text to replace… */
	from: number;
	to: number;
	/** …with this. */
	insert: string;
	/** What to select afterwards (the code itself). */
	select: [number, number];
}

/** What typing a backtick over text[start..end] should do, or null to type it normally. */
export function backtickEdit(text: string, start: number, end: number): Edit | null {
	if (start === end) return null;
	const sel = text.slice(start, end);

	// Already inline code: unwrap.
	if (!sel.includes('\n') && text[start - 1] === '`' && text[end] === '`' && text[start - 2] !== '`' && text[end + 1] !== '`') {
		return { from: start - 1, to: end + 1, insert: sel, select: [start - 1, end - 1] };
	}

	if (sel.includes('\n')) {
		const body = sel.replace(/\n+$/, '');
		const before = start > 0 && text[start - 1] !== '\n' ? '\n' : '';
		const after = end < text.length && text[end] !== '\n' ? '\n' : '';
		const trailing = sel.slice(body.length); // newlines the selection ended with stay after the fence
		const open = `${before}\`\`\`\n`;
		const insert = `${open}${body}\n\`\`\`${trailing || after}`;
		const at = start + open.length;
		return { from: start, to: end, insert, select: [at, at + body.length] };
	}

	// A selection with backticks of its own needs a longer fence (and spaces when it starts or ends with one).
	const fence = sel.includes('`') ? '``' : '`';
	const pad = fence.length > 1 ? ' ' : '';
	const open = fence + pad;
	return { from: start, to: end, insert: `${open}${sel}${pad}${fence}`, select: [start + open.length, start + open.length + sel.length] };
}

/** Keydown handler: applies backtickEdit to a textarea. True when it handled the key. */
export function markdownKeydown(e: KeyboardEvent & { currentTarget: HTMLTextAreaElement | HTMLInputElement }): boolean {
	if (e.defaultPrevented || e.key !== '`' || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return false;
	const el = e.currentTarget;
	const { selectionStart: start, selectionEnd: end } = el;
	if (start === null || end === null || el.readOnly || el.disabled) return false;
	const edit = backtickEdit(el.value, start, end);
	if (!edit) return false;
	e.preventDefault();
	applyEdit(el, edit);
	return true;
}

/** Make an edit in a text box as if typed: it goes into the browser's undo history and fires
 *  `input` (so bind:value follows); setRangeText is the fallback where insertText isn't supported. */
export function applyEdit(el: HTMLTextAreaElement | HTMLInputElement, edit: Edit) {
	el.focus();
	el.setSelectionRange(edit.from, edit.to);
	const ok = edit.insert === '' && edit.from === edit.to ? true : document.execCommand?.(edit.insert ? 'insertText' : 'delete', false, edit.insert);
	if (!ok) {
		el.setRangeText(edit.insert, edit.from, edit.to, 'end');
		el.dispatchEvent(new Event('input', { bubbles: true }));
	}
	el.setSelectionRange(...edit.select);
}
