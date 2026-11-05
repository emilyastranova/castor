<script lang="ts">
	import { lightbox } from '$lib/lightbox.svelte';
	import { Icon, IconButton } from '$lib/m3';

	let dialog = $state<HTMLDialogElement>();
	let stage = $state<HTMLDivElement>();
	let current = $derived(lightbox.current);

	// A modal <dialog> sits in the top layer, so it shows above an open card dialog too.
	$effect(() => {
		if (!dialog) return;
		if (current && !dialog.open) dialog.showModal();
		else if (!current && dialog.open) dialog.close();
	});

	// ---- zoom and pan: scale 1 is "fit to screen"; the image's centre is moved by (x, y) ----
	const MAX = 8;
	let scale = $state(1);
	let x = $state(0);
	let y = $state(0);
	let natural = $state({ w: 0, h: 0 });
	let fitted = $state({ w: 0, h: 0 });
	let loaded = $state(false);
	let broken = $state(false);

	$effect(() => {
		void current?.src;
		scale = 1;
		x = y = 0;
		loaded = broken = false;
	});

	/** Scale that shows the image at its real pixel size (at least 2x, so zooming always helps). */
	let actualScale = $derived(fitted.w ? Math.max(2, natural.w / fitted.w) : 2);

	function zoomTo(next: number, at?: { clientX: number; clientY: number }) {
		next = Math.min(Math.max(next, 1), MAX);
		if (next === 1) {
			scale = 1;
			x = y = 0;
			return;
		}
		// Keep the point under the cursor (or the centre) where it is.
		const r = stage!.getBoundingClientRect();
		const mx = (at?.clientX ?? r.left + r.width / 2) - (r.left + r.width / 2);
		const my = (at?.clientY ?? r.top + r.height / 2) - (r.top + r.height / 2);
		x = mx - (next / scale) * (mx - x);
		y = my - (next / scale) * (my - y);
		scale = next;
	}

	function onload(e: Event) {
		const img = e.currentTarget as HTMLImageElement;
		natural = { w: img.naturalWidth, h: img.naturalHeight };
		fitted = { w: img.clientWidth, h: img.clientHeight };
		loaded = true;
	}

	function onwheel(e: WheelEvent) {
		e.preventDefault();
		zoomTo(scale * Math.exp(-e.deltaY * 0.0025), e);
	}

	// A press that barely moves is a click (toggle zoom); otherwise it pans.
	let drag = $state<{ id: number; sx: number; sy: number; x: number; y: number; moved: boolean } | null>(null);
	function onpointerdown(e: PointerEvent) {
		if (e.button !== 0) return;
		drag = { id: e.pointerId, sx: e.clientX, sy: e.clientY, x, y, moved: false };
		(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
	}
	function onpointermove(e: PointerEvent) {
		if (!drag || drag.id !== e.pointerId) return;
		const dx = e.clientX - drag.sx;
		const dy = e.clientY - drag.sy;
		if (!drag.moved && Math.hypot(dx, dy) < 4) return;
		drag.moved = true;
		if (scale > 1) {
			x = drag.x + dx;
			y = drag.y + dy;
		}
	}
	function onpointerup(e: PointerEvent) {
		if (!drag || drag.id !== e.pointerId) return;
		const click = !drag.moved;
		drag = null;
		if (click) zoomTo(scale > 1 ? 1 : actualScale, e);
	}

	function onkeydown(e: KeyboardEvent) {
		// Escape is handled here, not by the browser's close request, which can close the card
		// dialog underneath too (see m3/Dialog.svelte).
		if (e.key === 'Escape') {
			e.stopPropagation();
			lightbox.close();
		} else if (e.key === 'ArrowRight') lightbox.step(1);
		else if (e.key === 'ArrowLeft') lightbox.step(-1);
		else if (e.key === '+' || e.key === '=') zoomTo(scale * 1.5);
		else if (e.key === '-') zoomTo(scale / 1.5);
		else if (e.key === '0') zoomTo(1);
		else return;
		e.preventDefault();
	}

	// ---- tools ----
	// The page's snackbar sits under the modal viewer, so feedback is shown here.
	let notice = $state<{ text: string; error?: boolean } | null>(null);
	let noticeTimer: ReturnType<typeof setTimeout> | undefined;
	function tell(text: string, error = false) {
		notice = { text, error };
		clearTimeout(noticeTimer);
		noticeTimer = setTimeout(() => (notice = null), 2500);
	}

	/** Clipboards take PNG everywhere; anything else is redrawn onto a canvas first. */
	async function asPng(blob: Blob): Promise<Blob> {
		if (blob.type === 'image/png') return blob;
		const url = URL.createObjectURL(blob);
		try {
			const img = new Image();
			img.src = url;
			await img.decode();
			const canvas = document.createElement('canvas');
			canvas.width = img.naturalWidth || 1024;
			canvas.height = img.naturalHeight || 768;
			canvas.getContext('2d')!.drawImage(img, 0, 0, canvas.width, canvas.height);
			return await new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('encode'))), 'image/png'));
		} finally {
			URL.revokeObjectURL(url);
		}
	}

	async function copyImage() {
		if (!current) return;
		// Safari only allows the write if it starts in the click, so it gets a promise.
		const png = fetch(current.src)
			.then((r) => {
				if (!r.ok) throw new Error(String(r.status));
				return r.blob();
			})
			.then(asPng);
		try {
			await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
			tell('Image copied');
		} catch {
			tell('Could not copy the image', true);
		}
	}

	async function copyLink() {
		if (!current) return;
		try {
			await navigator.clipboard.writeText(new URL(current.src, location.href).href);
			tell('Link copied');
		} catch {
			tell('Could not copy the link', true);
		}
	}

	/** Attachments download with their stored name when asked without `inline`. */
	let downloadHref = $derived(current ? current.src.replace(/([?&])inline=true&?/, '$1').replace(/[?&]$/, '') : '');

	let zoomLabel = $derived(`${Math.round((scale * (fitted.w || 1)) / (natural.w || fitted.w || 1) * 100)}%`);
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<dialog
	bind:this={dialog}
	class="viewer m-0 h-dvh max-h-none w-dvw max-w-none overflow-hidden bg-transparent p-0 outline-none"
	aria-label={current?.name ?? 'Image'}
	oncancel={(e) => {
		e.preventDefault();
		lightbox.close();
	}}
	{onkeydown}
