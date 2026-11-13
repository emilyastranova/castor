/** Opening a snapshot of some text from anywhere (a code block's camera button, the command
 *  palette): the root layout shows SnapshotDialog while `request` is set. */
export interface SnapshotRequest {
	text: string;
	/** As a code fence names it ("py"); highlight.js resolves it. */
	language?: string | null;
	title?: string;
}

class Snapshots {
	request = $state<SnapshotRequest | null>(null);

	open(r: SnapshotRequest) {
		this.request = r;
	}

	close() {
		this.request = null;
	}
}

export const snapshots = new Snapshots();
