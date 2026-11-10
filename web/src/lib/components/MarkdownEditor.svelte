<script lang="ts">
	import { tick, untrack, type Snippet } from 'svelte';
	import { Button, Icon } from '$lib/m3';
	import { applyEdit, markdownKeydown } from '$lib/m3/markdownKeys';
	import { codeBlock, continueList, inlineCode, link, mentionStart, toggleLines, wrapInline, type Edit, type LineKind } from '$lib/markdownEdit';
	import { MentionSuggest } from '$lib/mentionSuggest.svelte';
	import type { PastedImages } from '$lib/pastedImages.svelte';
	import type { Mentionable } from '$lib/people.svelte';
	import MentionMenu from './MentionMenu.svelte';
	import RichText from './RichText.svelte';

	/**
	 * Writing markdown (comments, timeline posts): Write and Preview, a formatting toolbar with
	 * the usual shortcuts, @mentions, images pasted, dropped or picked, lists that continue on
	 * Enter, and a box that grows with the text. The parent saves: `onsubmit` gets the text and
	 * throws to keep it.
	 */
	interface Props {
		value: string;
		/** The box's accessible name (and the placeholder when none is given). */
		label: string;
		placeholder?: string;
		/** Whose @mentions and #hashtags the preview shows (the project). */
		projectId: string;
		mentionables: () => Mentionable[];
		/** Where images go; the parent shows its naming prompt (NameImageDialog). */
		images: PastedImages;
		submitLabel?: string;
		submitIcon?: string;
		onsubmit: (text: string) => Promise<void> | void;
		oncancel?: () => void;
		busy?: boolean;
		/** Collapse to one quiet line while empty and not in use (the timeline). */
		collapsible?: boolean;
		autofocus?: boolean;
		minRows?: number;
		/** Link #hashtags in the preview (timeline posts). */
		hashtags?: (tag: string) => string;
		/** Keep an unsent draft in this browser under this key (new comments and posts). */
		draftKey?: string;
		/** Shown before the box while it's collapsed (the writer's avatar). */
		leading?: Snippet;
	}
	let {
		value = $bindable(''),
		label,
		placeholder,
		projectId,
		mentionables,
		images,
		submitLabel = 'Post',
		submitIcon = 'send',
		onsubmit,
		oncancel,
		busy = false,
		collapsible = false,
		autofocus = false,
		minRows = 3,
		hashtags,
		draftKey,
		leading
	}: Props = $props();

	const uid = `md-${Math.random().toString(36).slice(2, 8)}`;
	const mac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
	const mod = mac ? '⌘' : 'Ctrl';

	let textarea = $state<HTMLTextAreaElement>();
	let fileInput = $state<HTMLInputElement>();
	let mode = $state<'write' | 'preview'>('write');
	let focused = $state(false);
	let dragging = $state(0);
	let showHelp = $state(false);
	let height = $state(0); // the box's last height, so Preview doesn't jump

	const target = { get: () => value, set: (v: string) => (value = v) };
	const suggest = new MentionSuggest(() => mentionables(), target);

	let expanded = $derived(!collapsible || focused || !!value || mode === 'preview' || images.pending.length > 0 || dragging > 0);
	let reference = $derived([
		['**bold**', `${mod}+B`],
		['_italic_', `${mod}+I`],
		['`code`', `${mod}+E, or select text and type \``],
		['[text](url)', 'The link button'],
		['- item', `${mod}+Shift+8; Enter continues a list`],
		['1. item', `${mod}+Shift+7`],
		['- [ ] task', 'A checklist'],
		['> quote', `${mod}+Shift+.`],
		['@name', 'Mentions notify people or a group'],
		...(hashtags ? [['#tag', 'Tags the post; click one to filter']] : []),
		['Images', 'Paste, drop or pick them']
	]);
	let canSubmit = $derived(!!value.trim() && !busy && images.uploading === 0);

	// ---- drafts kept in this browser ----
	let restored = $state(false);
	$effect(() => {
		const key = draftKey;
		if (!key) return;
		untrack(() => {
			try {
				const saved = localStorage.getItem(`castor.draft.${key}`);
				if (saved && !value) {
					value = saved;
					restored = true;
				}
			} catch {
				/* storage unavailable */
			}
		});
	});
	let draftTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		if (!draftKey) return;
		const text = value;
		clearTimeout(draftTimer);
		draftTimer = setTimeout(() => {
			try {
				if (text.trim()) localStorage.setItem(`castor.draft.${draftKey}`, text);
				else localStorage.removeItem(`castor.draft.${draftKey}`);
			} catch {
				/* storage unavailable */
			}
		}, 400);
	});
	function forgetDraft() {
		clearTimeout(draftTimer);
		restored = false;
		if (!draftKey) return;
		try {
			localStorage.removeItem(`castor.draft.${draftKey}`);
		} catch {
			/* storage unavailable */
		}
	}

	// ---- the box grows with its text (up to about half the screen, then scrolls) ----
	function fit() {
		const el = textarea;
		if (!el) return;
		el.style.height = 'auto';
		const max = Math.max(200, Math.round(window.innerHeight * 0.5));
		el.style.height = `${Math.min(el.scrollHeight + 2, max)}px`;
		el.style.overflowY = el.scrollHeight + 2 > max ? 'auto' : 'hidden';
		height = el.offsetHeight;
	}
	$effect(() => {
		void value;
		void expanded;
		if (mode === 'write') void tick().then(fit);
	});
	// Focused with the caret at the end, ready to carry on writing.
	let focusedOnce = false;
	$effect(() => {
		if (!autofocus || !textarea || focusedOnce) return;
		focusedOnce = true;
		void tick().then(() => {
			textarea?.focus();
			textarea?.setSelectionRange(value.length, value.length);
		});
	});

	// ---- formatting ----
	function run(make: (text: string, start: number, end: number) => Edit | null) {
		const el = textarea;
		if (!el || mode !== 'write') return;
		const edit = make(el.value, el.selectionStart ?? el.value.length, el.selectionEnd ?? el.value.length);
		if (edit) applyEdit(el, edit);
	}
	const lines = (kind: LineKind) => run((t, s, e) => toggleLines(t, s, e, kind));
	// `phone`: shown on narrow screens too (the rest stay a keyboard shortcut or a wider screen away).
	const TOOLS: ({ icon: string; label: string; keys?: string; phone?: boolean; act: () => void } | null)[] = [
		{ icon: 'format_bold', label: 'Bold', phone: true, keys: `${mod}+B`, act: () => run((t, s, e) => wrapInline(t, s, e, '**', 'bold text')) },
		{ icon: 'format_italic', label: 'Italic', phone: true, keys: `${mod}+I`, act: () => run((t, s, e) => wrapInline(t, s, e, '_', 'italic text')) },
		{ icon: 'strikethrough_s', label: 'Strikethrough', act: () => run((t, s, e) => wrapInline(t, s, e, '~~', 'struck text')) },
		null,
		{ icon: 'code', label: 'Code', phone: true, keys: `${mod}+E`, act: () => run(inlineCode) },
		{ icon: 'data_object', label: 'Code block', act: () => run(codeBlock) },
		{ icon: 'link', label: 'Link', phone: true, act: () => run(link) },
		null,
		{ icon: 'format_quote', label: 'Quote', keys: `${mod}+Shift+.`, act: () => lines('quote') },
		{ icon: 'format_list_bulleted', label: 'Bulleted list', phone: true, keys: `${mod}+Shift+8`, act: () => lines('bullet') },
		{ icon: 'format_list_numbered', label: 'Numbered list', keys: `${mod}+Shift+7`, act: () => lines('number') },
		{ icon: 'checklist', label: 'Checklist', act: () => lines('task') },
		null,
		{ icon: 'alternate_email', label: 'Mention someone', phone: true, act: () => run(mentionStart) },
		{ icon: 'add_photo_alternate', label: 'Add images', phone: true, act: () => fileInput?.click() }
	];

	async function submit() {
		if (!canSubmit) return;
		try {
			await onsubmit(value);
		} catch {
			return; // the parent said why; keep the text
		}
		forgetDraft();
		mode = 'write';
	}

	function keydown(e: KeyboardEvent & { currentTarget: HTMLTextAreaElement }) {
		if (suggest.onkeydown(e)) return;
		const ctrl = e.ctrlKey || e.metaKey;
		const key = e.key.toLowerCase();
		if (ctrl && e.key === 'Enter') {
			e.preventDefault();
			void submit();
		} else if (e.key === 'Escape' && oncancel) {
			e.preventDefault();
			e.stopPropagation(); // cancel this, not the dialog around it
			oncancel();
		} else if (ctrl && !e.shiftKey && !e.altKey && ['b', 'i', 'e'].includes(key)) {
			e.preventDefault();
			TOOLS.find((t) => t?.keys === `${mod}+${key.toUpperCase()}`)?.act();
		} else if (ctrl && e.shiftKey && (e.code === 'Digit8' || e.code === 'Digit7' || e.code === 'Period')) {
			e.preventDefault();
			lines(e.code === 'Digit8' ? 'bullet' : e.code === 'Digit7' ? 'number' : 'quote');
		} else if (e.key === 'Enter' && !e.shiftKey && !ctrl && !e.altKey && !e.isComposing) {
			const el = e.currentTarget;
			const edit = continueList(el.value, el.selectionStart, el.selectionEnd);
			if (edit) {
				e.preventDefault();
				applyEdit(el, edit);
			}
		} else {
			markdownKeydown(e);
		}
	}

	async function setMode(next: 'write' | 'preview') {
		if (next === mode) return;
		if (next === 'preview' && textarea) height = textarea.offsetHeight;
		mode = next;
		suggest.close();
		if (next === 'write') {
			await tick();
			textarea?.focus();
		}
	}

	// ---- images dropped on the box or picked ----
	async function addFiles(files: FileList | null | undefined) {
		if (!files?.length) return;
		const list = Array.from(files);
		if (mode !== 'write') await setMode('write');
		if (textarea) images.add(list, textarea, target);
	}
	const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files');

	function focusout(e: FocusEvent) {
		const next = e.relatedTarget as Node | null;
		if (!next || !(e.currentTarget as HTMLElement).contains(next)) {
			focused = false;
			showHelp = false;
		}
	}
