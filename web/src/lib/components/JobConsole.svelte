<script lang="ts">
	import type { Job, LogChunk, UUID } from '$lib/api';
	import { api } from '$lib/api';
	import { realtime } from '$lib/realtime.svelte';
	import { tick } from 'svelte';
	import { fade } from 'svelte/transition';
	import { IconButton, Progress } from '$lib/m3';
	import { duration } from '$lib/format';
	import { snackbar } from '$lib/snackbar.svelte';

	interface Props {
		job: Job;
		oncancel?: (job: Job) => void;
		/** Shown in the header bar while expanded, so the card is still identifiable. */
		title?: string;
	}
	let { job, oncancel, title }: Props = $props();

	// ---- expand to fill the card ----
	// The console box animates its real size and position (not a scale transform), so text
	// stays crisp and re-wraps as it grows. `slot` always holds the console's place in the
	// layout; while expanded or animating, `box` is absolutely positioned against the card's
	// <dialog>, which is a fixed-position element and the nearest positioned ancestor.
	type Mode = 'inline' | 'expanding' | 'expanded' | 'collapsing';
	const DURATION = 300;
	const EASING = 'cubic-bezier(0.4, 0, 0.2, 1)'; // Material standard ease-in-out
	const RADIUS_INLINE = 'var(--md-sys-shape-corner-medium)';
	const RADIUS_EXPANDED = 'var(--md-sys-shape-corner-extra-large)';

	let mode = $state<Mode>('inline');
	let expanded = $derived(mode === 'expanding' || mode === 'expanded');
	let slot: HTMLDivElement | undefined = $state();
	let box: HTMLDivElement | undefined = $state();
	let collapseTo = $state({ top: 0, left: 0, width: 0, height: 0 });
	let animation: Animation | null = null;

	interface Box {
		top: number;
		left: number;
		width: number;
		height: number;
	}
	const relativeTo = (el: Element, container: Element): Box => {
		const r = el.getBoundingClientRect();
		const c = container.getBoundingClientRect();
		return { top: r.top - c.top, left: r.left - c.left, width: r.width, height: r.height };
	};
	const px = (b: Box) => ({
		top: `${b.top}px`,
		left: `${b.left}px`,
		width: `${b.width}px`,
		height: `${b.height}px`
	});

	async function setExpanded(next: boolean) {
		if (!box || !slot || next === expanded) return;
		const container = box.closest('dialog') ?? document.documentElement;
		// Start from wherever the box is right now; that also covers reversing mid-animation.
		const from = relativeTo(box, container);
		animation?.cancel();
		animation = null;
		const c = container.getBoundingClientRect();
		const to: Box = next
			? { top: 0, left: 0, width: c.width, height: c.height }
			: relativeTo(slot, container);

		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
			mode = next ? 'expanded' : 'inline';
			return;
		}
		if (!next) collapseTo = to; // the box's resting geometry while it shrinks back
		mode = next ? 'expanding' : 'collapsing';
		await tick();
		const running = box.animate(
			[
				{ ...px(from), borderRadius: next ? RADIUS_INLINE : RADIUS_EXPANDED },
				{ ...px(to), borderRadius: next ? RADIUS_EXPANDED : RADIUS_INLINE }
			],
			{ duration: DURATION, easing: EASING }
		);
		animation = running;
		followBottomWhile(running);
		running.onfinish = () => {
			if (animation !== running) return; // superseded by a reversal
			animation = null;
			mode = next ? 'expanded' : 'inline';
		};
	}

	/** Keep live output pinned to the bottom while the pane changes size. */
	function followBottomWhile(running: Animation) {
		const step = () => {
			if (pane && stickToBottom) pane.scrollTop = pane.scrollHeight;
			if (animation === running) requestAnimationFrame(step);
		};
		requestAnimationFrame(step);
	}

	// While expanded, Escape collapses the console instead of closing the card. Listen in the
	// capture phase so this runs before the dialog's own Escape handling.
	$effect(() => {
		if (!expanded) return;
		const onKey = (e: KeyboardEvent) => {
			if (e.key !== 'Escape' || e.defaultPrevented) return;
			e.preventDefault();
			e.stopPropagation();
			void setExpanded(false);
		};
		document.addEventListener('keydown', onKey, true);
		return () => document.removeEventListener('keydown', onKey, true);
	});

	$effect(() => () => animation?.cancel());

	let chunks = $state<LogChunk[]>([]);
	let loading = $state(true);
	let pane: HTMLDivElement | undefined = $state();
	let stickToBottom = $state(true);
	let live = $derived(['queued', 'dispatched', 'running'].includes(job.status));

	// (Re)load whenever the job changes; replay anything we missed while loading.
	$effect(() => {
		const id: UUID = job.id;
		let cancelled = false;
		const pending: LogChunk[] = [];
		loading = true;
		chunks = [];
		const unsubscribe = realtime.subscribe((e) => {
			if (e.name !== 'job.log') return;
			const d = e.data as { job_id: UUID; seq: number; stream: LogChunk['stream']; data: string };
			if (d.job_id !== id) return;
			const chunk = { seq: d.seq, stream: d.stream, data: d.data, ts: new Date().toISOString() };
			if (loading) pending.push(chunk);
			else append(chunk);
		});
		api()
			.jobs.get(id)
			.then((detail) => {
				if (cancelled) return;
				chunks = detail.logs;
				loading = false;
				for (const c of pending) append(c);
			})
			.catch(() => {
				loading = false;
			});
		return () => {
			cancelled = true;
			unsubscribe();
		};
	});

	function append(chunk: LogChunk) {
		if (chunks.some((c) => c.seq === chunk.seq)) return;
		chunks = [...chunks, chunk];
	}

	$effect(() => {
		void chunks.length;
		if (pane && stickToBottom) requestAnimationFrame(() => pane && (pane.scrollTop = pane.scrollHeight));
	});

	function onScroll() {
		if (!pane) return;
		stickToBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 40;
	}

	async function copy() {
		await navigator.clipboard.writeText(chunks.filter((c) => c.stream !== 'system').map((c) => c.data).join(''));
		snackbar.show('Output copied');
	}
