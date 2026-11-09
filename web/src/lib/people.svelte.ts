import { isAstra } from '$lib/components/PersonName.svelte';
/**
 * Who can see each project (members and admins), loaded once per project and kept fresh
 * from realtime events. Used for assignees, @mention suggestions and mention chips.
 *
 *   people.list(projectId)        // [] until loaded, then reactive
 *   people.user(projectId, id)
 */
import { browser } from '$app/environment';
import type { Group, UserBrief, UUID } from '$lib/api';
import { api } from '$lib/api';
import { realtime } from '$lib/realtime.svelte';
import { initials } from '$lib/format';

/** Someone (or a group) an @mention can name: a group is a UserBrief-shaped entry with `group`. */
export type Mentionable = UserBrief & { group?: Group };

class PeopleStore {
	byProject = $state<Record<string, UserBrief[]>>({});
	groupList = $state<Group[] | null>(null);
	private groupsLoading = false;
	private loading = new Set<string>();

	constructor() {
		if (!browser) return;
		realtime.subscribe((e) => {
			// Membership or someone's name/picture changed: reload what's been loaded.
			if (e.name === 'project.members' || e.name === 'user.changed') {
				for (const id of Object.keys(this.byProject)) void this.fetch(id);
			}
			if (e.name === 'groups.changed' && this.groupList) void this.fetchGroups();
		});
	}

	list(projectId: UUID | null | undefined): UserBrief[] {
		if (!projectId) return [];
		if (!(projectId in this.byProject) && !this.loading.has(projectId)) void this.fetch(projectId);
		return this.byProject[projectId] ?? [];
	}

	/** Every group, as @mention entries (mentioning one reaches its people who can see the project). */
	groups(): Mentionable[] {
		if (this.groupList === null && !this.groupsLoading && browser) void this.fetchGroups();
		return (this.groupList ?? []).map((g) => ({ id: `group:${g.id}`, username: g.slug, display_name: g.name, role: 'viewer', avatar_url: null, group: g }));
	}

	/** People who can see the project, then groups: what @ can name there. */
	mentionables(projectId: UUID | null | undefined): Mentionable[] {
		return [...this.list(projectId), ...this.groups()];
	}

	private async fetchGroups() {
		this.groupsLoading = true;
		try {
			this.groupList = await api().groups.list();
		} catch {
			this.groupList = [];
		} finally {
			this.groupsLoading = false;
		}
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

/** The @username tooltip over a mention chip. It lives in the top layer (a popover), so a
 *  clipped comment box (line-clamped in the feed) or a modal dialog can't hide it. */
let tip: HTMLDivElement | null = null;
export function showMentionTip(chip: HTMLElement) {
	if (!chip.dataset.username) return;
	if (!tip) {
		tip = document.createElement('div');
		tip.className = 'mention-tip';
		tip.setAttribute('popover', 'manual');
		tip.setAttribute('role', 'tooltip');
		document.body.append(tip);
	}
	tip.textContent = chip.dataset.username;
	const r = chip.getBoundingClientRect();
	tip.style.left = `${r.left + r.width / 2}px`;
	tip.style.top = `${r.top - 6}px`;
	if (!tip.matches(':popover-open')) tip.showPopover();
	tip.classList.add('shown');
	// It's placed once; anything scrolling would leave it behind, so it goes.
	window.addEventListener('scroll', hideMentionTip, { capture: true, once: true });
}
export function hideMentionTip() {
	if (!tip) return;
	tip.classList.remove('shown');
	if (tip.matches(':popover-open')) tip.hidePopover();
}

/** A mention chip: the person's picture (or initials) and display name; `@username` shows on
 *  hover (showMentionTip) so the text never shifts. */
function mentionChip(user: Mentionable, isMe: boolean): HTMLElement {
	if (user.group) return groupChip(user.group, isMe);
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
	name.className = isAstra(user.display_name) ? 'mention-name astra-name' : 'mention-name';
	name.textContent = user.display_name;
	chip.append(face, name);
	return chip;
}

/** A group's chip: a groups icon and its name; `@handle · N people` on hover. */
function groupChip(group: Group, isMe: boolean): HTMLElement {
	const chip = document.createElement('span');
	chip.className = `mention mention-group${isMe ? ' mention-me' : ''}`;
	chip.dataset.username = `@${group.slug} · ${group.member_count} ${group.member_count === 1 ? 'person' : 'people'}`;
	chip.dataset.group = group.slug;
	chip.setAttribute('aria-label', `@${group.slug} (group ${group.name})`);
	const face = document.createElement('span');
	face.className = 'mention-face material-symbols-rounded';
	face.setAttribute('aria-hidden', 'true');
	face.textContent = 'groups';
	const name = document.createElement('span');
	name.className = 'mention-name';
	name.textContent = group.name;
	chip.append(face, name);
	return chip;
}

/**
 * Wrap @mentions of people who can see the project in chips, inside rendered markdown
 * (not in code, preformatted text or links). Yours get a stronger chip.
 */
export function chipMentions(root: HTMLElement, who: Mentionable[], meId: string | undefined) {
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
			parts.push(mentionChip(user, user.group ? !!meId && user.group.member_ids.includes(meId) : user.id === meId));
			last = at + 1 + name.length;
		}
		if (!parts.length) continue;
		parts.push(text.slice(last));
		node.replaceWith(...parts.filter((p) => p !== ''));
	}
}
