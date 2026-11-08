<script lang="ts">
	import type { CommandDefinition, PlaybookSpec, PlaybookStepSpec, ToolDefinition } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { BLOCK_MIME } from '$lib/playbook';
	import { commandMatches, folders, NO_FILTERS } from '$lib/library';
	import { outputsOf, wiringFor, type Wiring } from '$lib/playbookEditor';
	import CommandRow from '$lib/components/library/CommandRow.svelte';

	/** The editor's "add a step" list: the commands that can follow the selected step first,
	 *  then the ones this playbook already uses, then every tool's commands (the Library's
	 *  folders). Arrow keys move, Enter adds after the selected step and connects it,
	 *  Shift+Enter adds it on its own. Rows can also be dragged onto the canvas. */
	interface Props {
		spec: PlaybookSpec;
		commands: CommandDefinition[];
		tools: ToolDefinition[];
		selected: PlaybookStepSpec | null;
		/** `block` is "transform" or "command:<id>"; `connect` adds it after the selected step, wired. */
		onadd: (block: string, connect: boolean) => void;
	}
	let { spec, commands, tools, selected, onadd }: Props = $props();

	let query = $state('');
	let search = $state<HTMLInputElement | null>(null);
	let listEl = $state<HTMLDivElement | null>(null);
	export function focusSearch() {
		search?.focus();
		search?.select();
	}

	const byId = $derived(new Map(commands.map((c) => [c.id, c])));
	const matches = (c: CommandDefinition) => commandMatches(c, { ...NO_FILTERS, q: query }, c.spec.tool ?? '');
	const transformMatches = $derived(!query.trim() || 'transform filter reshape pluck'.includes(query.trim().toLowerCase()));

	interface Row {
		key: string;
		block: string;
		command?: CommandDefinition;
		wiring?: Wiring | null;
		connect: boolean;
	}
	interface Section {
		title: string;
		hint?: string;
		rows: Row[];
	}

	let canFollow = $derived(!!selected && outputsOf(selected, byId).length > 0);
	let sections = $derived.by((): Section[] => {
		const out: Section[] = [];
		if (selected && canFollow) {
			const rows: Row[] = [];
			if (transformMatches) rows.push({ key: 'after:transform', block: 'transform', connect: true });
			for (const c of commands) {
				if (c.id === selected.command_ref) continue; // feeding a step into itself is rarely the next move
				const w = wiringFor(selected, c, byId);
				if (w && matches(c)) rows.push({ key: `after:${c.id}`, block: `command:${c.id}`, command: c, wiring: w, connect: true });
			}
			if (rows.length) out.push({ title: `After ${selected.name}`, hint: 'Enter adds it and connects it', rows });
		}
		const used = [...new Set(spec.steps.map((st) => st.command_ref).filter((x): x is string => !!x))]
			.map((id) => byId.get(id))
			.filter((c): c is CommandDefinition => !!c && matches(c));
		if (used.length) out.push({ title: 'In this playbook', rows: used.map((c) => ({ key: `used:${c.id}`, block: `command:${c.id}`, command: c, connect: false })) });
		if (transformMatches) out.push({ title: 'On the server', rows: [{ key: 'transform', block: 'transform', connect: false }] });
		for (const f of folders(commands, tools)) {
			const rows = f.commands.filter(matches).map((c) => ({ key: `${f.id}:${c.id}`, block: `command:${c.id}`, command: c, connect: false }));
			if (rows.length) out.push({ title: f.name, rows });
		}
		return out;
	});
	let rows = $derived(sections.flatMap((s) => s.rows));
	let cursor = $state<string | null>(null);
	// Keep the cursor on a row that exists.
	$effect(() => {
		if (cursor && !rows.some((r) => r.key === cursor)) cursor = null;
	});
	const rowId = (key: string) => `pal-${key.replace(/[^a-zA-Z0-9_-]/g, '_')}`;

	function move(step: number) {
		if (!rows.length) return;
		const i = cursor ? rows.findIndex((r) => r.key === cursor) : -1;
		const next = rows[Math.max(0, Math.min(rows.length - 1, i < 0 ? (step > 0 ? 0 : rows.length - 1) : i + step))];
		cursor = next.key;
		document.getElementById(rowId(next.key))?.scrollIntoView({ block: 'nearest' });
	}
	function add(row: Row, shift = false) {
		onadd(row.block, row.connect && !shift);
		// The new step is selected now: show what can follow it, from the top.
		query = '';
		cursor = null;
	}
	function listKey(e: KeyboardEvent) {
		const row = rows.find((r) => r.key === cursor);
		if (e.key === 'ArrowDown') move(1);
		else if (e.key === 'ArrowUp') {
			if (rows[0]?.key === cursor) {
				cursor = null;
				search?.focus();
			} else move(-1);
		} else if (e.key === 'Home') move(-rows.length);
		else if (e.key === 'End') move(rows.length);
		else if (e.key === 'Enter' && row) add(row, e.shiftKey);
		else if (e.key === 'Escape') search?.focus();
		else return;
		e.preventDefault();
		e.stopPropagation();
	}
	function searchKey(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			listEl?.focus();
			move(1);
		} else if (e.key === 'Enter' && rows.length) {
			e.preventDefault();
			add(rows.find((r) => r.key === cursor) ?? rows[0], e.shiftKey);
		} else if (e.key === 'Escape') {
			e.stopPropagation();
			if (query) query = '';
			else search?.blur();
		}
	}
	const describe = (w: Wiring) => (w.each ? `one card per item of ${w.output.name} → ${w.arg.name}` : `${w.output.name} → ${w.arg.name}`);
