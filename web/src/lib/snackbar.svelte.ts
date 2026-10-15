/** Global snackbar queue (M3 snackbar: one visible at a time, bottom of the screen). */
export interface SnackbarMessage {
	id: number;
	text: string;
	action?: { label: string; run: () => void };
	tone?: 'default' | 'error';
	timeout?: number;
}

class SnackbarStore {
	queue = $state<SnackbarMessage[]>([]);
	private next = 1;

	show(text: string, opts: Omit<SnackbarMessage, 'id' | 'text'> = {}): void {
		this.queue.push({ id: this.next++, text, timeout: 4000, ...opts });
	}

	error(text: string): void {
		this.show(text, { tone: 'error', timeout: 6000 });
	}

	dismiss(id: number): void {
		this.queue = this.queue.filter((m) => m.id !== id);
	}
}

export const snackbar = new SnackbarStore();