</script>

<!-- The group is the drop zone for images and takes the Write/Preview shortcut for everything in it. -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
	class="relative flex flex-col rounded-md border transition-[border-color,box-shadow] duration-150
		{dragging > 0 ? 'border-primary shadow-[inset_0_0_0_1px_var(--md-sys-color-primary)]' : focused ? 'border-primary shadow-[inset_0_0_0_1px_var(--md-sys-color-primary)]' : expanded ? 'border-outline-variant hover:border-outline' : 'border-outline hover:border-on-surface'}"
	onfocusin={() => (focused = true)}
	onfocusout={focusout}
	onkeydown={(e) => {
		if (e.key === 'Escape' && showHelp) {
			e.preventDefault();
			e.stopPropagation(); // close the reference, not the dialog around it
			showHelp = false;
			return;
		}
		// Write ⇄ Preview from anywhere in the editor.
		if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'p') {
			e.preventDefault();
			void setMode(mode === 'write' ? 'preview' : 'write');
		}
	}}
	ondragenter={(e) => hasFiles(e) && (e.preventDefault(), (dragging += 1))}
	ondragover={(e) => hasFiles(e) && e.preventDefault()}
	ondragleave={() => (dragging = Math.max(0, dragging - 1))}
	ondrop={(e) => {
		if (!hasFiles(e)) return;
		e.preventDefault();
		dragging = 0;
		void addFiles(e.dataTransfer?.files);
	}}
	role="group"
	aria-label={label}
