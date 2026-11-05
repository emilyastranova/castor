/**
 * App-wide image viewer: opens images full screen with zoom, pan and copy/download tools
 * instead of navigating to the raw file.
 *
 *   lightbox.open([{ src, name }], 0);
 *   <div onclick={openImagesIn}>{@html renderMarkdown(body)}</div>   // any images inside
 *
 * `ImageViewer.svelte` (mounted once in the root layout) renders it.
 */
import { tick } from 'svelte';

export interface LightboxImage {
	src: string;
	/** Shown in the viewer and used as the download file name. */
	name?: string;
}

class LightboxStore {
	images = $state<LightboxImage[]>([]);
	index = $state(0);
	/** Focus goes back here when the viewer closes. */
	returnFocus: HTMLElement | null = null;

	get current(): LightboxImage | null {
		return this.images[this.index] ?? null;
	}

	open(images: LightboxImage[], index = 0): void {
		if (!images.length) return;
		this.returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
		this.images = images;
		this.index = Math.min(Math.max(index, 0), images.length - 1);
	}

	close(): void {
		this.images = [];
		const el = this.returnFocus;
		this.returnFocus = null;
		if (el?.isConnected) void tick().then(() => el.focus());
	}

	step(by: number): void {
		const n = this.images.length;
		if (n > 1) this.index = (this.index + by + n) % n;
	}
}

export const lightbox = new LightboxStore();

/** Name for an image element: its alt text, else the last part of its URL. */
function nameOf(img: HTMLImageElement): string {
	if (img.alt.trim()) return img.alt.trim();
	try {
		return decodeURIComponent(new URL(img.src).pathname.split('/').filter(Boolean).pop() ?? 'image');
	} catch {
		return 'image';
	}
}

/**
 * Click handler for rendered markdown: a click on an image opens the viewer with every
 * image in the same block, starting at the clicked one.
 */
export function openImagesIn(e: MouseEvent): void {
	const img = (e.target as HTMLElement).closest('img');
	const root = e.currentTarget as HTMLElement | null;
	if (!img?.src || !root || img.closest('.mention')) return; // a mention's picture isn't content
	e.preventDefault();
	e.stopPropagation();
	const all = Array.from(root.querySelectorAll('img')).filter((i) => i.src && !i.closest('.mention'));
	lightbox.open(
		all.map((i) => ({ src: i.src, name: nameOf(i) })),
		Math.max(all.indexOf(img), 0)
	);
}
