/**
 * App-wide confirmation prompt (M3 dialog), used before anything is deleted.
 *
 *   if (!(await confirmAction({ title: 'Delete comment?', message: '…' }))) return;
 *
 * Prompts queue if several are requested at once. Focus returns to whatever had it when
 * the prompt was requested.
 */
import { tick } from 'svelte';

export interface ConfirmOptions {
	title: string;
	message: string;
	/** Label of the confirming button, e.g. "Delete". */
	confirmLabel?: string;
	cancelLabel?: string;
	/** Destructive actions get a red confirm button. Defaults to true. */
	danger?: boolean;
	icon?: string;
}

interface Pending extends ConfirmOptions {
	resolve: (confirmed: boolean) => void;
	returnFocus: HTMLElement | null;
	/** Fallback when returnFocus is removed by the action (e.g. the deleted item's button). */
	returnDialog: HTMLDialogElement | null;
}

class ConfirmStore {
	queue = $state<Pending[]>([]);

	get current(): Pending | null {
		return this.queue[0] ?? null;
	}

	ask(options: ConfirmOptions): Promise<boolean> {
		const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
		const returnDialog = returnFocus?.closest('dialog') ?? null;
		return new Promise((resolve) => {
			this.queue = [...this.queue, { danger: true, ...options, resolve, returnFocus, returnDialog }];
		});
	}

	settle(confirmed: boolean): void {
		const pending = this.queue[0];
		if (!pending) return;
		this.queue = this.queue.slice(1);
		pending.resolve(confirmed);
		if (this.queue.length > 0) return;
		const { returnFocus, returnDialog } = pending;
		// Wait for the prompt to leave the top layer (focus calls before that are ignored) and
		// for the action to finish, which may remove the element that had focus.
		const keepFocusInDialog = () => {
			if (document.activeElement === document.body && returnDialog?.isConnected && returnDialog.open) {
				returnDialog.focus({ preventScroll: true });
			}
		};
		void tick().then(() => {
			setTimeout(() => {
				if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
				else keepFocusInDialog();
			}, 50);
			// The confirmed action usually finishes a little later (a network request) and may
			// remove the element we just refocused; catch that too.
			setTimeout(keepFocusInDialog, 600);
			setTimeout(keepFocusInDialog, 1500);
		});
	}
}

export const confirmStore = new ConfirmStore();

export function confirmAction(options: ConfirmOptions): Promise<boolean> {
	return confirmStore.ask(options);
}
