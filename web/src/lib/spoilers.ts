/**
 * Spoiler text, Discord style: `||hidden||` anywhere people write (titles, descriptions,
 * comments, posts, notes). It shows as a bar until someone clicks it (or presses Enter on it);
 * the one handler for the whole app is `revealSpoilers` (root layout). Spoilers hide things
 * from a glance, not from anyone determined: the text is still in the page, the API and
 * exports. Secrets belong in the vault.
 *
 * - Rendered markdown: the `marked` extension below (lib/markdown.ts uses it).
 * - Plain text (card titles…): the `Spoilers` component, from `splitSpoilers`.
 * - Where text can't be hidden (snackbars, desktop notifications, dropdowns, search results, tooltips):
 *   `maskSpoilers` censors it with asterisks. The pipes never show anywhere but where you type.
 * The server has the same rule (`spoilers.py`) for chat alerts, webhooks and exports: keep them alike.
 */
import type { TokenizerAndRendererExtension, Tokens } from 'marked';

/** `||text||`: at least one character, not starting or ending with a space, on one paragraph. */
export const SPOILER = /\|\|(?=[^\s|])((?:[^|\n]|\|(?!\|))*?[^\s|])\|\|/g;

export type Piece = { text: string; spoiler: boolean };

export function splitSpoilers(text: string | null | undefined): Piece[] {
	const source = text ?? '';
	const out: Piece[] = [];
	let at = 0;
	for (const m of source.matchAll(SPOILER)) {
		if (m.index! > at) out.push({ text: source.slice(at, m.index), spoiler: false });
		out.push({ text: m[1], spoiler: true });
		at = m.index! + m[0].length;
	}
	if (at < source.length) out.push({ text: source.slice(at), spoiler: false });
	return out;
}

export const hasSpoilers = (text: string | null | undefined) => !!text && text.includes('||') && splitSpoilers(text).some((p) => p.spoiler);

/** For text that can't hide part of itself: each spoiler is censored with asterisks (3 to 8, about
 *  half its length, so it doesn't give the length away). */
export function maskSpoilers(text: string | null | undefined): string {
	return (text ?? '').replace(SPOILER, (_all, inner: string) => '*'.repeat(Math.max(3, Math.min(8, Math.round(inner.length / 2)))));
}

/** A `marked` inline extension: `||text||` → `<span class="spoiler">…</span>` (inline markdown inside works). */
export const spoilerExtension: TokenizerAndRendererExtension = {
	name: 'spoiler',
	level: 'inline',
	start(src: string) {
		const i = src.indexOf('||');
		return i < 0 ? undefined : i;
	},
	tokenizer(src: string) {
		const m = /^\|\|(?=[^\s|])((?:[^|\n]|\|(?!\|))*?[^\s|])\|\|/.exec(src);
		if (!m) return undefined;
		const token = { type: 'spoiler', raw: m[0], text: m[1], tokens: [] as Tokens.Generic[] };
		this.lexer.inline(m[1], token.tokens);
		return token;
	},
	renderer(token) {
		return `<span class="spoiler" tabindex="0" role="button" aria-label="Spoiler: press to show">${this.parser.parseInline(token.tokens ?? [])}</span>`;
	}
};

/** One handler for every spoiler on the page (root layout): a click or Enter/Space on a hidden
 *  spoiler shows it, and does nothing else (it doesn't open the card or follow a link under it). */
export function revealSpoilers(root: Document | HTMLElement = document): () => void {
	const show = (e: Event) => {
		const el = (e.target as Element | null)?.closest?.('.spoiler:not(.revealed)');
		if (!el) return;
		if (e instanceof KeyboardEvent && e.key !== 'Enter' && e.key !== ' ') return;
		e.preventDefault();
		e.stopPropagation();
		el.classList.add('revealed');
		el.removeAttribute('role');
		el.setAttribute('aria-label', 'Spoiler, shown');
	};
	root.addEventListener('click', show, true);
	root.addEventListener('keydown', show as EventListener, true);
	return () => {
		root.removeEventListener('click', show, true);
		root.removeEventListener('keydown', show as EventListener, true);
	};
}
