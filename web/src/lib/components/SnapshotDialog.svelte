<script lang="ts">
	import type { Attachment, Job, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, IconButton, Select, Switch, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import {
		buildScene,
		compile,
		fileName,
		fontsReady,
		newRecipe,
		readOutput,
		themePalette,
		THEMES,
		toCanvas,
		toPng,
		toSvg,
		type Line,
		type SnapshotRecipe
	} from '$lib/snapshot';

	/** A terminal snapshot: a picture of some of a job's output, for a report. Pick the lines,
	 *  hide what shouldn't be in it, highlight what matters, and copy, download or attach it. An
	 *  attached snapshot keeps its recipe, so it opens here again to be changed. */
	interface Props {
		open?: boolean;
		jobId: UUID;
		taskId: UUID;
		/** The card's title (the window's title, the file's name). */
		title: string;
		/** Logical lines [from, to) to start with (the console's selection). */
		range?: { from: number; to: number } | null;
		/** A snapshot to change. */
		attachment?: Attachment | null;
		/** May attach it to the card. */
		canAttach?: boolean;
		onsaved?: (a: Attachment) => void;
	}
	let { open = $bindable(false), jobId, taskId, title, range = null, attachment = null, canAttach = false, onsaved }: Props = $props();

	let job = $state<Job | null>(null);
	let lines = $state<Line[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let recipe = $state<SnapshotRecipe | null>(null);

	// Load the job and replay its output each time the dialog opens.
	$effect(() => {
		if (!open) return;
		const id = attachment?.job_id ?? jobId;
		let cancelled = false;
		loading = true;
		error = null;
		(async () => {
			try {
				const detail = await api().jobs.get(id);
				const read = await readOutput(detail, detail.logs);
				if (cancelled) return;
				job = detail;
				lines = read;
				const saved = attachment?.snapshot as SnapshotRecipe | null | undefined;
				const r = saved ? { ...newRecipe(detail, title), ...JSON.parse(JSON.stringify(saved)) } : newRecipe(detail, title, range?.from ?? 0, range?.to ?? read.length);
				r.to = Math.min(r.to, read.length);
				r.from = Math.min(r.from, Math.max(0, r.to - 1));
				if (!saved) r.wrap = Math.min(140, Math.max(60, ...read.slice(r.from, r.to).map((l) => l.cells.length)));
				recipe = r;
			} catch (err) {
				error = err instanceof ApiError ? err.message : 'Could not read the output';
			} finally {
				if (!cancelled) loading = false;
			}
		})();
		return () => {
			cancelled = true;
		};
	});

	// ---- the preview: the picture itself, drawn as the PNG will be ----
	let previewHost = $state<HTMLDivElement>();
	let drawn = $state({ width: 0, height: 0 });
	$effect(() => {
		if (!recipe || !job || !previewHost) return;
		const r: SnapshotRecipe = JSON.parse(JSON.stringify(recipe)); // track every field
		const j = job;
		const host = previewHost;
		const t = setTimeout(async () => {
			await fontsReady(r.font_size);
			const scene = buildScene(lines, r, j);
			const canvas = toCanvas(scene, Math.max(1, Math.min(2, window.devicePixelRatio || 1)));
			canvas.style.width = `${scene.width}px`;
			canvas.className = 'max-w-full h-auto';
			host.replaceChildren(canvas);
			drawn = { width: scene.width, height: scene.height };
		}, 60);
		return () => clearTimeout(t);
	});

	// ---- picking lines ----
	const inRange = (i: number) => !!recipe && i >= recipe.from && i < recipe.to;
	function pickLine(i: number, e: MouseEvent) {
		if (!recipe) return;
		if (e.ctrlKey || e.metaKey) {
			recipe.highlight = recipe.highlight.includes(i) ? recipe.highlight.filter((h) => h !== i) : [...recipe.highlight, i].sort((a, b) => a - b);
		} else if (e.shiftKey) {
			if (i < recipe.from) recipe.from = i;
			else recipe.to = i + 1;
		} else {
			recipe.from = i;
			recipe.to = i + 1;
		}
	}
	function everything() {
		if (recipe) [recipe.from, recipe.to] = [0, lines.length];
	}

	// ---- hiding text ----
	let picker = $state<HTMLDivElement>();
	let selection = $state<{ line: number; start: number; end: number }[]>([]);
	/** A string offset in a line's text, as a cell index. */
	function cellOf(line: Line, offset: number): number {
		let at = 0;
		for (let i = 0; i < line.cells.length; i++) {
			if (at >= offset) return i;
			at += line.cells[i].ch.length;
		}
		return line.cells.length;
	}
	function lineOf(node: Node | null): { el: HTMLElement; index: number } | null {
		const el = (node instanceof HTMLElement ? node : node?.parentElement)?.closest<HTMLElement>('[data-line]');
		return el && picker?.contains(el) ? { el, index: Number(el.dataset.line) } : null;
	}
	/** Offset of (node, offset) within a line's text element. */
	function offsetIn(textEl: Element, node: Node, offset: number): number {
		const r = document.createRange();
		r.selectNodeContents(textEl);
		r.setEnd(node, offset);
		return r.toString().length;
	}
	function readSelection() {
		const s = document.getSelection();
		if (!s || s.isCollapsed || !s.rangeCount) return (selection = []);
		const range = s.getRangeAt(0);
		const a = lineOf(range.startContainer);
		const b = lineOf(range.endContainer);
		if (!a || !b) return (selection = []);
		const out: { line: number; start: number; end: number }[] = [];
		for (let i = a.index; i <= b.index; i++) {
			const line = lines[i];
			if (!line) continue;
			const textEl = picker?.querySelector(`[data-line="${i}"] [data-text]`);
			const start = i === a.index && textEl && textEl.contains(range.startContainer) ? cellOf(line, offsetIn(textEl, range.startContainer, range.startOffset)) : 0;
			const end = i === b.index && textEl && textEl.contains(range.endContainer) ? cellOf(line, offsetIn(textEl, range.endContainer, range.endOffset)) : line.cells.length;
			if (end > start) out.push({ line: i, start, end });
		}
		selection = out;
	}
	function hideSelection() {
		if (!recipe || !selection.length) return;
		recipe.hide = [...recipe.hide, ...selection];
		document.getSelection()?.removeAllRanges();
		selection = [];
	}
	/** A line's text in pieces, hidden ones marked (for the picker). */
	function pieces(i: number, line: Line): { text: string; hidden: boolean }[] {
		const hides = recipe?.hide.filter((h) => h.line === i) ?? [];
		if (!hides.length) return [{ text: line.text, hidden: false }];
		const out: { text: string; hidden: boolean }[] = [];
		line.cells.forEach((c, k) => {
			const hidden = hides.some((h) => k >= h.start && k < h.end);
			const last = out[out.length - 1];
			if (last && last.hidden === hidden) last.text += c.ch;
			else out.push({ text: c.ch, hidden });
		});
		return out;
	}

	let pattern = $state('');
	let patternError = $derived(pattern.trim() && !compile(pattern) ? "That isn't a regular expression" : null);
	const PRESETS = [
		{ label: 'IP addresses', re: '\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b' },
		{ label: 'Email addresses', re: '[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+' },
		{ label: 'Hashes', re: '\\b[0-9a-fA-F]{32,}\\b' }
	];
	function addPattern(re = pattern) {
		if (!recipe || !compile(re) || recipe.patterns.includes(re)) return;
		recipe.patterns = [...recipe.patterns, re];
		if (re === pattern) pattern = '';
	}

	// ---- out ----
	let busy = $state<string | null>(null);
	async function png(): Promise<Blob> {
		if (!recipe || !job) throw new Error('Nothing to draw');
		await fontsReady(recipe.font_size);
		return toPng(buildScene(lines, recipe, job), recipe.scale);
	}
	async function copy() {
		busy = 'copy';
		try {
			await navigator.clipboard.write([new ClipboardItem({ 'image/png': png() })]);
			snackbar.show('Picture copied');
		} catch {
			snackbar.error("This browser didn't allow copying a picture; download it instead");
		} finally {
			busy = null;
		}
	}
	function save(blob: Blob, name: string) {
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = name;
		a.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
	async function download(kind: 'png' | 'svg') {
		if (!recipe || !job) return;
		busy = kind;
		try {
			const blob = kind === 'png' ? await png() : new Blob([toSvg(buildScene(lines, recipe, job))], { type: 'image/svg+xml' });
			save(blob, fileName(title, kind));
		} finally {
			busy = null;
		}
	}
	async function attach(asNew: boolean) {
		if (!recipe || !job) return;
		busy = asNew ? 'attach' : 'update';
		try {
			const file = new File([await png()], fileName(title, 'png'), { type: 'image/png' });
			const text = JSON.stringify(recipe);
			const saved = !asNew && attachment ? await api().attachments.replaceSnapshot(attachment.id, file, text) : await api().tasks.upload(taskId, file, 'screenshot', { snapshot: text, job_id: job.id });
			snackbar.show(asNew ? 'Snapshot attached to the card' : 'Snapshot saved');
			onsaved?.(saved);
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not attach it');
		} finally {
			busy = null;
		}
	}

	const FRAMES = [
		{ value: 'window', label: 'Window' },
		{ value: 'bar', label: 'Title bar' },
		{ value: 'none', label: 'No frame' }
	];
	const BACKGROUNDS = [
		{ value: 'gradient', label: 'Gradient' },
		{ value: 'solid', label: 'Solid' },
		{ value: 'none', label: 'None (transparent)' }
	];
	const SIZES = [12, 13, 14, 16, 18].map((n) => ({ value: String(n), label: `${n} px` }));
	const PADDINGS = [16, 32, 48, 72].map((n) => ({ value: String(n), label: `${n} px` }));
	const SCALES = [1, 2, 3].map((n) => ({ value: String(n), label: `${n}×${n === 2 ? ' (sharp)' : n === 3 ? ' (print)' : ''}` }));
	let shownCount = $derived(recipe ? lines.slice(recipe.from, recipe.to).filter((l) => recipe!.system || !l.system).length : 0);
</script>

<Dialog bind:open title={attachment ? 'Change the snapshot' : 'Snapshot'} icon="photo_camera" size="lg" initialFocus="dialog">
	{#if loading}
		<p class="flex items-center gap-2 py-10 body-medium"><Icon name="progress_activity" class="animate-spin" />Reading the output…</p>
	{:else if error || !recipe}
		<p class="py-10 body-medium text-error">{error ?? 'Nothing to show'}</p>
	{:else}
		<div class="flex flex-col gap-4 pb-2 lg:h-full lg:min-h-0 lg:flex-row">
			<div class="flex min-w-0 flex-1 flex-col gap-3 lg:min-h-0">
				<!-- The picture, as it will be. -->
				<div class="flex max-h-80 min-h-40 shrink-0 lg:max-h-[45%] items-start justify-center overflow-auto rounded-md bg-surface-container-lowest p-3 [background-image:repeating-conic-gradient(var(--md-sys-color-surface-container)_0_25%,transparent_0_50%)] [background-size:16px_16px]">
					<div bind:this={previewHost} class="max-w-full" aria-label="Preview, {drawn.width} by {drawn.height} pixels before scaling" role="img"></div>
				</div>

				<div class="flex flex-wrap items-center gap-x-3 gap-y-1 body-small text-on-surface-variant">
					<span class="text-on-surface">{shownCount} line{shownCount === 1 ? '' : 's'}</span>
					<span>Click a number to start there, Shift-click to end there, Ctrl-click to highlight it. Select text to hide it.</span>
					<span class="flex-1"></span>
					<Button variant="text" class="h-8" onclick={everything}>All of it</Button>
					<Button variant="tonal" icon="visibility_off" class="h-8" disabled={!selection.length} onclick={hideSelection}>Hide selection</Button>
				</div>

				<!-- The output, to pick lines and select text in. -->
				<div
					bind:this={picker}
					class="h-72 shrink-0 overflow-auto rounded-md border lg:h-auto lg:min-h-32 lg:flex-1 lg:shrink border-outline-variant bg-surface-container-lowest py-1 font-mono text-[12px] leading-[1.6] text-on-surface"
					onmouseup={readSelection}
					onkeyup={readSelection}
					role="listbox"
					aria-label="Output lines"
					tabindex="-1"
				>
					{#each lines as line, i (i)}
						<div data-line={i} class="flex [content-visibility:auto] [contain-intrinsic-size:auto_19px] {inRange(i) ? (recipe.highlight.includes(i) ? 'bg-primary/16' : 'bg-secondary-container/50') : 'opacity-55'} {line.system ? 'italic text-on-surface-variant' : ''}">
							<button
								type="button"
								class="w-14 shrink-0 cursor-pointer pr-3 text-right text-on-surface-variant select-none hover:text-primary {recipe.highlight.includes(i) ? 'font-bold text-primary' : ''}"
								aria-label="Line {i + 1}"
								onclick={(e) => pickLine(i, e)}>{i + 1}</button
							>
							<span data-text class="min-w-0 flex-1 pr-3 break-all whitespace-pre-wrap"
								>{#each pieces(i, line) as piece, k (k)}{#if piece.hidden}<span class="rounded-xs bg-on-surface/70 text-transparent">{piece.text}</span>{:else}{piece.text}{/if}{/each}{#if !line.text}&nbsp;{/if}</span
							>
						</div>
					{/each}
				</div>
			</div>

			<!-- How it looks. -->
			<aside class="flex shrink-0 flex-col gap-3 lg:w-72 lg:overflow-auto" aria-label="Look">
				<TextField label="Title" bind:value={recipe.title} />
				<Select
					label="Colours"
					value={recipe.theme}
					options={THEMES}
					onchange={(v) => {
						if (!recipe) return;
						recipe.theme = v as SnapshotRecipe['theme'];
						recipe.palette = v === 'theme' ? themePalette() : undefined;
					}}
				/>
				<div class="grid grid-cols-2 gap-3">
					<Select label="Frame" value={recipe.frame} options={FRAMES} onchange={(v) => recipe && (recipe.frame = v as SnapshotRecipe['frame'])} />
					<Select label="Background" value={recipe.background} options={BACKGROUNDS} onchange={(v) => recipe && (recipe.background = v as SnapshotRecipe['background'])} />
					<Select label="Text size" value={String(recipe.font_size)} options={SIZES} onchange={(v) => recipe && (recipe.font_size = Number(v))} />
					<Select label="Padding" value={String(recipe.padding)} options={PADDINGS} disabled={recipe.background === 'none'} onchange={(v) => recipe && (recipe.padding = Number(v))} />
					<TextField label="Wrap at" type="number" value={recipe.wrap} trailingLabel="columns" oninput={(e) => recipe && (recipe.wrap = Math.max(20, Math.min(400, Number((e.currentTarget as HTMLInputElement).value) || 100)))} />
					<Select label="Picture size" value={String(recipe.scale)} options={SCALES} onchange={(v) => recipe && (recipe.scale = Number(v))} />
				</div>
				<div class="flex flex-col gap-2 py-1">
					<Switch label="The command first" bind:checked={recipe.command} />
					<Switch label="Line numbers" bind:checked={recipe.line_numbers} />
					<Switch label="Castor's own lines" bind:checked={recipe.system} />
				</div>

				<section class="flex flex-col gap-2 border-t border-outline-variant pt-3" aria-label="Hidden text">
					<p class="label-large text-on-surface">Hide everywhere</p>
					<div class="flex flex-wrap gap-1.5">
						{#each PRESETS.filter((p) => !recipe?.patterns.includes(p.re)) as p (p.re)}
							<button type="button" class="state-layer rounded-full border border-outline-variant px-2.5 py-0.5 label-medium text-on-surface-variant" onclick={() => addPattern(p.re)}>+ {p.label}</button>
						{/each}
					</div>
					<TextField
						label="Or a regular expression"
						mono
						bind:value={pattern}
						error={patternError}
						trailingIcon="add"
						trailingLabel="Hide its matches"
						ontrailing={() => addPattern()}
						onkeydown={(e) => {
							if (e.key === 'Enter') {
								e.preventDefault();
								addPattern();
							}
						}}
					/>
					{#if recipe.patterns.length || recipe.hide.length}
						<ul class="flex flex-col gap-1 body-small">
							{#each recipe.patterns as re (re)}
								<li class="flex items-center gap-2 rounded-sm bg-surface-container px-2 py-0.5">
									<code class="min-w-0 flex-1 truncate font-mono" title={re}>{PRESETS.find((p) => p.re === re)?.label ?? re}</code>
									<IconButton icon="close" size="sm" label="Show these again" onclick={() => recipe && (recipe.patterns = recipe.patterns.filter((x) => x !== re))} />
								</li>
							{/each}
							{#if recipe.hide.length}
								<li class="flex items-center gap-2 rounded-sm bg-surface-container px-2 py-0.5">
									<span class="flex-1">{recipe.hide.length} selection{recipe.hide.length === 1 ? '' : 's'} hidden</span>
									<IconButton icon="close" size="sm" label="Show them again" onclick={() => recipe && (recipe.hide = [])} />
								</li>
							{/if}
						</ul>
					{/if}
					<p class="body-small text-on-surface-variant">These hide on every line, the command's too. Hidden text isn't in the picture at all, only a bar where it was.</p>
				</section>
			</aside>
		</div>
	{/if}

	{#snippet actions()}
		<Button variant="text" icon="content_copy" loading={busy === 'copy'} disabled={!recipe || !!busy} onclick={copy}>Copy</Button>
		<Button variant="text" icon="download" loading={busy === 'png'} disabled={!recipe || !!busy} onclick={() => download('png')}>PNG</Button>
		<Button variant="text" icon="download" loading={busy === 'svg'} disabled={!recipe || !!busy} onclick={() => download('svg')}>SVG</Button>
		{#if canAttach}
			{#if attachment?.snapshot}
				<Button variant="outlined" loading={busy === 'attach'} disabled={!recipe || !!busy} onclick={() => attach(true)}>Attach as new</Button>
				<Button icon="save" loading={busy === 'update'} disabled={!recipe || !!busy} onclick={() => attach(false)}>Save</Button>
			{:else}
				<Button icon="attach_file" loading={busy === 'attach'} disabled={!recipe || !!busy} onclick={() => attach(true)}>Attach to card</Button>
			{/if}
		{/if}
	{/snippet}
</Dialog>
