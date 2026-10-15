<script lang="ts">
	import type { Job, LogChunk, UUID } from '$lib/api';
	import { api } from '$lib/api';
	import { realtime } from '$lib/realtime.svelte';
	import { IconButton, Progress } from '$lib/m3';
	import { duration } from '$lib/format';
	import { snackbar } from '$lib/snackbar.svelte';

	interface Props {
		job: Job;
		oncancel?: (job: Job) => void;
	}
	let { job, oncancel }: Props = $props();

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

<div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md bg-[#101418] text-[#e2e8f0]">
	<div class="flex h-10 items-center gap-2 border-b border-white/10 px-3 label-medium text-white/70">
		{#if live}<Progress variant="circular" size={14} />{/if}
		<span class="truncate">{job.status}{job.exit_code !== null ? ` · exit ${job.exit_code}` : ''}{job.started_at ? ` · ${duration(job.started_at, job.finished_at)}` : ''}</span>
		{#if job.error}<span class="truncate text-[#ffb4ab]">· {job.error}</span>{/if}
		<span class="flex-1"></span>
		{#if live && oncancel}
			<IconButton icon="stop_circle" label="Cancel job" size="sm" class="text-[#ffb4ab]" onclick={() => oncancel(job)} />
		{/if}
		<IconButton icon="content_copy" label="Copy output" size="sm" class="text-white/70" onclick={copy} />
		<IconButton icon="download" label="Download output" size="sm" class="text-white/70" href="/api/jobs/{job.id}/output" />
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
