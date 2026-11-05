/** What the signed-in user may do, for showing or hiding controls. The API enforces the
 *  same rules; this only keeps the UI from offering what would be refused. */
import { page } from '$app/state';
import type { User, UserRole } from '$lib/api';

const RANK: Record<UserRole, number> = { viewer: 0, operator: 1, admin: 2 };

export function me(): User | null {
	return (page.data.user as User | null | undefined) ?? null;
}

/** Operators and admins can change things; viewers only look. */
export function canEdit(): boolean {
	const user = me();
	return !!user && RANK[user.role] >= RANK.operator;
}

export function isAdmin(): boolean {
	return me()?.role === 'admin';
}

export const ROLE_LABELS: Record<UserRole, { label: string; detail: string; icon: string }> = {
	viewer: { label: 'Viewer', detail: 'Sees their projects; changes nothing', icon: 'visibility' },
	operator: { label: 'Operator', detail: 'Works in their projects and edits the libraries', icon: 'build' },
	admin: { label: 'Admin', detail: 'Everything, including users, agents and every project', icon: 'shield_person' }
};
