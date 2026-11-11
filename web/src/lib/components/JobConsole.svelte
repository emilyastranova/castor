<script lang="ts">
	import type { Job, LogChunk, UUID } from '$lib/api';
	import { api } from '$lib/api';
	import { realtime } from '$lib/realtime.svelte';
	import { tick } from 'svelte';
	import { fade } from 'svelte/transition';
	import { Button, Icon, IconButton, Progress } from '$lib/m3';
	import { duration } from '$lib/format';
	import { snackbar } from '$lib/snackbar.svelte';
	import { currentTheme } from '$lib/m3/theme';
	import type { Terminal as XTerm } from '@xterm/xterm';
	import type { FitAddon as XFit } from '@xterm/addon-fit';
	import '@xterm/xterm/css/xterm.css';

	/** A job's output in a real terminal emulator (xterm.js): colours, cursor movement and
	 *  full-screen programs render as they would in a terminal. An interactive (tty) job can
	 *  be taken over: keystrokes and the window size go to it, so tmux, vim or a sudo prompt
	 *  work from the browser. */
	interface Props {
		job: Job;
		oncancel?: (job: Job) => void;
		/** Shown in the header bar while expanded, so the card is still identifiable. */
		title?: string;
		/** May type into an interactive job (operators on the project). */
		canInteract?: boolean;
	}
	let { job, oncancel, title, canInteract = false }: Props = $props();

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
	const px = (b: Box) => ({ top: `${b.top}px`, left: `${b.left}px`, width: `${b.width}px`, height: `${b.height}px` });

	async function setExpanded(next: boolean) {
		if (!box || !slot || next === expanded) return;
		const container = box.closest('dialog') ?? document.documentElement;
		const from = relativeTo(box, container);
		animation?.cancel();
		animation = null;
		const c = container.getBoundingClientRect();
		const to: Box = next ? { top: 0, left: 0, width: c.width, height: c.height } : relativeTo(slot, container);
		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
			mode = next ? 'expanded' : 'inline';
			return;
		}
		if (!next) collapseTo = to;
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
		running.onfinish = () => {
			if (animation !== running) return;
			animation = null;
			mode = next ? 'expanded' : 'inline';
		};
	}

	// While expanded, Escape collapses the console instead of closing the card, except while
	// you're in control of a terminal: then Escape belongs to the program (vim, tmux…).
	$effect(() => {
		if (!expanded || controlling) return;
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

	// ---- the terminal ----
	const THEME = {
		background: '#101418',
		foreground: '#e2e8f0',
		cursor: '#e2e8f0',
		cursorAccent: '#101418',
		selectionBackground: 'rgba(108, 182, 255, 0.35)',
		black: '#1d2228',
		red: '#ff6b6b',
		green: '#69d48c',
		yellow: '#f2cc60',
		blue: '#6cb6ff',
		magenta: '#d38aff',
		cyan: '#56d4dd',
		white: '#c9d1d9',
		brightBlack: '#6e7681',
		brightRed: '#ff8f8f',
		brightGreen: '#8ae4a6',
		brightYellow: '#f8e08e',
		brightBlue: '#9ecbff',
		brightMagenta: '#e2b0ff',
		brightCyan: '#8be9f0',
		brightWhite: '#f0f6fc'
	};
	let host: HTMLDivElement | undefined = $state();
	let term: XTerm | null = null;
	let fit: XFit | null = null;
	let ready = $state(false);
	let loading = $state(true);
	let empty = $state(true);
	let chunks: LogChunk[] = [];
	const seen = new Set<number>();
	let live = $derived(['queued', 'dispatched', 'running'].includes(job.status));
	let tty = $derived(!!job.tty);

	/** Plain streams need CR before LF; stderr in red, Castor's own lines dim. Terminal
	 *  output from an interactive job is written exactly as it came. */
	function render(c: LogChunk): string {
		if (tty && c.stream === 'stdout') return c.data;
		const text = c.data.replace(/\r?\n/g, '\r\n');
		if (c.stream === 'stderr') return `\x1b[31m${text}\x1b[0m`;
		if (c.stream === 'system') return `\x1b[2;3m${text}\x1b[0m`;
		return text;
	}
	function write(c: LogChunk) {
		if (seen.has(c.seq)) return;
		seen.add(c.seq);
		chunks.push(c);
		empty = false;
		term?.write(render(c));
	}

	/** Viewers of an interactive job see it at the size the person in control set (so a
	 *  full-screen program lines up); everything else fits the box. */
	function layout() {
		if (!term || !fit) return;
		if (tty && !controlling && job.tty_size) term.resize(job.tty_size.cols, job.tty_size.rows);
		else fit.fit();
	}

	// Only a different job rebuilds the terminal: the job object itself is replaced on every
	// update (status, size, who's in control), and tracking it would rebuild it each time.
	let jobId = $derived(job.id);
	$effect(() => {
		const id: UUID = jobId;
		let cancelled = false;
		let observer: ResizeObserver | null = null;
		const pending: LogChunk[] = [];
		loading = true;
		const unsubscribe = realtime.subscribe((e) => {
			if (e.name === 'job.log') {
				const d = e.data as { job_id: UUID; seq: number; stream: LogChunk['stream']; data: string };
				if (d.job_id !== id) return;
				const chunk = { seq: d.seq, stream: d.stream, data: d.data, ts: new Date().toISOString() };
				if (loading) pending.push(chunk);
				else write(chunk);
			} else if (e.name === 'job.resize') {
				const d = e.data as { job_id: string; cols: number; rows: number };
				if (d.job_id === id && !controlling) term?.resize(d.cols, d.rows);
			}
		});
		(async () => {
			const [{ Terminal }, { FitAddon }, { WebLinksAddon }] = await Promise.all([
				import('@xterm/xterm'),
				import('@xterm/addon-fit'),
				import('@xterm/addon-web-links')
			]);
			await document.fonts?.ready; // measure the monospace font once it's loaded
			if (cancelled || !host) return;
			// The AstraLabs theme: carbon, bone and an acid cursor, in Martian Mono.
			const astra = currentTheme().id === 'astralabs';
			const acid = getComputedStyle(document.documentElement).getPropertyValue('--al-acid').trim() || '#b6ff2e';
			term = new Terminal({
				theme: astra
					? { ...THEME, background: '#0a0909', foreground: '#ede6e1', cursor: acid, cursorAccent: '#0a0909', selectionBackground: `${acid}47`, black: '#1c1b1a', brightBlack: '#8e8380', red: '#ff5a63', yellow: '#ffa21f' }
					: THEME,
				fontFamily: astra ? '"Martian Mono", "Roboto Mono", ui-monospace, monospace' : '"Roboto Mono", ui-monospace, SFMono-Regular, Menlo, monospace',
				fontSize: astra ? 11.5 : 12.5,
				lineHeight: 1.25,
				scrollback: 20000,
				convertEol: false,
				disableStdin: true,
				cursorBlink: false,
				cursorStyle: 'block',
				allowTransparency: false,
				macOptionIsMeta: true,
				rightClickSelectsWord: true
			});
			fit = new FitAddon();
			term.loadAddon(fit);
			term.loadAddon(new WebLinksAddon());
			term.open(host);
			layout();
			observer = new ResizeObserver(() => {
				layout();
				if (controlling) sendSize();
			});
			observer.observe(host);
			ready = true;
			try {
				const detail = await api().jobs.get(id);
				if (cancelled) return;
				for (const c of detail.logs) write(c);
			} catch {
				/* shown as empty */
			}
			loading = false;
			for (const c of pending) write(c);
		})();
		return () => {
			cancelled = true;
			unsubscribe();
			observer?.disconnect();
			release();
			term?.dispose();
			term = null;
			fit = null;
			ready = false;
			chunks = [];
			seen.clear();
			empty = true;
		};
	});

	// ---- taking control of an interactive job ----
	let controlling = $state(false);
	let socket: WebSocket | null = null;
	let sizeTimer: ReturnType<typeof setTimeout> | undefined;

	function sendSize() {
		clearTimeout(sizeTimer);
		sizeTimer = setTimeout(() => {
			if (term && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ t: 'resize', cols: term.cols, rows: term.rows }));
		}, 120);
	}
	async function takeControl() {
		if (!term || controlling) return;
		const proto = location.protocol === 'https:' ? 'wss' : 'ws';
		const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${job.id}/terminal`);
		socket = ws;
		ws.onopen = () => {
			controlling = true;
			if (!expanded) void setExpanded(true);
			if (!term) return;
			term.options.disableStdin = false;
			term.options.cursorBlink = true;
			fit?.fit();
			sendSize();
			term.focus();
		};
		ws.onmessage = (e) => {
			if (JSON.parse(e.data).t === 'ended') release();
		};
		ws.onclose = (e) => {
			if (e.code === 4403) snackbar.error('Only people who can change this project can type into it');
			release();
		};
		dataSub?.dispose();
		dataSub = term.onData((d) => {
			if (controlling && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ t: 'in', d }));
		});
	}
	let dataSub: { dispose(): void } | null = null;
	function release() {
		controlling = false;
		dataSub?.dispose();
		dataSub = null;
		if (socket) {
			const s = socket;
			socket = null;
			s.onclose = null;
			s.close();
		}
		if (term) {
			term.options.disableStdin = true;
			term.options.cursorBlink = false;
		}
		layout();
	}
	$effect(() => {
		if (!live && controlling) release();
	});
	function interrupt() {
		socket?.send(JSON.stringify({ t: 'signal', name: 'INT' }));
		term?.focus();
	}

	const ANSI = /\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])/g;
	async function copy() {
		const selected = term?.getSelection();
		const text = selected || chunks.filter((c) => c.stream !== 'system').map((c) => c.data).join('').replace(ANSI, '');
		await navigator.clipboard.writeText(text);
		snackbar.show(selected ? 'Selection copied' : 'Output copied');
	}
</script>

<!-- `slot` keeps the console's place in the card; `box` is the console itself. The same box
     element is used in every state, so output and scroll position carry over. -->
<div bind:this={slot} class="flex min-h-0 flex-1 flex-col">
	<div
		bind:this={box}
		class="job-console flex min-h-0 flex-col overflow-hidden bg-[#101418] text-[#e2e8f0] {mode === 'inline'
			? 'flex-1 rounded-md'
			: 'absolute z-30 rounded-xl'} {expanded ? 'inset-0' : ''} {controlling ? 'ring-2 ring-[#6cb6ff]' : ''}"
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
			{#if tty}<span class="shrink-0 rounded-full border border-white/20 px-1.5 text-[11px] text-white/60" title="Runs in an interactive terminal">terminal{job.tty_size ? ` ${job.tty_size.cols}×${job.tty_size.rows}` : ''}</span>{/if}
			{#if job.error}<span class="truncate text-[#ffb4ab]">· {job.error}</span>{/if}
			<span class="flex-1"></span>
			{#if controlling}
				<span class="hidden shrink-0 items-center gap-1 text-[#9ecbff] sm:flex"><Icon name="keyboard" size={16} />You're in control</span>
				<IconButton icon="block" label="Send Ctrl+C" size="sm" class="text-white/70" onclick={interrupt} />
				<Button variant="text" class="h-8 text-[#9ecbff]" onclick={release}>Release</Button>
			{:else if live && tty && canInteract}
				<Button variant={job.waiting_input ? 'filled' : 'text'} icon="keyboard" class="h-8 {job.waiting_input ? '' : 'text-[#9ecbff]'}" onclick={takeControl}>Take control</Button>
			{/if}
			{#if live && oncancel}
				<IconButton icon="stop_circle" label="Cancel job" size="sm" class="text-[#ffb4ab]" onclick={() => oncancel(job)} />
			{/if}
			<IconButton icon="content_copy" label="Copy output (or the selection)" size="sm" class="text-white/70" onclick={copy} />
			<IconButton icon="download" label="Download output" size="sm" class="text-white/70" href="/api/jobs/{job.id}/output" />
			<IconButton
				icon={expanded ? 'close_fullscreen' : 'open_in_full'}
				label={expanded ? 'Collapse output' : 'Expand output'}
				size="sm"
				class="text-white/70"
				onclick={() => setExpanded(!expanded)}
			/>
		</div>
		{#if tty && job.status === 'queued'}
			<div class="flex shrink-0 items-center gap-2 border-b border-white/10 bg-white/5 px-3 py-1.5 body-small text-white/70">
				<Icon name="hourglass_top" size={16} />Waiting for an agent that can run interactive terminals. Agents started before this feature need a restart.
			</div>
		{/if}
		{#if live && job.waiting_input && !controlling}
			<div class="flex shrink-0 items-center gap-2 border-b border-[#f2cc60]/30 bg-[#f2cc60]/10 px-3 py-1.5 body-small text-[#f8e08e]" transition:fade={{ duration: 150 }}>
				<Icon name="front_hand" size={16} />Waiting for input: <code class="truncate font-mono">{job.waiting_input}</code>
				{#if canInteract}<span class="ml-auto shrink-0 text-white/60">Take control to answer it</span>{/if}
			</div>
		{/if}
		<div class="relative min-h-0 flex-1 overflow-auto px-2 py-1.5">
			<div bind:this={host} class="h-full min-h-40 w-full"></div>
			{#if ready && !loading && empty}
				<span class="pointer-events-none absolute top-2 left-3 font-mono text-[12.5px] text-white/50">{live ? 'Waiting for output…' : 'No output.'}</span>
			{:else if !ready || loading}
				<span class="pointer-events-none absolute top-2 left-3 font-mono text-[12.5px] text-white/50">Loading output…</span>
			{/if}
		</div>
	</div>
</div>

<style>
	/* xterm draws its own scrollbar area; keep it dark like the console. */
	:global(.xterm .xterm-viewport) {
		background-color: #101418 !important;
	}
</style>
