/**
 * Emoji suggestions for a markdown box: typing `:` and two or more letters (":par", ":rock")
 * offers the instance's custom emoji and the usual ones by name; Enter/Tab (or a click) inserts the
 * emoji itself, or `:name: ` for a custom one (which draws as its picture). Like MentionSuggest.
 */
import { tick } from 'svelte';
import { ALL, searchEmoji } from '$lib/emoji';
import { customEmojis } from '$lib/emojis.svelte';
import type { DraftTarget } from '$lib/pastedImages.svelte';

export interface EmojiChoice {
	/** What goes in: the emoji, or ":name:". */
	emoji: string;
	/** Its name, shown next to it. */
	name: string;
}

export class EmojiSuggest {
	open = $state(false);
	query = $state('');
	index = $state(0);
	private start = 0;
	private textarea: HTMLTextAreaElement | null = null;

	constructor(private target: DraftTarget) {}

	get matches(): EmojiChoice[] {
		const q = this.query.toLowerCase();
		const custom = customEmojis.list
			.filter((c) => c.name.includes(q))
			.sort((a, b) => Number(!a.name.startsWith(q)) - Number(!b.name.startsWith(q)) || a.name.localeCompare(b.name))
			.map((c) => ({ emoji: `:${c.name}:`, name: c.name }));
		const unicode = searchEmoji(q, 12).map((e) => ({ emoji: e, name: (ALL.find(([x]) => x === e)?.[1] ?? '').split(' ')[0] }));
		return [...custom, ...unicode].slice(0, 8);
	}

	oninput = (e: Event) => {
		const ta = e.currentTarget as HTMLTextAreaElement;
		const upto = ta.value.slice(0, ta.selectionStart ?? ta.value.length);
		// After a space, a bracket or the start, not in the middle of a word or a time (12:30).
		const m = /(?:^|[\s(])(:([a-z0-9_+-]{2,32}))$/i.exec(upto);
		if (!m) {
			this.open = false;
			return;
		}
		customEmojis.load();
		this.textarea = ta;
		this.start = upto.length - m[1].length;
		this.query = m[2];
		this.index = 0;
		this.open = true;
	};

	/** Handles the key if the list is showing; returns true when it did. */
	onkeydown = (e: KeyboardEvent): boolean => {
		if (!this.open) return false;
		const list = this.matches;
		if (e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation();
			this.open = false;
			return true;
		}
		if (!list.length) return false;
		if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			e.preventDefault();
			this.index = (this.index + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length;
			return true;
		}
		if ((e.key === 'Enter' && !e.ctrlKey && !e.metaKey) || e.key === 'Tab') {
			e.preventDefault();
			e.stopPropagation();
			this.pick(list[Math.min(this.index, list.length - 1)]);
			return true;
		}
		return false;
	};

	close = () => {
		this.open = false;
	};

	pick(choice: EmojiChoice) {
		const ta = this.textarea;
		const text = this.target.get();
		const caret = ta?.selectionStart ?? text.length;
		const insert = `${choice.emoji} `;
		this.target.set(text.slice(0, this.start) + insert + text.slice(caret));
		this.open = false;
		const at = this.start + insert.length;
		void tick().then(() => {
			ta?.focus();
			ta?.setSelectionRange(at, at);
		});
	}
}
