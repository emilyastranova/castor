<script lang="ts">
	import type { ProjectFile } from '$lib/api';
	import { api } from '$lib/api';
	import { Button, Dialog, Icon, IconButton } from '$lib/m3';
	import { lightbox } from '$lib/lightbox.svelte';
	import { snapshots } from '$lib/snapshots.svelte';
	import { CATEGORY, SOURCE, cardHref, extension, isPdf, isPicture, isReadable } from '$lib/files';
	import { absoluteTime, bytes, relativeTime } from '$lib/format';

	/** One file of a project's Files, looked at: its picture, text or PDF beside where it came from,
	 *  with the files around it a key press away. */
	interface Props {
		open?: boolean;
		projectId: string;
		files: ProjectFile[];
		index?: number;
		canDelete?: boolean;
		ondelete?: (f: ProjectFile) => void;
	}
	let { open = $bindable(false), projectId, files, index = $bindable(0), canDelete = false, ondelete }: Props = $props();

	let file = $derived(files[index] as ProjectFile | undefined);
	let pictures = $derived(files.filter(isPicture));

	const TEXT_LIMIT = 256 * 1024;
	let text = $state<string | null>(null);
	let truncated = $state(false);
	let textError = $state(false);
	let wrap = $state(true);
	$effect(() => {
		const f = file;
		text = null;
		truncated = textError = false;
		if (!open || !f || !isReadable(f)) return;
		let stale = false;
		fetch(api().attachments.url(f.id, true), { headers: { Range: `bytes=0-${TEXT_LIMIT - 1}` } })
			.then(async (res) => {
				if (!res.ok) throw new Error(String(res.status));
				const body = await res.text();
				if (stale) return;
				truncated = f.size > TEXT_LIMIT;
				text = body.length > TEXT_LIMIT ? body.slice(0, TEXT_LIMIT) : body;
			})
			.catch(() => {
				if (!stale) textError = true;
			});
		return () => (stale = true);
	});

	function step(by: number) {
		if (files.length > 1) index = (index + by + files.length) % files.length;
	}
	function onkeydown(e: KeyboardEvent) {
		if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
		const t = e.target as HTMLElement;
		if (t.closest('input, textarea, select, [contenteditable]')) return;
		if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
			e.preventDefault();
			step(e.key === 'ArrowRight' ? 1 : -1);
		}
	}

	function fullScreen() {
		if (!file) return;
		lightbox.open(
			pictures.map((p) => ({ src: api().attachments.url(p.id, true), name: p.filename })),
			Math.max(pictures.findIndex((p) => p.id === file!.id), 0)
		);
	}
	const LANG: Record<string, string> = { JSON: 'json', XML: 'xml', YAML: 'yaml', YML: 'yaml', CSV: 'plaintext', PY: 'python', SH: 'bash', JS: 'javascript', TS: 'typescript', HTML: 'xml', MD: 'markdown', SQL: 'sql', PS1: 'powershell' };
	function snapshot() {
		if (!file || text === null) return;
		snapshots.open({ text, title: file.filename, language: LANG[extension(file.filename)] ?? null });
	}
</script>

<svelte:window onkeydown={open ? onkeydown : undefined} />

