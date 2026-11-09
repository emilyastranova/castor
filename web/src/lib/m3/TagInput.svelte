<script lang="ts">
	import Icon from './Icon.svelte';
	import type { TagSuggestion } from '$lib/tags';

	/** Tags as pills, typed in place. Enter, comma, space or Tab turns what you typed into a
	 *  tag (an existing one when it matches, whatever its case, so tags don't drift into
	 *  smb / SMB); suggestions of tags already in use show as you type, most used first.
	 *  Backspace in the empty box selects the last tag, and again removes it. Pasting
	 *  "a, b c" adds three. */
	interface Props {
		label: string;
		value: string[];
		suggestions?: (TagSuggestion | string)[];
		/** What a count means, e.g. "cards": "12 cards". */
		noun?: string;
		supporting?: string;
		placeholder?: string;
		disabled?: boolean;
		readonly?: boolean;
		/** Called after each change, with the new list. */
		onchange?: (tags: string[]) => void;
		class?: string;
	}
	let {
		label,
		value = $bindable([]),
		suggestions = [],
		noun = '',
		supporting,
		placeholder = '',
		disabled = false,
		readonly = false,
		onchange,
		class: klass = ''
	}: Props = $props();

	const uid = `tags-${Math.random().toString(36).slice(2, 8)}`;
	const MAX = 40;

	let text = $state('');
	let focused = $state(false);
	let open = $state(false);
	let active = $state(-1); // -1: what was typed; 0…: a suggestion
	let selectedChip = $state<number | null>(null); // Backspace's first press
	let box: HTMLDivElement | undefined = $state();
	let input: HTMLInputElement | undefined = $state();
	let menu: HTMLDivElement | undefined = $state();
	let pos = $state({ left: 0, top: 0, width: 0 });

	let known = $derived(suggestions.map((s) => (typeof s === 'string' ? { tag: s } : s)));
	let chosen = $derived(new Set(value.map((t) => t.toLowerCase())));

	/** A tag as stored: no separators, no leading #, at most MAX characters. */
	function clean(raw: string): string {
		return raw.trim().replace(/^#+/, '').replace(/[\s,]+/g, '').slice(0, MAX);
	}
	/** The existing spelling of a tag typed in another case. */
	function canonical(tag: string): string {
		return known.find((k) => k.tag.toLowerCase() === tag.toLowerCase())?.tag ?? tag;
	}

	let typed = $derived(clean(text));
	let matches = $derived.by(() => {
		const q = typed.toLowerCase();
		const free = known.filter((k) => !chosen.has(k.tag.toLowerCase()));
		if (!q) return free.slice(0, 8);
		const starts = free.filter((k) => k.tag.toLowerCase().startsWith(q));
		const contains = free.filter((k) => !k.tag.toLowerCase().startsWith(q) && k.tag.toLowerCase().includes(q));
		return [...starts, ...contains].slice(0, 8);
	});
	/** Typed text that isn't a tag yet: offered first, as a new tag. */
	let isNew = $derived(!!typed && !known.some((k) => k.tag.toLowerCase() === typed.toLowerCase()) && !chosen.has(typed.toLowerCase()));
	/** Enter takes the first suggestion when it starts with what's typed (like any
	 *  autocomplete); otherwise it adds what's typed. Space and comma always add what's typed. */
	function bestGuess(): number {
		return typed && matches[0]?.tag.toLowerCase().startsWith(typed.toLowerCase()) ? 0 : -1;
	}
	let showMenu = $derived(open && focused && !readonly && (isNew || matches.length > 0));

	function set(next: string[]) {
		value = next;
		onchange?.(next);
	}
	function add(raw: string): boolean {
		const tag = canonical(clean(raw));
		if (!tag || chosen.has(tag.toLowerCase())) return false;
		set([...value, tag]);
		return true;
	}
	function remove(index: number) {
		set(value.filter((_, i) => i !== index));
		selectedChip = null;
		input?.focus();
	}
	/** Make what was typed (or the highlighted suggestion) a tag. */
	function commit(useActive = true) {
		const pick = useActive && active >= 0 ? matches[active]?.tag : null;
		if (pick) add(pick);
		else if (typed) add(typed);
		text = '';
		active = -1;
	}

	function keydown(e: KeyboardEvent) {
		if (readonly) return;
		const key = e.key;
		if (key !== 'Backspace') selectedChip = null;
		if (key === 'Enter') {
			if (!typed && active < 0) return; // nothing typed: let the form submit
			e.preventDefault();
			commit();
		} else if (key === ',' || key === ' ') {
			e.preventDefault();
			commit(false);
		} else if (key === 'Tab') {
			if (!typed && active < 0) return; // move focus on as usual
			e.preventDefault();
			// Complete to the highlighted (or best) suggestion, like a shell would.
			const pick = active >= 0 ? matches[active] : matches.find((k) => k.tag.toLowerCase().startsWith(typed.toLowerCase()));
			add(pick?.tag ?? typed);
			text = '';
			active = -1;
		} else if (key === 'ArrowDown' || key === 'ArrowUp') {
			if (!matches.length) return;
			e.preventDefault();
			open = true;
			const last = matches.length - 1;
			// The "add as a new tag" row (-1) is only there for text that isn't a tag yet.
			const first = isNew ? -1 : 0;
			active = key === 'ArrowDown' ? (active >= last ? first : active + 1) : active <= first ? last : active - 1;
		} else if (key === 'Escape') {
			if (showMenu) {
				e.preventDefault();
				e.stopPropagation(); // close the menu, not the dialog around it
				open = false;
				active = -1;
			}
		} else if (key === 'Backspace' && text === '' && value.length) {
			e.preventDefault();
			if (selectedChip === value.length - 1) remove(value.length - 1);
			else selectedChip = value.length - 1;
		}
	}

	function paste(e: ClipboardEvent) {
		const pasted = e.clipboardData?.getData('text') ?? '';
		if (!/[\s,]/.test(pasted.trim())) return; // one word: just type it
		e.preventDefault();
		const next = [...value];
		for (const raw of pasted.split(/[\s,]+/)) {
			const tag = canonical(clean(raw));
			if (tag && !next.some((t) => t.toLowerCase() === tag.toLowerCase())) next.push(tag);
		}
		if (next.length !== value.length) set(next);
		text = '';
	}

	function place() {
		if (!box) return;
		const r = box.getBoundingClientRect();
		pos = { left: r.left, top: r.bottom + 4, width: r.width };
	}
	$effect(() => {
		if (!showMenu || !menu) return;
		place();
		if (!menu.matches(':popover-open')) menu.showPopover();
		const follow = () => place();
		window.addEventListener('scroll', follow, true);
		window.addEventListener('resize', follow);
		return () => {
			window.removeEventListener('scroll', follow, true);
			window.removeEventListener('resize', follow);
			if (menu?.matches(':popover-open')) menu.hidePopover();
		};
	});

	let float = $derived(focused || value.length > 0 || !!text || !!placeholder);
	let border = $derived(focused ? 'border-2 border-primary' : 'border-outline hover:border-on-surface');
	const countLabel = (n: number | undefined) => (n === undefined ? '' : `${n}${noun ? ` ${noun}` : ''}`);
</script>

<div class="flex flex-col pt-2 {klass}">
	<!-- Clicking anywhere in the field types into it. -->
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div
		bind:this={box}
		class="relative flex min-h-14 flex-wrap items-center gap-1.5 rounded-xs py-2 pr-3 pl-3 {disabled ? 'opacity-40' : ''} {readonly ? '' : 'cursor-text'}"
		onclick={() => input?.focus()}
	>
		<fieldset aria-hidden="true" class="pointer-events-none absolute inset-x-0 -top-[5px] bottom-0 m-0 rounded-xs border px-3 transition-colors {border}">
			<legend class="invisible h-[11px] overflow-hidden whitespace-nowrap text-[12px] leading-[11px] transition-[max-width,padding] duration-150 {float ? 'max-w-full px-1' : 'max-w-0 p-0'}">{label}</legend>
		</fieldset>
		<label
			for={uid}
			class="pointer-events-none absolute left-4 z-10 origin-left transition-all duration-150 {focused ? 'text-primary' : 'text-on-surface-variant'} {float ? '-top-2 body-small' : 'top-1/2 -translate-y-1/2 body-large'}"
			>{label}</label
		>
		{#each value as tag, i (tag)}
			<span
				class="inline-flex h-7 max-w-full items-center gap-1 rounded-sm pl-2.5 label-large transition-colors {selectedChip === i
					? 'bg-primary text-on-primary'
					: 'bg-secondary-container text-on-secondary-container'} {readonly ? 'pr-2.5' : 'pr-1'}"
			>
				<span class="truncate">{tag}</span>
				{#if !readonly && !disabled}
					<button
						type="button"
						tabindex="-1"
						class="inline-flex size-5 items-center justify-center rounded-full hover:bg-on-secondary-container/12"
						aria-label="Remove {tag}"
						onclick={(e) => {
							e.stopPropagation();
							remove(i);
						}}><Icon name="close" size={14} /></button
					>
				{/if}
			</span>
		{/each}
		{#if !readonly}
			<input
				bind:this={input}
				id={uid}
				class="min-w-24 flex-1 bg-transparent py-1 body-large text-on-surface outline-none placeholder:text-on-surface-variant/70"
				bind:value={text}
				placeholder={value.length ? '' : placeholder}
				{disabled}
				autocomplete="off"
				spellcheck="false"
				role="combobox"
				aria-expanded={showMenu}
				aria-controls="{uid}-menu"
				aria-activedescendant={active >= 0 ? `${uid}-opt-${active}` : undefined}
				onfocus={() => {
					focused = true;
					open = true;
				}}
				onblur={() => {
					focused = false;
					commit(false); // a tag typed but not entered still counts
				}}
				oninput={() => {
					open = true;
					selectedChip = null;
					// A separator that arrived without its key press (phone keyboards, dictation,
					// autofill): what's before it becomes tags, the rest stays in the box.
					if (/[\s,]/.test(text)) {
						const parts = text.split(/[\s,]+/);
						const rest = parts.pop() ?? '';
						for (const part of parts) add(part);
						text = rest;
					}
					active = bestGuess();
				}}
				onkeydown={keydown}
				onpaste={paste}
			/>
		{:else if !value.length}
			<span class="py-1 body-large text-on-surface-variant">—</span>
		{/if}
	</div>
	{#if supporting}<p class="px-4 pt-1 body-small text-on-surface-variant">{supporting}</p>{/if}

	{#if showMenu}
		<!-- In the top layer, so dialogs can't clip it. Pointer-down keeps focus in the box. -->
		<div
			bind:this={menu}
			id="{uid}-menu"
			popover="manual"
			role="listbox"
			tabindex="-1"
			class="m-0 max-h-72 overflow-y-auto rounded-xs border-0 bg-surface-container p-0 py-1 text-on-surface shadow-e2 outline-none"
			style:position="fixed"
			style:left="{pos.left}px"
			style:top="{pos.top}px"
			style:width="{pos.width}px"
			onpointerdown={(e) => e.preventDefault()}
		>
			{#if isNew}
				<!-- svelte-ignore a11y_click_events_have_key_events -->
				<div
					role="option"
					tabindex="-1"
					aria-selected={active === -1}
					class="relative flex h-11 cursor-pointer items-center gap-3 px-3 body-medium {active === -1 ? 'bg-on-surface/8' : ''}"
					onpointermove={() => (active = -1)}
					onclick={() => commit(false)}
				>
					<Icon name="add" size={18} class="text-primary" />
					<span class="min-w-0 truncate">Add <span class="label-large">{typed}</span> as a new tag</span>
					<span class="ml-auto shrink-0 label-small text-on-surface-variant">{active === -1 ? 'Enter' : 'Space'}</span>
				</div>
			{/if}
			{#each matches as m, i (m.tag)}
				<!-- svelte-ignore a11y_click_events_have_key_events -->
				<div
					id="{uid}-opt-{i}"
					role="option"
					tabindex="-1"
					aria-selected={active === i}
					class="relative flex h-11 cursor-pointer items-center gap-3 px-3 body-medium {active === i ? 'bg-on-surface/8' : ''}"
					onpointermove={() => (active = i)}
					onclick={() => {
						add(m.tag);
						text = '';
						active = -1;
						input?.focus();
					}}
				>
					<Icon name="sell" size={18} class="text-on-surface-variant" />
					<span class="min-w-0 truncate">{m.tag}</span>
					{#if m.count !== undefined}
						<span class="ml-auto shrink-0 label-small text-on-surface-variant" title={m.here ? `${m.here} here` : undefined}>{countLabel(m.count)}</span>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>
