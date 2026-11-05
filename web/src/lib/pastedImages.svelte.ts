/**
 * Images pasted into a markdown text box (comments, pinned notes): each one is named first
 * (NameImageDialog), then uploaded and embedded as markdown at the caret. A placeholder
 * holds its place during the upload.
 *
 *   const images = new PastedImages(async (file) => api().attachments.url((await upload(file)).id, true));
 *   <TextField onpaste={images.pasteInto({ get: () => body, set: (v) => (body = v) })} />
 *   {#if images.pending[0]}{#key images.pending[0]}
 *     <NameImageDialog file={images.pending[0].file} remaining={images.pending.length - 1}
 *       onconfirm={(n) => images.confirm(n)} oncancel={() => images.cancel()} />
 *   {/key}{/if}
 */
import { tick } from 'svelte';
import { ApiError } from '$lib/api';
import { snackbar } from '$lib/snackbar.svelte';

/** The markdown text an image is inserted into. */
export interface DraftTarget {
	get: () => string;
	set: (value: string) => void;
}

interface PendingImage {
	file: File;
	textarea: HTMLTextAreaElement;
	target: DraftTarget;
}

const IMAGE_EXT: Record<string, string> = {
	'image/png': 'png',
	'image/jpeg': 'jpg',
	'image/gif': 'gif',
	'image/webp': 'webp',
	'image/bmp': 'bmp',
	'image/svg+xml': 'svg'
};

export class PastedImages {
	/** Images waiting to be named; the first one's name prompt is showing. */
	pending = $state<PendingImage[]>([]);
	/** Uploads in progress (saving should wait for them). */
	uploading = $state(0);

	/** `upload` stores the file and returns the URL to embed. */
	constructor(private upload: (file: File) => Promise<string>) {}

	pasteInto(target: DraftTarget) {
		return (e: ClipboardEvent & { currentTarget: HTMLTextAreaElement }) => {
			const images = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith('image/'));
			if (!images.length) return;
			e.preventDefault();
			e.stopPropagation(); // don't also run a surrounding paste-to-attachments handler
			const textarea = e.currentTarget;
			this.pending = [...this.pending, ...images.map((file) => ({ file, textarea, target }))];
		};
	}

	async confirm(name: string) {
		const item = this.pending[0];
		this.pending = this.pending.slice(1);
		if (!item) return;
		const ext = IMAGE_EXT[item.file.type] ?? 'png';
		// Only real image extensions count, so a name like "scan 10.0.0.55" still gets one.
		const extPattern = /\.(png|jpe?g|gif|webp|bmp|svg)$/i;
		const filename = extPattern.test(name) ? name : `${name}.${ext}`;
		const alt = name.replace(extPattern, '').replace(/[[\]]/g, '');
		const token = `![Uploading ${alt}…](#upload-${Math.random().toString(36).slice(2, 10)})`;
		const { target } = item;
		this.insertAtCaret(item.textarea, target, token);
		this.uploading += 1;
		try {
			const url = await this.upload(new File([item.file], filename, { type: item.file.type }));
			const markdown = `![${alt}](${url})`;
			const text = target.get();
			target.set(
				text.includes(token) ? text.replace(token, markdown) : `${text}${text.endsWith('\n') || !text ? '' : '\n'}${markdown}\n`
			);
		} catch (err) {
			target.set(target.get().replace(token, ''));
			snackbar.error(err instanceof ApiError ? `Image upload failed: ${err.message}` : 'Image upload failed');
		} finally {
			this.uploading -= 1;
		}
	}

	cancel() {
		const item = this.pending[0];
		this.pending = this.pending.slice(1);
		if (item) this.refocus(item.textarea);
	}

	/** Insert text at the textarea's caret (or over its selection) and put the caret after it. */
	private insertAtCaret(textarea: HTMLTextAreaElement, target: DraftTarget, text: string) {
		const current = target.get();
		const start = textarea.selectionStart ?? current.length;
		const end = textarea.selectionEnd ?? start;
		const before = current.slice(0, start);
		const after = current.slice(end);
		// Give the image its own line so it renders as a block, not mid-sentence.
		const lead = before && !before.endsWith('\n') ? '\n' : '';
		const trail = after.startsWith('\n') ? '' : '\n';
		const inserted = lead + text + trail;
		target.set(before + inserted + after);
		this.refocus(textarea, start + inserted.length);
	}

	/** Return the caret to the text box once the name prompt has closed. Focus calls made
	 *  while the prompt is still the active modal dialog are ignored by the browser. */
	private refocus(textarea: HTMLTextAreaElement, caret?: number) {
		void tick().then(() =>
			requestAnimationFrame(() => {
				if (this.pending.length > 0) return; // another name prompt is open
				textarea.focus();
				if (caret !== undefined) textarea.setSelectionRange(caret, caret);
			})
		);
	}
}