<Dialog bind:open size="lg" initialFocus="dialog">
	{#snippet header()}
		{#if file}
			<div class="flex items-center gap-2 px-4 pt-4 pb-3 sm:px-6 sm:pt-5">
				<Icon name={CATEGORY[file.category].icon} class="shrink-0 text-secondary" />
				<h2 class="min-w-0 flex-1 truncate title-large" title={file.filename}>{file.filename}</h2>
				{#if files.length > 1}
					<span class="hidden label-medium text-on-surface-variant tabular-nums sm:inline">{index + 1} of {files.length}</span>
					<IconButton icon="chevron_left" label="Previous file (←)" onclick={() => step(-1)} />
					<IconButton icon="chevron_right" label="Next file (→)" onclick={() => step(1)} />
				{/if}
				<IconButton icon="close" label="Close" onclick={() => (open = false)} />
			</div>
		{/if}
	{/snippet}

	{#if file}
		<div class="flex h-full min-h-0 flex-col gap-4 pb-4 lg:flex-row">
			<!-- What's in it -->
			<div class="flex min-h-[16rem] min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-outline-variant/60 bg-surface-container-lowest">
				{#if isPicture(file)}
					<button type="button" class="group relative flex min-h-0 flex-1 cursor-zoom-in items-center justify-center p-3" onclick={fullScreen} aria-label="View full screen">
						{#key file.id}
							<img src={api().attachments.url(file.id, true)} alt={file.filename} class="max-h-full max-w-full object-contain" />
						{/key}
						<span class="absolute right-3 bottom-3 flex items-center gap-1 rounded-full bg-surface-container-high/90 px-3 py-1 label-medium text-on-surface opacity-0 shadow-e1 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
							<Icon name="fullscreen" size={18} />Full screen
						</span>
					</button>
				{:else if isPdf(file)}
					{#key file.id}
						<iframe title={file.filename} src={api().attachments.url(file.id, true)} class="min-h-[60vh] w-full flex-1 border-0"></iframe>
					{/key}
				{:else if isReadable(file)}
					<div class="flex items-center gap-2 border-b border-outline-variant/60 px-3 py-1.5">
						<span class="flex-1 label-medium text-on-surface-variant">
							{#if truncated}The first {bytes(TEXT_LIMIT)} of {bytes(file.size)}{:else if text !== null}{text.split('\n').length.toLocaleString()} lines{/if}
						</span>
						<IconButton size="sm" icon={wrap ? 'wrap_text' : 'notes'} label={wrap ? 'Don’t wrap lines' : 'Wrap lines'} selected={wrap} onclick={() => (wrap = !wrap)} />
						<IconButton size="sm" icon="photo_camera" label="Snapshot of this text" disabled={text === null} onclick={snapshot} />
					</div>
					{#if textError}
						<p class="p-4 body-medium text-error">Castor couldn’t read this file.</p>
					{:else if text === null}
						<p class="p-4 body-medium text-on-surface-variant">Reading…</p>
					{:else if !text.trim()}
						<p class="p-4 body-medium text-on-surface-variant">It’s empty.</p>
					{:else}
						<pre class="min-h-0 flex-1 overflow-auto p-3 font-mono text-[0.8125rem] leading-relaxed text-on-surface {wrap ? 'break-words whitespace-pre-wrap' : 'whitespace-pre'}">{text}</pre>
					{/if}
				{:else}
					<div class="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
						<Icon name={CATEGORY[file.category].icon} size={48} class="text-on-surface-variant/70" />
						<p class="title-medium text-on-surface">{extension(file.filename) || 'A file'} · {bytes(file.size)}</p>
						<p class="body-medium text-on-surface-variant">Castor can’t show this kind of file. Download it to open it.</p>
					</div>
				{/if}
			</div>

			<!-- Where it came from -->
			<aside class="flex shrink-0 flex-col gap-4 lg:w-72">
				<dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2.5 body-medium">
					<dt class="text-on-surface-variant">On</dt>
					<dd class="min-w-0 text-on-surface">
						{#if file.task_id}
							<a class="text-primary hover:underline" href={cardHref(projectId, file)}>{file.card_title}</a>
						{:else}
							Notes and posts
						{/if}
					</dd>
					{#if file.run_id}
						<dt class="text-on-surface-variant">Run</dt>
						<dd class="text-on-surface">{file.run_name}{#if file.run_started}<span class="text-on-surface-variant"> · {relativeTime(file.run_started)}</span>{/if}</dd>
					{/if}
					{#if file.step}
						<dt class="text-on-surface-variant">Step</dt>
						<dd class="text-on-surface">{file.step}</dd>
					{/if}
					{#if file.agent_name}
						<dt class="text-on-surface-variant">Agent</dt>
						<dd class="text-on-surface"><a class="hover:underline" href="/agents">{file.agent_name}</a></dd>
					{/if}
					<dt class="text-on-surface-variant">From</dt>
					<dd class="flex items-center gap-1.5 text-on-surface"><Icon name={SOURCE[file.source].icon} size={18} class="text-on-surface-variant" />{SOURCE[file.source].label}</dd>
					{#if file.uploader}
						<dt class="text-on-surface-variant">By</dt>
						<dd class="text-on-surface">{file.uploader}</dd>
					{/if}
					<dt class="text-on-surface-variant">Added</dt>
					<dd class="text-on-surface" title={absoluteTime(file.created_at)}>{relativeTime(file.created_at)}</dd>
					<dt class="text-on-surface-variant">Size</dt>
					<dd class="text-on-surface tabular-nums">{bytes(file.size)}</dd>
					<dt class="text-on-surface-variant">Type</dt>
					<dd class="min-w-0 truncate text-on-surface" title={file.content_type}>{file.content_type}</dd>
				</dl>
				<div class="mt-auto flex flex-wrap gap-2">
					<!-- Not a link: the app's router would take /api/… for a page. The download answers with
					     Content-Disposition: attachment, so the page stays put. -->
					<Button variant="filled" icon="download" onclick={() => file && (window.location.href = api().attachments.url(file.id))}>Download</Button>
					{#if file.task_id}
						<Button variant="outlined" icon="open_in_new" href={cardHref(projectId, file) ?? undefined}>Open the card</Button>
					{/if}
					{#if canDelete}
						<Button variant="text" icon="delete" onclick={() => file && ondelete?.(file)}>Delete</Button>
					{/if}
				</div>
			</aside>
		</div>
	{/if}
</Dialog>
