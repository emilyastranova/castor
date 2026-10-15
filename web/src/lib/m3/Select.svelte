<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';

	interface Option {
		value: string;
		label: string;
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
	}
	let {
		label,
		value = $bindable(''),
		options,
		supporting,
		disabled,
		class: klass = '',
		onchange,
		leading
	}: Props = $props();

	const uid = `sel-${Math.random().toString(36).slice(2, 8)}`;
	const ITEM_HEIGHT = 48;

	let field: HTMLDivElement | undefined = $state();
	let trigger: HTMLButtonElement | undefined = $state();
	let list: HTMLUListElement | undefined = $state();
	let open = $state(false);
	let active = $state(-1);
	let pos = $state({ left: 0, top: 0, width: 0, maxHeight: 320, above: false });
	let typed = '';
	let typedAt = 0;

	let selected = $derived(options.find((o) => o.value === value));

	/** Place the menu under the field, or above it when there is more room there. */
	function place() {
		if (!trigger) return;
		const r = trigger.getBoundingClientRect();
		const margin = 8;
		const gap = 4;
		const wanted = Math.min(options.length * ITEM_HEIGHT + 16, 320);
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
		const current = options.findIndex((o) => o.value === value);
		active = index ?? (current >= 0 ? current : 0);
		open = true;
	}

	function closeMenu(refocus = true) {
		if (!open) return;
		open = false;
		if (refocus) trigger?.focus({ preventScroll: true });
	}

	function choose(index: number) {
		const option = options[index];
		if (!option) return;
		const changed = option.value !== value;
		value = option.value;
		closeMenu();
		if (changed) onchange?.(option.value);
	}

	function scrollActiveIntoView() {
		list?.querySelector(`#${uid}-opt-${active}`)?.scrollIntoView({ block: 'nearest' });
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

	// Show the popover once it is in the DOM; it lives in the top layer so dialog
	// scroll containers cannot clip it.
	$effect(() => {
		if (!open || !list) return;
		if (!list.matches(':popover-open')) list.showPopover();
		list.focus({ preventScroll: true });
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
		const last = options.length - 1;
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
				return move(0);
			case 'End':
				return move(last);
			case 'Enter':
			case ' ':
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
				if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
					const index = typeahead(e.key);
					if (index >= 0) move(index);
				}
		}
	}
</script>

<div class="flex flex-col {klass}">
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
			<ul
				bind:this={list}
				id="{uid}-list"
				popover="manual"
				role="listbox"
				tabindex="-1"
				aria-labelledby="{uid}-label"
				aria-activedescendant={active >= 0 ? `${uid}-opt-${active}` : undefined}
				class="m-0 overflow-y-auto rounded-xs border-0 bg-surface-container py-2 text-on-surface shadow-e2 outline-none"
				style:position="fixed"
				style:inset="auto"
				style:left="{pos.left}px"
				style:top="{pos.top}px"
				style:min-width="{pos.width}px"
				style:max-height="{pos.maxHeight}px"
				style:transform={pos.above ? 'translateY(-100%)' : undefined}
				onkeydown={onListKey}
			>
				{#each options as option, i (option.value)}
					<!-- Keyboard interaction is handled on the listbox (aria-activedescendant pattern). -->
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<li
						id="{uid}-opt-{i}"
						role="option"
						aria-selected={option.value === value}
						class="relative flex h-12 cursor-pointer items-center gap-3 px-3 body-large select-none {option.value ===
						value
							? 'bg-secondary-container text-on-secondary-container'
							: ''}"
						onpointermove={() => (active = i)}
						onclick={() => choose(i)}
					>
						{#if i === active}
							<span class="pointer-events-none absolute inset-0 bg-on-surface/8"></span>
						{/if}
						{#if leading}{@render leading(option.value)}{/if}
						<span class="min-w-0 flex-1 truncate">{option.label}</span>
						{#if option.value === value}<Icon name="check" size={20} />{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</div>
	{#if supporting}<p class="px-4 pt-1 body-small text-on-surface-variant">{supporting}</p>{/if}
</div>
