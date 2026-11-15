/** How a card's due date reads: a short label, a tone and an icon (pure). */

export type DueTone = 'overdue' | 'soon' | 'later' | 'done';

const DAY = 86_400_000;

function startOfDay(d: Date): number {
	return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** A due date picked without a time means the end of that day (23:59 local). */
export const hasOwnTime = (d: Date) => !(d.getHours() === 23 && d.getMinutes() === 59);

/** "Today 17:00", "Tomorrow", "Thu", "18 Nov", "Overdue 2d" — and whether it needs attention.
 *  A date with no time of its own (end of day) shows without one. */
export function dueInfo(due: string, finished = false, now = new Date()): { label: string; tone: DueTone; icon: string; title: string } {
	const d = new Date(due);
	const days = Math.round((startOfDay(d) - startOfDay(now)) / DAY);
	const hasTime = hasOwnTime(d);
	const time = hasTime ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '';
	const title = `Due ${d.toLocaleString(undefined, { weekday: 'long', day: 'numeric', month: 'long', ...(hasTime ? { hour: '2-digit', minute: '2-digit' } : {}) })}`;
	if (finished) return { label: shortDay(d, days, time), tone: 'done', icon: 'event_available', title };
	const late = now.getTime() - d.getTime();
	if (late > 0) {
		const lateDays = Math.floor(late / DAY);
		const label = lateDays >= 1 ? `Overdue ${lateDays}d` : `Overdue ${Math.max(1, Math.floor(late / 3_600_000))}h`;
		return { label, tone: 'overdue', icon: 'event_busy', title };
	}
	return { label: shortDay(d, days, time), tone: d.getTime() - now.getTime() <= DAY ? 'soon' : 'later', icon: 'event', title };
}

function shortDay(d: Date, days: number, time: string): string {
	const at = time ? ` ${time}` : '';
	if (days === 0) return `Today${at}`;
	if (days === 1) return `Tomorrow${at}`;
	if (days > 1 && days < 7) return `${d.toLocaleDateString(undefined, { weekday: 'short' })}${at}`;
	return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
}

export const DUE_TONE: Record<DueTone, string> = {
	overdue: 'bg-error-container text-on-error-container',
	soon: 'bg-tertiary-container text-on-tertiary-container',
	later: 'border border-outline-variant text-on-surface-variant',
	done: 'border border-outline-variant text-on-surface-variant opacity-70'
};

const pad = (n: number) => String(n).padStart(2, '0');

/** A due date as the values of a date input and an (optional) time input, in local time. */
export function toInputs(iso: string | null | undefined): { date: string; time: string } {
	if (!iso) return { date: '', time: '' };
	const d = new Date(iso);
	return {
		date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
		time: hasOwnTime(d) ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : ''
	};
}
/** And back: no date is no due date; no time is the end of that day. */
export function fromInputs(date: string, time: string): string | null {
	if (!date) return null;
	const [y, mo, da] = date.split('-').map(Number);
	const [h, mi] = time ? time.split(':').map(Number) : [23, 59];
	return new Date(y, mo - 1, da, h, mi).toISOString();
}
