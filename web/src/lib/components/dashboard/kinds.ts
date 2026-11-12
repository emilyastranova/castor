import type { FeedKind } from '$lib/api';

/** The kinds of timeline entry (the server's dashboard.kind_of), as Home's filter chips and the
 *  timeline export show them. Admin is only offered to admins. */
export const ALL_KINDS: { kind: FeedKind; label: string; icon: string }[] = [
	{ kind: 'comment', label: 'Comments', icon: 'chat_bubble' },
	{ kind: 'job', label: 'Jobs', icon: 'terminal' },
	{ kind: 'card', label: 'Cards', icon: 'note_stack_add' },
	{ kind: 'move', label: 'Moves', icon: 'move_item' },
	{ kind: 'board', label: 'Board', icon: 'view_kanban' },
	{ kind: 'playbook', label: 'Playbooks', icon: 'account_tree' },
	{ kind: 'library', label: 'Library', icon: 'construction' },
	{ kind: 'file', label: 'Files', icon: 'attach_file' },
	{ kind: 'note', label: 'Notes', icon: 'push_pin' },
	{ kind: 'asset', label: 'Assets', icon: 'radar' },
	{ kind: 'plugin', label: 'Plugins', icon: 'extension' },
	{ kind: 'agent', label: 'Agents', icon: 'dns' },
	{ kind: 'admin', label: 'Admin', icon: 'shield_person' }
];

/** What the timeline shows by default: the work, not note edits, agent connections or admin. */
export const WORK: FeedKind[] = ['comment', 'job', 'card', 'playbook', 'library', 'file', 'plugin'];
