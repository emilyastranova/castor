/**
 * Who can see each project (members and admins), loaded once per project and kept fresh
 * from realtime events. Used for assignees, @mention suggestions and mention chips.
 *
 *   people.list(projectId)        // [] until loaded, then reactive
 *   people.user(projectId, id)
 */
import { browser } from '$app/environment';
import type { UserBrief, UUID } from '$lib/api';
import { api } from '$lib/api';
import { realtime } from '$lib/realtime.svelte';
import { initials } from '$lib/format';

class PeopleStore {
	byProject = $state<Record<string, UserBrief[]>>({});
	private loading = new Set<string>();

	constructor() {
		if (!browser) return;
		realtime.subscribe((e) => {
			// Membership or someone's name/picture changed: reload what's been loaded.
			if (e.name === 'project.members' || e.name === 'user.changed') {
				for (const id of Object.keys(this.byProject)) void this.fetch(id);
			}
		});
	}

	list(projectId: UUID | null | undefined): UserBrief[] {
		if (!projectId) return [];
		if (!(projectId in this.byProject) && !this.loading.has(projectId)) void this.fetch(projectId);
		return this.byProject[projectId] ?? [];
	}

	user(projectId: UUID | null | undefined, id: string): UserBrief | undefined {
		return this.list(projectId).find((u) => u.id === id);
	}

	private async fetch(projectId: UUID) {
		this.loading.add(projectId);
		try {
			this.byProject[projectId] = await api().projects.people(projectId);
		} catch {
			/* not visible (or offline): leave it empty */
		} finally {
			this.loading.delete(projectId);
		}
	}
}

export const people = new PeopleStore();

// ---- @mentions ----

/** `@name` the way the server reads it (people.MENTION). */
export const MENTION = /(?<![\w@./])@([A-Za-z0-9][A-Za-z0-9._-]{0,63})/g;

/** A mention chip: the person's picture (or initials) and display name; `@username` shows on
 *  hover (CSS, from data-username) so the text never shifts. */
function mentionChip(user: UserBrief, isMe: boolean): HTMLElement {
	const chip = document.createElement('span');
	chip.className = `mention${isMe ? ' mention-me' : ''}`;
	chip.dataset.username = `@${user.username}`;
	chip.setAttribute('aria-label', `@${user.username} (${user.display_name})`);
	const face = document.createElement(user.avatar_url ? 'img' : 'span');
	face.className = 'mention-face';
	face.setAttribute('aria-hidden', 'true');
	if (face instanceof HTMLImageElement) {
		face.src = user.avatar_url!;
		face.alt = '';
		face.loading = 'lazy';
		// A broken picture falls back to initials.
		face.onerror = () => face.replaceWith(Object.assign(document.createElement('span'), { className: 'mention-face', textContent: initials(user.display_name) }));
	} else {
		face.textContent = initials(user.display_name);
	}
	const name = document.createElement('span');
	name.className = 'mention-name';
	name.textContent = user.display_name;
	chip.append(face, name);
	return chip;
}

/**
 * Wrap @mentions of people who can see the project in chips, inside rendered markdown
 * (not in code, preformatted text or links). Yours get a stronger chip.
 */
export function chipMentions(root: HTMLElement, who: UserBrief[], meId: string | undefined) {
	if (!who.length) return;
	const byName = new Map(who.map((u) => [u.username.toLowerCase(), u]));
	const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
		acceptNode: (n) =>
			n.parentElement?.closest('code, pre, a, .mention') || !n.textContent?.includes('@') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
	});
	const nodes: Text[] = [];
	while (walker.nextNode()) nodes.push(walker.currentNode as Text);
	for (const node of nodes) {
		const text = node.textContent ?? '';
		const parts: (string | HTMLElement)[] = [];
		let last = 0;
		for (const match of text.matchAll(MENTION)) {
			const name = match[1].replace(/[._-]+$/, '');
			const user = byName.get(name.toLowerCase());
			if (!user) continue;
			const at = match.index!;
			parts.push(text.slice(last, at));
			parts.push(mentionChip(user, user.id === meId));
			last = at + 1 + name.length;
		}
		if (!parts.length) continue;
		parts.push(text.slice(last));
		node.replaceWith(...parts.filter((p) => p !== ''));
	}
}