>
	{#if expanded}
		<!-- Write / Preview, and the formatting tools. Buttons keep the caret where it is. -->
		<div class="flex min-w-0 flex-wrap items-center gap-1 border-b border-outline-variant/70 px-1.5 py-1">
			<div class="flex shrink-0 items-center gap-0.5" role="tablist" aria-label="Mode">
				{#each [['write', 'Write'], ['preview', 'Preview']] as [m, text] (m)}
					<button
						type="button"
						role="tab"
						id="{uid}-{m}"
						aria-selected={mode === m}
						aria-controls="{uid}-panel"
						class="state-layer focus-ring h-8 rounded-full px-3 label-large transition-colors {mode === m ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface-variant'}"
						onmousedown={(e) => e.preventDefault()}
						onclick={() => setMode(m as 'write' | 'preview')}>{text}</button
					>
				{/each}
			</div>
			<div
				class="ml-auto flex min-w-0 items-center max-sm:order-last max-sm:ml-0 max-sm:basis-full max-sm:justify-between {mode === 'preview' ? 'pointer-events-none opacity-0 max-sm:hidden' : ''}"
				role="toolbar"
				aria-label="Formatting"
				aria-hidden={mode === 'preview'}
			>
				{#each TOOLS as tool, i (i)}
					{#if tool}
						<button
							type="button"
							class="state-layer focus-ring size-8 shrink-0 items-center justify-center rounded-full text-on-surface-variant hover:text-on-surface {tool.phone ? 'flex' : 'hidden sm:flex'}"
							aria-label={tool.label}
							title={tool.keys ? `${tool.label} (${tool.keys})` : tool.label}
							tabindex={mode === 'preview' ? -1 : 0}
							onmousedown={(e) => e.preventDefault()}
							onclick={tool.act}><Icon name={tool.icon} size={20} /></button
						>
					{:else}
						<span class="mx-1 h-4 w-px shrink-0 bg-outline-variant max-sm:hidden" aria-hidden="true"></span>
					{/if}
				{/each}
			</div>
		</div>
	{/if}

	<div id="{uid}-panel" role="tabpanel" aria-labelledby="{uid}-{mode}" class="relative">
		{#if mode === 'write'}
			<!-- Collapsed, the box reads as an invitation: who's writing, the prompt, and an image
			     button. The textarea itself stays the same element, so clicking it just opens up. -->
			<div class="flex items-center">
			{#if !expanded && leading}<div class="shrink-0 pl-3" aria-hidden="true">{@render leading()}</div>{/if}
			<textarea
				bind:this={textarea}
				bind:value
				rows={expanded ? minRows : 1}
				aria-label={label}
				placeholder={placeholder ?? label}
				class="block w-full min-w-0 flex-1 resize-none bg-transparent px-3.5 body-medium leading-[1.6] text-on-surface caret-primary outline-none placeholder:text-on-surface-variant {expanded ? 'py-2.5' : 'cursor-text py-3.5 body-large'}"
				oninput={(e) => {
					restored = false;
					suggest.oninput(e);
				}}
				onkeydown={keydown}
				onblur={suggest.close}
				onpaste={images.pasteInto(target)}
			></textarea>
			{#if !expanded}
				<button
					type="button"
					class="state-layer focus-ring mr-2 flex size-10 shrink-0 items-center justify-center rounded-full text-on-surface-variant hover:text-on-surface"
					aria-label="Add images"
					title="Add images"
					onclick={() => fileInput?.click()}><Icon name="add_photo_alternate" size={22} /></button
				>
			{/if}
			</div>
			<MentionMenu {suggest} />
		{:else}
			<div class="overflow-y-auto px-3.5 py-2.5" style="min-height: {height}px; max-height: 50vh">
				{#if value.trim()}
					<RichText text={value} {projectId} {hashtags} class="body-medium text-on-surface" />
				{:else}
					<p class="body-medium text-on-surface-variant italic">Nothing to preview yet.</p>
				{/if}
			</div>
		{/if}
		{#if dragging > 0}
			<div class="pointer-events-none absolute inset-0 flex items-center justify-center gap-2 rounded-b-md bg-primary-container/85 label-large text-on-primary-container">
				<Icon name="add_photo_alternate" size={20} />Drop images to add them
			</div>
		{/if}
	</div>

	{#if expanded}
		<div class="flex flex-wrap items-center gap-x-2 gap-y-1 px-2 pt-1 pb-2">
			<div class="relative">
				<button
					type="button"
					class="state-layer focus-ring flex h-8 items-center gap-1.5 rounded-full px-2 body-small text-on-surface-variant"
					aria-expanded={showHelp}
					aria-controls="{uid}-help"
					onmousedown={(e) => e.preventDefault()}
					onclick={() => (showHelp = !showHelp)}
				>
					<Icon name="help" size={16} />Markdown, @mentions and images
				</button>
				{#if showHelp}
					<div id="{uid}-help" class="absolute bottom-full left-0 z-40 mb-1 w-[22rem] max-w-[calc(100vw-3rem)] rounded-md bg-surface-container p-3 shadow-e2" role="note">
						<p class="mb-2 title-small text-on-surface">Formatting</p>
						<dl class="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 body-small">
							{#each reference as [syntax, how] (syntax)}
								<dt class="font-mono text-[12px] leading-4 whitespace-nowrap text-on-surface">{syntax}</dt>
								<dd class="text-on-surface-variant">{how}</dd>
							{/each}
						</dl>
						<p class="mt-2 border-t border-outline-variant pt-2 body-small text-on-surface-variant">
							{mod}+Enter {submitLabel.toLowerCase()}s{oncancel ? ' · Esc cancels' : ''} · {mod}+Shift+P previews
						</p>
					</div>
				{/if}
			</div>
			{#if images.uploading > 0}
				<span class="flex items-center gap-1.5 body-small text-on-surface-variant" role="status"><Icon name="progress_activity" size={16} class="animate-spin" />Uploading image…</span>
			{:else if restored}
				<span class="flex items-center gap-1.5 body-small text-on-surface-variant" role="status">
					<Icon name="history" size={16} />Draft restored
					<button
						type="button"
						class="text-primary hover:underline focus-ring rounded-xs"
						onclick={() => {
							value = '';
							forgetDraft();
							textarea?.focus();
						}}>Discard</button
					>
				</span>
			{/if}
			<span class="ml-auto flex items-center gap-2">
				{#if oncancel}<Button variant="text" onclick={oncancel}>Cancel</Button>{/if}
				<Button icon={submitIcon} loading={busy} disabled={!canSubmit} onclick={submit} title="{submitLabel} ({mod}+Enter)">{submitLabel}</Button>
			</span>
		</div>
	{/if}

	<input bind:this={fileInput} type="file" accept="image/*" multiple class="hidden" onchange={(e) => {
		void addFiles(e.currentTarget.files).then(() => (e.currentTarget.value = ''));
	}} />
</div>
