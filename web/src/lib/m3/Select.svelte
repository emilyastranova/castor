<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';

	interface Option {
		value: string;
		label: string;
		/** A second line in the menu (and matched by search). */
		description?: string;
		/** Options with the same group are listed under its heading (when not searching). */
		group?: string;
		/** More words to match, e.g. an id or tags. */
		keywords?: string;
	}
	interface Props {
		label: string;
		value?: string;
		options: Option[];
		supporting?: string;
		disabled?: boolean;
		class?: string;
		onchange?: (value: string) => void;
		/** Optional content shown before an option's label (in the field and in the menu). */
		leading?: Snippet<[string]>;
		/** A search box at the top of the menu. Default: on when there are more than 7 options. */
		searchable?: boolean;
	}
	let {
		label,
		value = $bindable(''),
		options,
		supporting,
		disabled,
		class: klass = '',
		onchange,
		leading,
		searchable
	}: Props = $props();

	const uid = `sel-${Math.random().toString(36).slice(2, 8)}`;
	const ITEM_HEIGHT = 48;

	let field: HTMLDivElement | undefined = $state();
	let trigger: HTMLButtonElement | undefined = $state();
	let list: HTMLDivElement | undefined = $state();
	let open = $state(false);
	let active = $state(-1);
	let pos = $state({ left: 0, top: 0, width: 0, maxHeight: 320, above: false });
	let typed = '';
	let typedAt = 0;

	let selected = $derived(options.find((o) => o.value === value));

	// ---- search ----
	let withSearch = $derived(searchable ?? options.length > 7);
	let query = $state('');
	let searchBox: HTMLInputElement | undefined = $state();
	/** Best matches first: label starts with it, a word starts with it, then anywhere. */
	function score(o: Option, q: string): number {
		const label = o.label.toLowerCase();
		if (label.startsWith(q)) return 0;
		if (label.split(/[\s·:/_-]+/).some((w) => w.startsWith(q))) return 1;
		if (label.includes(q)) return 2;
		const rest = `${o.value} ${o.keywords ?? ''} ${o.description ?? ''} ${o.group ?? ''}`.toLowerCase();
		return rest.includes(q) ? 3 : -1;
	}
	let shown = $derived.by(() => {
		const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
		if (!words.length) return options;
		return options
			.map((o, i) => ({ o, i, s: Math.max(...words.map((w) => score(o, w))), worst: Math.min(...words.map((w) => score(o, w))) }))
			.filter((x) => x.worst >= 0)
			.sort((a, b) => a.s - b.s || a.i - b.i)
			.map((x) => x.o);
	});
	/** Group headings: only while the full list shows, and only if any option has a group. */
	let grouped = $derived(!query.trim() && options.some((o) => o.group));
	let hasDescriptions = $derived(options.some((o) => o.description));
	/** The search changed: the best match becomes the active option. */
	function searched() {
		active = shown.length ? 0 : -1;
	}

	/** Place the menu under the field, or above it when there is more room there. */
	function place() {
		if (!trigger) return;
		const r = trigger.getBoundingClientRect();
		const margin = 8;
		const gap = 4;
		const perItem = hasDescriptions ? 60 : ITEM_HEIGHT;
		const wanted = Math.min(options.length * perItem + 16 + (withSearch ? 56 : 0), withSearch ? 420 : 320);
		const spaceBelow = window.innerHeight - r.bottom - margin - gap;
		const spaceAbove = r.top - margin - gap;
		const above = spaceBelow < wanted && spaceAbove > spaceBelow;
		pos = {
			left: r.left,
			width: r.width,
			top: above ? r.top - gap : r.bottom + gap,
			maxHeight: Math.max(Math.min(wanted, above ? spaceAbove : spaceBelow), ITEM_HEIGHT * 2),
			above
		};
	}

	function openMenu(index?: number) {
		if (disabled || open || options.length === 0) return;
		place();
		query = '';
		const current = options.findIndex((o) => o.value === value);
		open = true;
		active = index ?? (current >= 0 ? current : 0);
	}

	function closeMenu(refocus = true) {
		if (!open) return;
		open = false;
		if (refocus) trigger?.focus({ preventScroll: true });
	}

	function choose(index: number) {
		const option = shown[index];
		if (!option) return;
		const changed = option.value !== value;
		value = option.value;
		closeMenu();
		if (changed) onchange?.(option.value);
	}

	function scrollActiveIntoView() {
		list?.querySelector(`#${CSS.escape(uid)}-opt-${active}`)?.scrollIntoView({ block: 'nearest' });
	}

	/** Type-to-jump: typing letters moves to the next option whose label starts with them. */
	function typeahead(key: string) {
		const now = Date.now();
		typed = now - typedAt > 700 ? key : typed + key;
		typedAt = now;
		const query = typed.toLowerCase();
		const start = typed.length === 1 ? active + 1 : active;
		for (let i = 0; i < options.length; i++) {
			const index = (start + i) % options.length;
			if (options[index]!.label.toLowerCase().startsWith(query)) return index;
		}
		return -1;
	}

	/** Typing on the closed field of a searchable select opens it with that text searched. */
	function openWithText(key: string) {
		openMenu();
		query = key;
		searched();
	}

	// Show the popover once it is in the DOM; it lives in the top layer so dialog
	// scroll containers cannot clip it.
	$effect(() => {
		if (!open || !list) return;
		if (!list.matches(':popover-open')) list.showPopover();
		if (withSearch) searchBox?.focus({ preventScroll: true });
		else list.querySelector('ul')?.focus({ preventScroll: true });
		scrollActiveIntoView();
	});

	// While open: follow the field when anything scrolls or the window resizes,
	// and close on any press outside the field and menu.
	$effect(() => {
		if (!open) return;
		const reposition = (e: Event) => {
			if (list && e.target instanceof Node && list.contains(e.target)) return;
			place();
		};
		const outside = (e: PointerEvent) => {
			const target = e.target as Node;
			if (field?.contains(target) || list?.contains(target)) return;
			closeMenu(false);
		};
		window.addEventListener('scroll', reposition, true);
		window.addEventListener('resize', reposition);
		document.addEventListener('pointerdown', outside, true);
		return () => {
			window.removeEventListener('scroll', reposition, true);
			window.removeEventListener('resize', reposition);
			document.removeEventListener('pointerdown', outside, true);
		};
	});

	function onTriggerKey(e: KeyboardEvent) {
		if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
			e.preventDefault();
			openMenu();
		} else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && withSearch) {
			e.preventDefault();
			openWithText(e.key);
		} else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
			// Like a native select: typing on the closed field picks the matching option.
			const index = typeahead(e.key);
			if (index >= 0) {
				active = index;
				choose(index);
			}
		}
	}

	function onListKey(e: KeyboardEvent) {
		const last = shown.length - 1;
		const move = (index: number) => {
			e.preventDefault();
			active = Math.max(0, Math.min(last, index));
			scrollActiveIntoView();
		};
		switch (e.key) {
			case 'ArrowDown':
				return move(active + 1);
			case 'ArrowUp':
				return move(active - 1);
			case 'Home':
				if (withSearch) return; // the search box's cursor
				return move(0);
			case 'End':
				if (withSearch) return;
				return move(last);
			case 'Enter':
				e.preventDefault();
				return choose(active);
			case ' ':
				if (withSearch) return; // a space in the search box
				e.preventDefault();
				return choose(active);
			case 'Escape':
				// Keep Escape from also closing a surrounding dialog.
				e.preventDefault();
				e.stopPropagation();
				return closeMenu();
			case 'Tab':
				e.preventDefault();
				return closeMenu();
			default:
				if (!withSearch && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
					const index = typeahead(e.key);
					if (index >= 0) move(index);
				}
		}
	}