</script>

<!-- `slot` keeps the console's place in the card; `box` is the console itself. The same box
     element is used in every state, so output and scroll position carry over. -->
<div bind:this={slot} class="flex min-h-0 flex-1 flex-col">
	<div
		bind:this={box}
		class="flex min-h-0 flex-col overflow-hidden bg-[#101418] text-[#e2e8f0] {mode === 'inline'
			? 'flex-1 rounded-md'
			: 'absolute z-30 rounded-xl'} {expanded ? 'inset-0' : ''}"
		style:top={mode === 'collapsing' ? `${collapseTo.top}px` : undefined}
		style:left={mode === 'collapsing' ? `${collapseTo.left}px` : undefined}
		style:width={mode === 'collapsing' ? `${collapseTo.width}px` : undefined}
		style:height={mode === 'collapsing' ? `${collapseTo.height}px` : undefined}
		role={expanded ? 'region' : undefined}
		aria-label={expanded ? 'Expanded job output' : undefined}
	>
		<div class="flex h-10 shrink-0 items-center gap-2 border-b border-white/10 px-3 label-medium text-white/70">
			{#if mode === 'expanded' && title}<span class="flex min-w-0 max-w-[40%] shrink-0 items-center gap-2" transition:fade={{ duration: 150 }}><span class="truncate title-small text-white/90">{title}</span><span class="text-white/30">·</span></span>{/if}
			{#if live}<Progress variant="circular" size={14} />{/if}
			<span class="truncate">{job.status}{job.exit_code !== null ? ` · exit ${job.exit_code}` : ''}{job.started_at ? ` · ${duration(job.started_at, job.finished_at)}` : ''}</span>
			{#if job.error}<span class="truncate text-[#ffb4ab]">· {job.error}</span>{/if}
			<span class="flex-1"></span>
			{#if live && oncancel}
				<IconButton icon="stop_circle" label="Cancel job" size="sm" class="text-[#ffb4ab]" onclick={() => oncancel(job)} />
			{/if}
			<IconButton icon="content_copy" label="Copy output" size="sm" class="text-white/70" onclick={copy} />
			<IconButton icon="download" label="Download output" size="sm" class="text-white/70" href="/api/jobs/{job.id}/output" />
			<IconButton
				icon={expanded ? 'close_fullscreen' : 'open_in_full'}
				label={expanded ? 'Collapse output' : 'Expand output'}
				size="sm"
				class="text-white/70"
				onclick={() => setExpanded(!expanded)}
			/>
		</div>
		<div bind:this={pane} onscroll={onScroll} class="min-h-0 flex-1 overflow-auto p-3 font-mono text-[12.5px] leading-5">
			{#if loading}
				<span class="text-white/50">Loading output…</span>
			{:else if chunks.length === 0}
				<span class="text-white/50">{live ? 'Waiting for output…' : 'No output.'}</span>
			{:else}
				{#each chunks as c (c.seq)}<span
						class={c.stream === 'stderr' ? 'text-[#ffb4ab]' : c.stream === 'system' ? 'text-white/45 italic' : ''}
						style="white-space: pre-wrap; word-break: break-all">{c.data}</span
					>{/each}
				{#if live}<span class="inline-block h-4 w-2 animate-pulse bg-white/60 align-text-bottom"></span>{/if}
			{/if}
		</div>
	</div>
</div>
