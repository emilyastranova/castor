import type { Task } from '$lib/api';

/** A playbook run's card: its state in a word, an icon and a tone (TaskCard and the list view). */
export function runState(task: Task): { label: string; icon: string; tone: string } | null {
	const r = task.run;
	if (!r) return null;
	if (r.attention) return { label: 'Needs you', icon: 'front_hand', tone: 'bg-tertiary-container text-on-tertiary-container' };
	if (r.status === 'running') return { label: `Running · ${r.done}/${r.total}`, icon: 'progress_activity', tone: 'bg-primary-container text-on-primary-container' };
	if (r.status === 'succeeded') return { label: 'Done', icon: 'check_circle', tone: 'bg-primary-container text-on-primary-container' };
	if (r.status === 'failed') return { label: `Failed · ${r.failed} step${r.failed === 1 ? '' : 's'}`, icon: 'error', tone: 'bg-error-container text-on-error-container' };
	return { label: 'Cancelled', icon: 'cancel', tone: 'bg-surface-container-highest text-on-surface-variant' };
}

/** Whether something is running for the card right now. */
export const isRunning = (task: Task) =>
	task.latest_job?.status === 'running' || task.latest_job?.status === 'dispatched' || task.run?.status === 'running';

/** The card's kind as an icon: a playbook run, a command, or a person's task. */
export const kindIcon = (task: Task) => (task.run ? 'account_tree' : task.kind === 'command' ? 'terminal' : 'person');
