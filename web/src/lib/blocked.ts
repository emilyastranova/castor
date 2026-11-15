/** Starting a card that waits on unfinished cards: the server refuses (409 with blocked_by);
 *  ask whether to start it anyway, and try again with force. */
import { ApiError } from '$lib/api';
import { confirmAction } from '$lib/confirm.svelte';

export interface Blocker {
	id: string;
	title: string;
}

/** The cards a refused start waits on, or null when it wasn't that. */
export function blockersOf(err: unknown): Blocker[] | null {
	if (!(err instanceof ApiError) || err.status !== 409) return null;
	const d = err.detail as { blocked_by?: Blocker[] } | null;
	return d && Array.isArray(d.blocked_by) ? d.blocked_by : null;
}

/** Run `attempt(false)`; if the card is blocked, ask, and run `attempt(true)` if the person agrees.
 *  Resolves to null when they decline; other errors are thrown as they were. */
export async function startAnyway<T>(title: string, attempt: (force: boolean) => Promise<T>): Promise<T | null> {
	try {
		return await attempt(false);
	} catch (err) {
		const blockers = blockersOf(err);
		if (!blockers) throw err;
		const names = blockers.map((b) => `“${b.title}”`);
		const ok = await confirmAction({
			title: `Start “${title}” anyway?`,
			message: `It waits on ${names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0]}, which ${names.length > 1 ? "aren't" : "isn't"} finished yet.`,
			confirmLabel: 'Start anyway',
			danger: false,
			icon: 'lock_open'
		});
		return ok ? attempt(true) : null;
	}
}
