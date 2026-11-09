/**
 * @mention suggestions for a markdown text box: typing `@` followed by letters offers the
 * people who can see the project; Enter/Tab (or a click) inserts `@username `.
 *
 *   const suggest = new MentionSuggest(() => people.list(projectId), { get: () => body, set: (v) => (body = v) });
 *   <div class="relative">
 *     <TextField oninput={suggest.oninput} onkeydown={(e) => suggest.onkeydown(e) || myKeys(e)} />
 *     <MentionMenu {suggest} />
 *   </div>
 */
import { tick } from 'svelte';
import type { Mentionable } from '$lib/people.svelte';
import type { DraftTarget } from '$lib/pastedImages.svelte';

export class MentionSuggest {
	open = $state(false);
	query = $state('');
	index = $state(0);
	private start = 0;
	private textarea: HTMLTextAreaElement | null = null;

	constructor(
		private people: () => Mentionable[],
		private target: DraftTarget
	) {}

	get matches(): Mentionable[] {
		const q = this.query.toLowerCase();
		const score = (u: Mentionable) => {
			const name = u.username.toLowerCase();
			const words = u.display_name.toLowerCase().split(/\s+/);
			if (name.startsWith(q)) return 0;
			if (words.some((w) => w.startsWith(q))) return 1;
			if (name.includes(q) || u.display_name.toLowerCase().includes(q)) return 2;
			return -1;
		};
		return this.people()
			.map((u) => ({ u, s: score(u) }))
			.filter((x) => x.s >= 0)
			.sort((a, b) => a.s - b.s || a.u.display_name.localeCompare(b.u.display_name))
			.slice(0, 6)
			.map((x) => x.u);
	}

	oninput = (e: Event) => {
		const ta = e.currentTarget as HTMLTextAreaElement;
		const upto = ta.value.slice(0, ta.selectionStart ?? ta.value.length);
		const m = /(?:^|[^\w@./])@([A-Za-z0-9._-]{0,64})$/.exec(upto);
		if (!m) {
			this.open = false;
			return;
		}
		this.textarea = ta;
		this.start = upto.length - m[1].length - 1;
		this.query = m[1];
		this.index = 0;
		this.open = true;
	};

	/** Handles the key if the list is showing; returns true when it did. */
	onkeydown = (e: KeyboardEvent): boolean => {
		if (!this.open) return false;
		const list = this.matches;
		if (e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation(); // close the list, not the dialog around it
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

	pick(user: Mentionable) {
		const ta = this.textarea;
		const text = this.target.get();
		const caret = ta?.selectionStart ?? text.length;
		const insert = `@${user.username} `;
		this.target.set(text.slice(0, this.start) + insert + text.slice(caret));
		this.open = false;
		const at = this.start + insert.length;
		void tick().then(() => {
			ta?.focus();
			ta?.setSelectionRange(at, at);
		});
	}
}