>
	{#if current}
		<div class="flex h-full flex-col">
			<!-- Clicking the backdrop around the image closes the viewer. -->
			<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
			<div
				bind:this={stage}
				class="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden"
				onclick={(e) => e.target === e.currentTarget && lightbox.close()}
				{onwheel}
			>
				{#if broken}
					<p class="flex items-center gap-2 body-large"><Icon name="broken_image" />This image could not be loaded.</p>
				{:else}
					<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
					<img
						src={current.src}
						alt={current.name ?? ''}
						draggable="false"
						class="max-h-[calc(100dvh-8rem)] max-w-[calc(100vw-2rem)] touch-none select-none object-contain shadow-e3 transition-opacity md:max-w-[calc(100vw-9rem)] {loaded ? 'opacity-100' : 'opacity-0'} {scale > 1 ? 'cursor-grab active:cursor-grabbing' : 'cursor-zoom-in'}"
						style:transform="translate({x}px, {y}px) scale({scale})"
						style:transition={drag ? 'none' : 'transform 120ms ease-out, opacity 150ms'}
						{onload}
						onerror={() => (broken = true)}
						{onpointerdown}
						{onpointermove}
						{onpointerup}
						onpointercancel={() => (drag = null)}
					/>
				{/if}

				{#if lightbox.images.length > 1}
					<button type="button" class="nav left-3" aria-label="Previous image" onclick={() => lightbox.step(-1)}><Icon name="chevron_left" size={28} /></button>
					<button type="button" class="nav right-3" aria-label="Next image" onclick={() => lightbox.step(1)}><Icon name="chevron_right" size={28} /></button>
				{/if}

				{#if notice}
					<div class="absolute top-4 left-1/2 -translate-x-1/2 rounded-xs px-4 py-2.5 shadow-e3 body-medium {notice.error ? 'bg-error-container text-on-error-container' : 'bg-inverse-surface text-inverse-on-surface'}" role="status">{notice.text}</div>
				{/if}
			</div>

			<div class="flex shrink-0 flex-col items-center gap-2 px-4 pt-2 pb-4">
				{#if current.name || lightbox.images.length > 1}
					<p class="max-w-full truncate rounded-full bg-surface-container-high/90 px-3 py-1 body-small text-on-surface" title={current.name}>
						{current.name}{#if lightbox.images.length > 1}<span class="text-on-surface-variant">{current.name ? ' · ' : ''}{lightbox.index + 1} of {lightbox.images.length}</span>{/if}
					</p>
				{/if}
				<div class="flex max-w-full items-center gap-0.5 overflow-x-auto rounded-full bg-surface-container-high p-1 text-on-surface shadow-e2">
					<IconButton icon="zoom_out" label="Zoom out (−)" disabled={scale <= 1} onclick={() => zoomTo(scale / 1.5)} />
					<button type="button" class="state-layer h-10 min-w-14 rounded-full px-2 label-large text-on-surface-variant tabular-nums" title="Fit to screen (0)" onclick={() => zoomTo(1)}>{loaded ? zoomLabel : '…'}</button>
					<IconButton icon="zoom_in" label="Zoom in (+)" disabled={scale >= MAX} onclick={() => zoomTo(scale * 1.5)} />
					<span class="mx-1 h-6 w-px shrink-0 bg-outline-variant"></span>
					<IconButton icon="content_copy" label="Copy image" disabled={broken} onclick={copyImage} />
					<IconButton icon="link" label="Copy link" onclick={copyLink} />
					<a class="state-layer focus-ring inline-flex size-10 shrink-0 items-center justify-center rounded-full text-on-surface-variant" href={downloadHref} download={current.name || ''} aria-label="Download" title="Download"><Icon name="download" /></a>
					<a class="state-layer focus-ring inline-flex size-10 shrink-0 items-center justify-center rounded-full text-on-surface-variant" href={current.src} target="_blank" rel="noopener" aria-label="Open original in a new tab" title="Open original in a new tab"><Icon name="open_in_new" /></a>
					<span class="mx-1 h-6 w-px shrink-0 bg-outline-variant"></span>
					<IconButton icon="close" label="Close (Esc)" onclick={() => lightbox.close()} />
				</div>
			</div>
		</div>
	{/if}
</dialog>

<style>
	.viewer::backdrop {
		background: color-mix(in srgb, var(--md-sys-color-scrim) 88%, transparent);
	}
	.nav {
		position: absolute;
		top: 50%;
		display: none;
		width: 3rem;
		height: 3rem;
		transform: translateY(-50%);
		align-items: center;
		justify-content: center;
		border-radius: 9999px;
		background: color-mix(in srgb, var(--md-sys-color-surface-container-high) 85%, transparent);
		color: var(--md-sys-color-on-surface);
	}
	.nav:hover {
		background: var(--md-sys-color-surface-container-highest);
	}
	@media (min-width: 768px) {
		.nav {
			display: flex;
		}
	}
</style>