</script>

<div class="flex min-h-0 flex-1 flex-col gap-2">
	<div class="flex items-baseline justify-between gap-2">
		<p class="label-large text-on-surface">Add a step</p>
		<p class="body-small text-on-surface-variant">drag, click or Enter</p>
	</div>
	<label class="flex h-10 items-center gap-2 rounded-full bg-surface-container-high px-3 focus-within:outline-2 focus-within:outline-secondary">
		<Icon name="search" size={18} class="text-on-surface-variant" />
		<input
			bind:this={search}
			bind:value={query}
			class="h-full min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant"
			placeholder="Commands, tools, outputs"
			aria-label="Search steps to add"
			autocomplete="off"
			onkeydown={searchKey}
		/>
		{#if query}
			<button type="button" class="state-layer flex size-7 items-center justify-center rounded-full text-on-surface-variant" aria-label="Clear the search" onclick={() => (query = '')}><Icon name="close" size={16} /></button>
		{:else}
			<kbd class="hidden rounded-xs border border-outline-variant px-1.5 font-mono text-[12px] text-on-surface-variant pointer-fine:inline">/</kbd>
		{/if}
	</label>

	<!-- svelte-ignore a11y_no_noninteractive_tabindex (a listbox: the arrow keys move through it) -->
	<div
		bind:this={listEl}
		class="-mx-1 flex min-h-0 flex-1 flex-col overflow-y-auto rounded-md px-1 pb-2 outline-none focus-visible:outline-2 focus-visible:outline-secondary"
		role="listbox"
		tabindex="0"
		aria-label="Steps to add"
		aria-activedescendant={cursor ? rowId(cursor) : undefined}
		onkeydown={listKey}
	>
		{#each sections as s (s.title)}
			<div class="flex flex-col gap-px pt-2" role="group" aria-label={s.title}>
				<p class="px-2 pb-1 label-medium {s.hint ? 'text-primary' : 'text-on-surface-variant'}">
					<span class="block truncate">{s.title}</span>
					{#if s.hint}<span class="block body-small text-on-surface-variant">{s.hint}</span>{/if}
				</p>
				{#each s.rows as row (row.key)}
					<!-- svelte-ignore a11y_no_static_element_interactions (the row inside is the option) -->
					<div
						draggable="true"
						class="rounded-md {cursor === row.key ? 'bg-surface-container-high' : ''}"
						title={row.wiring ? `Adds it after ${selected?.name}: ${describe(row.wiring)}` : (row.command?.description ?? '')}
						ondragstart={(e) => e.dataTransfer?.setData(BLOCK_MIME, row.block)}
					>
						{#if row.command}
							<CommandRow
								command={row.command}
								nested={false}
								id={rowId(row.key)}
								selected={false}
								onselect={() => {
									cursor = row.key;
									add(row);
								}}
							/>
						{:else}
							<!-- svelte-ignore a11y_click_events_have_key_events, a11y_interactive_supports_focus (the listbox owns focus and the keyboard) -->
							<div
								id={rowId(row.key)}
								role="option"
								aria-selected="false"
								class="state-layer flex min-h-12 cursor-pointer items-center gap-3 rounded-md border border-dashed border-tertiary/60 px-3 py-1.5"
								onclick={() => {
									cursor = row.key;
									add(row);
								}}
							>
								<Icon name="filter_alt" size={18} class="shrink-0 text-tertiary" />
								<span class="min-w-0">
									<span class="block title-small text-on-surface">Transform</span>
									<span class="block body-small text-on-surface-variant">{row.connect ? `Filter or reshape ${selected?.name}’s results` : 'Filter or reshape results'}</span>
								</span>
							</div>
						{/if}
						{#if row.wiring}
							<p class="-mt-1 truncate pb-1.5 pl-3 font-mono text-[11.5px] text-primary">{describe(row.wiring)}</p>
						{/if}
					</div>
				{/each}
			</div>
		{:else}
			<p class="px-2 py-6 text-center body-small text-on-surface-variant">Nothing matches “{query}”.</p>
		{/each}
	</div>
</div>