</script>

<!-- pt-2 matches the outlined TextField, which reserves room for the floating label -->
<div class="flex flex-col pt-2 {klass}">
	<div class="group relative h-14 rounded-xs" bind:this={field}>
		<fieldset
			aria-hidden="true"
			class="pointer-events-none absolute inset-x-0 -top-[5px] bottom-0 m-0 rounded-xs border px-3 transition-colors {open
				? 'border-2 border-primary'
				: 'border-outline group-hover:border-on-surface group-focus-within:border-2 group-focus-within:border-primary'}"
		>
			<legend
				class="invisible h-[11px] max-w-full overflow-hidden px-1 text-[12px] leading-[11px] whitespace-nowrap"
				>{label}</legend
			>
		</fieldset>
		<label
			id="{uid}-label"
			for={uid}
			class="pointer-events-none absolute -top-2 left-4 body-small {open
				? 'text-primary'
				: 'text-on-surface-variant group-focus-within:text-primary'}">{label}</label
		>
		<button
			id={uid}
			bind:this={trigger}
			type="button"
			role="combobox"
			aria-haspopup="listbox"
			aria-expanded={open}
			aria-controls="{uid}-list"
			aria-labelledby="{uid}-label {uid}"
			{disabled}
			class="flex h-full w-full items-center gap-3 bg-transparent pr-10 pl-4 text-left text-on-surface body-large outline-none disabled:opacity-40"
			onclick={() => (open ? closeMenu() : openMenu())}
			onkeydown={onTriggerKey}
		>
			{#if leading && selected}{@render leading(selected.value)}{/if}
			<span class="min-w-0 flex-1 truncate">{selected?.label ?? ''}</span>
		</button>
		<Icon
			name="arrow_drop_down"
			class="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-on-surface-variant transition-transform duration-150 {open
				? 'rotate-180'
				: ''}"
		/>

		{#if open}
			<!-- The popover lives in the top layer so dialog scroll containers can't clip it. -->
			<div
				bind:this={list}
				popover="manual"
				role="presentation"
				class="m-0 flex flex-col overflow-hidden rounded-xs border-0 bg-surface-container p-0 text-on-surface shadow-e2 outline-none"
				style:position="fixed"
				style:inset="auto"
				style:left="{pos.left}px"
				style:top="{pos.top}px"
				style:min-width="{pos.width}px"
				style:max-width="min(560px, calc(100vw - 16px))"
				style:max-height="{pos.maxHeight}px"
				style:transform={pos.above ? 'translateY(-100%)' : undefined}
				tabindex="-1"
				onkeydown={onListKey}
			>
				{#if withSearch}
					<div class="flex shrink-0 items-center gap-2 border-b border-outline-variant px-3">
						<Icon name="search" size={20} class="text-on-surface-variant" />
						<input
							bind:this={searchBox}
							bind:value={query}
							oninput={searched}
							class="h-12 min-w-0 flex-1 bg-transparent body-large text-on-surface outline-none placeholder:text-on-surface-variant"
							placeholder="Search {options.length} options"
							aria-label="Search {label}"
							aria-controls="{uid}-list"
							aria-activedescendant={active >= 0 ? `${uid}-opt-${active}` : undefined}
							autocomplete="off"
							spellcheck="false"
						/>
						{#if query}<button type="button" class="state-layer rounded-full p-1 text-on-surface-variant" aria-label="Clear search" onclick={() => { query = ''; searched(); searchBox?.focus(); }}><Icon name="close" size={18} /></button>{/if}
					</div>
				{/if}
				<ul
					id="{uid}-list"
					role="listbox"
					aria-labelledby="{uid}-label"
					aria-activedescendant={!withSearch && active >= 0 ? `${uid}-opt-${active}` : undefined}
					tabindex="-1"
					class="m-0 min-h-0 flex-1 overflow-y-auto py-2 outline-none"
				>
					{#each shown as option, i (option.value)}
						{#if grouped && option.group && option.group !== shown[i - 1]?.group}
							<li role="presentation" class="px-3 pt-2 pb-1 label-small tracking-wide text-on-surface-variant uppercase">{option.group}</li>
						{/if}
						<!-- Keyboard interaction is handled on the popover (aria-activedescendant pattern). -->
						<!-- svelte-ignore a11y_click_events_have_key_events -->
						<li
							id="{uid}-opt-{i}"
							role="option"
							aria-selected={option.value === value}
							class="relative flex cursor-pointer items-center gap-3 px-3 select-none {option.description ? 'min-h-14 py-1.5' : 'h-12'} {option.value === value
								? 'bg-secondary-container text-on-secondary-container'
								: ''}"
							onpointermove={() => (active = i)}
							onclick={() => choose(i)}
						>
							{#if i === active}
								<span class="pointer-events-none absolute inset-0 bg-on-surface/8"></span>
							{/if}
							{#if leading}{@render leading(option.value)}{/if}
							<span class="min-w-0 flex-1">
								<span class="block truncate body-large">{option.label}</span>
								{#if option.description}<span class="block truncate body-small text-on-surface-variant">{option.description}</span>{/if}
							</span>
							{#if option.value === value}<Icon name="check" size={20} />{/if}
						</li>
					{:else}
						<li class="px-4 py-3 body-medium text-on-surface-variant">Nothing matches “{query}”.</li>
					{/each}
				</ul>
			</div>
		{/if}
	</div>
	{#if supporting}<p class="px-4 pt-1 body-small text-on-surface-variant">{supporting}</p>{/if}
</div>
