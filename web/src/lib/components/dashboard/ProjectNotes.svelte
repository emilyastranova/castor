<script lang="ts">
	import PersonName from '$lib/components/PersonName.svelte';
	import { tick } from 'svelte';
	import type { NoteVersion, ProjectNote, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Avatar, Button, Dialog, Icon, IconButton, Menu, MenuItem, TextField } from '$lib/m3';
	import { renderMarkdown } from '$lib/markdown';
	import { openImagesIn } from '$lib/lightbox.svelte';
	import { realtime } from '$lib/realtime.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { canEdit } from '$lib/session';
	import { PastedImages } from '$lib/pastedImages.svelte';
	import NameImageDialog from '../NameImageDialog.svelte';
	import NoteColorPicker, { noteBg } from './NoteColorPicker.svelte';
	import { pop, glide } from '$lib/motion';
	import { appSettings } from '$lib/appSettings.svelte';

	/** Pinned notes at the top of a project's dashboard: status, objectives, scope, contacts… */
	let { projectId }: { projectId: UUID } = $props();
	// The board holds a set number of notes (Settings → General); at the limit, no add tile.
	$effect(() => void appSettings.load());

	let notes = $state<ProjectNote[]>([]);
	let loaded = $state(false);
	const SUGGESTED = ['Status', 'Objectives', 'Scope', 'Contacts'];

	async function load() {
		try {
			notes = await api().notes.list(projectId);
		} finally {
			loaded = true;
		}
	}
	$effect(() => {
		void projectId;
		void load();
	});
	let reloadTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name !== 'project.notes' || e.project_id !== projectId) return;
			clearTimeout(reloadTimer);
			reloadTimer = setTimeout(load, 150);
		})
	);

	// ---- editing: one note (or a new one, id 'new') at a time ----
	let editingId = $state<UUID | 'new' | null>(null);
	let draftTitle = $state('');
	let draftBody = $state('');
	let draftColor = $state('default');
	let saving = $state(false);
	let error = $state<string | null>(null);
	let editorEl = $state<HTMLDivElement>();
	// Pasted images are stored on the project and embedded in the note's markdown.
	const images = new PastedImages(async (file) => api().attachments.url((await api().notes.uploadImage(projectId, file)).id, true));
	const bodyTarget = { get: () => draftBody, set: (v: string) => (draftBody = v) };

	async function startEdit(note: ProjectNote | null, title = '') {
		editingId = note ? note.id : 'new';
		draftTitle = note ? note.title : title;
		draftBody = note ? note.body : '';
		draftColor = note ? note.color : 'default';
		error = null;
		// The title when it's still to be chosen, else the text.
		await tick();
		const field = editorEl?.querySelector<HTMLElement>(draftTitle ? 'textarea' : 'input');
		field?.focus();
		field?.scrollIntoView({ block: 'nearest' });
	}
	function cancelEdit() {
		editingId = null;
	}
	async function save() {
		if (images.uploading > 0) return;
		if (!draftTitle.trim()) {
			error = 'Give the note a title';
			return;
		}
		saving = true;
		error = null;
		try {
			const saved =
				editingId === 'new'
					? await api().notes.create(projectId, { title: draftTitle.trim(), body: draftBody, color: draftColor })
					: await api().notes.update(editingId!, { title: draftTitle.trim(), body: draftBody, color: draftColor });
			notes = [...notes.filter((n) => n.id !== saved.id), saved].sort((a, b) => a.position - b.position);
			editingId = null;
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save the note';
		} finally {
			saving = false;
		}
	}
	function editorKeys(e: KeyboardEvent) {
		if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
			e.preventDefault();
			void save();
		} else if (e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation();
			cancelEdit();
		}
	}

	async function remove(note: ProjectNote) {
		const ok = await confirmAction({
			title: `Remove “${note.title}”?`,
			message: 'The note and its earlier versions are removed from the project.',
			confirmLabel: 'Remove note'
		});
		if (!ok) return;
		try {
			await api().notes.remove(note.id);
			notes = notes.filter((n) => n.id !== note.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove the note');
		}
	}

	async function recolor(note: ProjectNote, color: string) {
		if (note.color === color) return;
		const before = note.color;
		notes = notes.map((n) => (n.id === note.id ? { ...n, color } : n));
		try {
			await api().notes.update(note.id, { color });
		} catch (err) {
			notes = notes.map((n) => (n.id === note.id ? { ...n, color: before } : n));
			snackbar.error(err instanceof ApiError ? err.message : 'Could not change the color');
		}
	}

	async function move(note: ProjectNote, by: -1 | 1) {
		const ids = notes.map((n) => n.id);
		const i = ids.indexOf(note.id);
		const j = i + by;
		if (j < 0 || j >= ids.length) return;
		[ids[i], ids[j]] = [ids[j], ids[i]];
		const before = notes;
		notes = ids.map((id) => notes.find((n) => n.id === id)!);
		try {
			notes = await api().notes.order(projectId, ids);
		} catch (err) {
			notes = before;
			snackbar.error(err instanceof ApiError ? err.message : 'Could not reorder');
		}
	}

	// ---- fixed height: every note is the same size so content (or a pasted image arriving
	// over realtime) never shifts the dashboard; longer notes scroll, or expand for this viewer ----
	let expanded = $state<Record<string, boolean>>({});
	let overflow = $state<Record<string, { more: boolean; atEnd: boolean }>>({});
	/** Watches a note's scroll box: does its content overflow, and is it scrolled to the end? */
	function measure(node: HTMLElement, id: string) {
		const check = () => {
			const more = node.scrollHeight > node.clientHeight + 1;
			const atEnd = node.scrollTop + node.clientHeight >= node.scrollHeight - 2;
			const was = overflow[id];
			if (!was || was.more !== more || was.atEnd !== atEnd) overflow[id] = { more, atEnd };
		};
		// The box's own size and its content's (images load later, notes change over realtime).
		const ro = new ResizeObserver(check);
		ro.observe(node);
		if (node.firstElementChild) ro.observe(node.firstElementChild);
		node.addEventListener('scroll', check, { passive: true });
		check();
		return {
			destroy() {
				ro.disconnect();
				node.removeEventListener('scroll', check);
			}
		};
	}

	// ---- history ----
	let historyNote = $state<ProjectNote | null>(null);
	let historyOpen = $state(false);
	let versions = $state<NoteVersion[]>([]);
	let picked = $state(0);
	let restoring = $state(false);
	async function openHistory(note: ProjectNote) {
		historyNote = note;
		versions = [];
		picked = 0;
		historyOpen = true;
		try {
			versions = await api().notes.history(note.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load the history');
		}
	}
	async function restore() {
		if (!historyNote || !versions[picked]) return;
		restoring = true;
		try {
			const saved = await api().notes.restore(historyNote.id, versions[picked].index);
			notes = notes.map((n) => (n.id === saved.id ? saved : n));
			historyOpen = false;
			snackbar.show(`Restored “${saved.title}”`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not restore');
		} finally {
			restoring = false;
		}
	}
</script>

{#snippet editor()}
	<div bind:this={editorEl} class="flex min-h-60 flex-col gap-3 rounded-lg border border-primary p-4" style:background-color={noteBg(draftColor)}>
		<TextField label="Title" bind:value={draftTitle} onkeydown={editorKeys} />
		<TextField label="Note (markdown)" bind:value={draftBody} multiline markdown rows={7} onkeydown={editorKeys} onpaste={images.pasteInto(bodyTarget)} {error} supporting="Markdown · paste an image to add it · Ctrl+Enter to save · Esc to cancel" />
		<NoteColorPicker value={draftColor} onpick={(c) => (draftColor = c)} size={24} />
		<div class="flex items-center justify-end gap-2">
			{#if images.uploading > 0}<span class="mr-auto body-small text-on-surface-variant">Uploading image…</span>{/if}
			<Button variant="text" onclick={cancelEdit}>Cancel</Button>
			<Button icon="check" loading={saving} disabled={images.uploading > 0} onclick={save}>{editingId === 'new' ? 'Pin note' : 'Save'}</Button>
		</div>
	</div>
{/snippet}

{#if loaded && (notes.length || canEdit())}
	<section aria-label="Pinned notes" class="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] items-start gap-3">
		{#each notes as note, i (note.id)}
			<div class="min-w-0" in:pop out:pop animate:glide>
			{#if editingId === note.id}
				{@render editor()}
			{:else}
				<article class="group flex min-w-0 flex-col gap-2 rounded-lg bg-surface-container-low p-4 {expanded[note.id] ? '' : 'h-60'}" style:background-color={noteBg(note.color)}>
					<header class="flex items-start gap-2">
						<Icon name="push_pin" size={18} class="mt-0.5 shrink-0 text-tertiary" />
						<h2 class="min-w-0 flex-1 truncate title-medium text-on-surface" title={note.title}>{note.title}</h2>
						{#if canEdit()}
							<IconButton icon="edit" label="Edit note" size="sm" class="opacity-0 group-focus-within:opacity-100 group-hover:opacity-100" onclick={() => startEdit(note)} />
							<Menu class="opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 has-[[role=menu]]:opacity-100">
								{#snippet anchor()}<IconButton icon="palette" label="Note color" size="sm" />{/snippet}
								<div class="w-max px-3 py-1.5" role="presentation">
									<p class="mb-2 label-medium text-on-surface-variant">Color</p>
									<div class="w-[12.5rem]"><NoteColorPicker value={note.color} onpick={(c) => recolor(note, c)} /></div>
								</div>
							</Menu>
						{/if}
						<Menu>
							{#snippet anchor()}<IconButton icon="more_vert" label="Note menu" size="sm" />{/snippet}
							{#if canEdit()}<MenuItem icon="edit" onclick={() => startEdit(note)}>Edit</MenuItem>{/if}
							<MenuItem icon="history" onclick={() => openHistory(note)}>History{note.versions ? ` (${note.versions})` : ''}</MenuItem>
							{#if canEdit()}
								{#if i > 0}<MenuItem icon="arrow_back" onclick={() => move(note, -1)}>Move left</MenuItem>{/if}
								{#if i < notes.length - 1}<MenuItem icon="arrow_forward" onclick={() => move(note, 1)}>Move right</MenuItem>{/if}
								<MenuItem icon="delete" danger onclick={() => remove(note)}>Remove</MenuItem>
							{/if}
						</Menu>
					</header>
					{#if note.body.trim()}
						{@const fade = !expanded[note.id] && overflow[note.id]?.more && !overflow[note.id]?.atEnd}
						<div use:measure={note.id} class="note-scroll min-h-0 flex-1 {expanded[note.id] ? '' : 'overflow-y-auto'}" class:faded={fade}>
							<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
							<div class="prose-m3 body-medium text-on-surface" onclick={openImagesIn}>{@html renderMarkdown(note.body)}</div>
						</div>
					{:else}
						<p class="flex-1 body-medium text-on-surface-variant italic">Empty</p>
					{/if}
					<footer class="flex shrink-0 items-center gap-1.5 pt-1 body-small text-on-surface-variant">
						{#if note.updated_by}<Avatar name={note.updated_by.name} src={note.updated_by.avatar_url} size={20} /><span class="truncate"><PersonName name={note.updated_by.name} /></span> ·{/if}
						<span class="shrink-0" title={absoluteTime(note.updated_at)}>{relativeTime(note.updated_at)}</span>
						{#if expanded[note.id] || overflow[note.id]?.more}
							<button
								type="button"
								class="state-layer ml-auto flex shrink-0 items-center gap-0.5 rounded-full py-0.5 pr-2 pl-1.5 label-medium text-primary"
								aria-expanded={!!expanded[note.id]}
								onclick={() => (expanded[note.id] = !expanded[note.id])}
							>
								<Icon name={expanded[note.id] ? 'unfold_less' : 'unfold_more'} size={16} />{expanded[note.id] ? 'Collapse' : 'Expand'}
							</button>
						{/if}
					</footer>
				</article>
			{/if}
			</div>
		{/each}

		{#if editingId === 'new'}
			{@render editor()}
		{:else if canEdit() && notes.length < appSettings.value.notes_per_project}
			<div class="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-outline-variant p-4 text-on-surface-variant {notes.length ? 'h-60' : 'min-h-24 col-span-full sm:flex-row sm:justify-start'}">
				<Button variant="text" icon="push_pin" onclick={() => startEdit(null)}>{notes.length ? 'Add note' : 'Pin a note'}</Button>
				{#if !notes.length}
					<span class="body-small">for the team: the project's status, objectives, scope or contacts.</span>
					<span class="flex flex-wrap gap-1 sm:ml-auto">
						{#each SUGGESTED as t (t)}
							<button type="button" class="state-layer rounded-sm border border-outline-variant px-2.5 py-1 label-medium" onclick={() => startEdit(null, t)}>{t}</button>
						{/each}
					</span>
				{/if}
			</div>
		{/if}
	</section>
{/if}

<Dialog bind:open={historyOpen} title={historyNote ? `History · ${historyNote.title}` : 'History'} icon="history" size="md">
	{#if !versions.length}
		<p class="py-4 body-medium text-on-surface-variant">No earlier versions: this note hasn't been edited.</p>
	{:else}
		<div class="grid min-h-0 gap-4 pt-2 pb-2 md:grid-cols-[200px_1fr]">
			<ul class="flex flex-col gap-1 overflow-y-auto md:max-h-[50vh]">
				{#each versions as v, i (v.index)}
					<li>
						<button type="button" class="state-layer w-full rounded-md px-3 py-2 text-left {picked === i ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface'}" onclick={() => (picked = i)}>
							<span class="block truncate label-large">{v.by_name}</span>
							<span class="block body-small text-on-surface-variant" title={absoluteTime(v.at)}>{relativeTime(v.at)}</span>
						</button>
					</li>
				{/each}
			</ul>
			{#if versions[picked]}
				<div class="min-w-0 rounded-md bg-surface-container p-4 md:max-h-[50vh] md:overflow-y-auto">
					<p class="mb-2 title-small text-on-surface">{versions[picked].title}</p>
					<div class="prose-m3 body-medium text-on-surface">{@html renderMarkdown(versions[picked].body || '_Empty_')}</div>
				</div>
			{/if}
		</div>
	{/if}
	{#snippet actions()}
		<Button variant="text" onclick={() => (historyOpen = false)}>Close</Button>
		{#if canEdit() && versions.length}<Button icon="restore" loading={restoring} onclick={restore}>Restore this version</Button>{/if}
	{/snippet}
</Dialog>

{#if images.pending[0]}
	{#key images.pending[0]}
		<NameImageDialog file={images.pending[0].file} remaining={images.pending.length - 1} onconfirm={(name) => images.confirm(name)} oncancel={() => images.cancel()} />
	{/key}
{/if}

<style>
	/* A longer note fades out at the bottom until it's scrolled to the end. */
	.note-scroll.faded {
		mask-image: linear-gradient(to bottom, #000 calc(100% - 2.5rem), transparent);
	}
	.note-scroll {
		scrollbar-width: thin;
		overscroll-behavior: contain;
	}
</style>
