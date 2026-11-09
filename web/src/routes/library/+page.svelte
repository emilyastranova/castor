<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { CommandDefinition, ToolDefinition } from '$lib/api';
	import { api } from '$lib/api';
	import { Button, Chip, Dialog, Icon, IconButton, Menu, MenuItem, TopAppBar } from '$lib/m3';
	import { canEdit } from '$lib/session';
	import { aiStatus } from '$lib/ai.svelte';
	import { realtime } from '$lib/realtime.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import LibraryTransfer from '$lib/components/LibraryTransfer.svelte';
	import AiGenerateDialog from '$lib/components/AiGenerateDialog.svelte';
	import CommandRow from '$lib/components/library/CommandRow.svelte';
	import CommandDetail from '$lib/components/library/CommandDetail.svelte';
	import ToolDetail from '$lib/components/library/ToolDetail.svelte';
	import { filterFolders, filtering, folders, NO_FILTERS, NO_TOOL, provision, readiness, type Filters } from '$lib/library';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let commands = $state<CommandDefinition[]>(data.commands);
	// svelte-ignore state_referenced_locally
	let tools = $state<ToolDefinition[]>(data.tools);
	// svelte-ignore state_referenced_locally
	let loaded = data;
	$effect(() => {
		if (data !== loaded) {
			loaded = data;
			commands = data.commands;
			tools = data.tools;
		}
	});
	const editable = $derived(canEdit());
	$effect(() => void aiStatus.load());

	// Tools change state as agents prepare them; commands change when someone edits the library.
	let timer: ReturnType<typeof setTimeout> | undefined;
	async function reloadTools() {
		try {
			tools = await api().tools.list();
		} catch {
			/* keep what we have */
		}
	}
	$effect(() =>
		realtime.subscribe((e) => {
			if (['tool.status', 'tools.changed', 'agent.updated', 'agent.deleted'].includes(e.name)) {
				clearTimeout(timer);
				timer = setTimeout(reloadTools, 300);
			}
		})
	);

	// ---- what's shown ----
	let filters = $state<Filters>({ ...NO_FILTERS });
	let all = $derived(folders(commands, tools));
	let shown = $derived(filterFolders(all, filters));
	let collapsed = $state<Set<string>>(loadCollapsed());
	// While filtering every matching folder opens; folding one then lasts until the filters change.
	let filterFolded = $state<Set<string>>(new Set());
	let filterKey = $derived(JSON.stringify(filters));
	$effect(() => {
		void filterKey;
		filterFolded = new Set();
	});
	const isOpen = (id: string) => (filtering(filters) ? !filterFolded.has(id) : !collapsed.has(id));

	function loadCollapsed(): Set<string> {
		try {
			return new Set(JSON.parse(localStorage.getItem('castor.library.collapsed') ?? '[]'));
		} catch {
			return new Set();
		}
	}
	function toggle(id: string, open?: boolean) {
		if (filtering(filters)) {
			const next = new Set(filterFolded);
			if (open ?? next.has(id)) next.delete(id);
			else next.add(id);
			filterFolded = next;
			return;
		}
		const next = new Set(collapsed);
		if (open ?? next.has(id)) next.delete(id);
		else next.add(id);
		collapsed = next;
		try {
			localStorage.setItem('castor.library.collapsed', JSON.stringify([...next]));
		} catch {
			/* only for this tab */
		}
	}

	/** Keys for the rows the keyboard walks through: `t:<tool>`, `f:` (no-tool folder), `c:<command>`. */
	let rows = $derived(
		shown.flatMap((f) => [f.tool ? `t:${f.id}` : 'f:', ...(isOpen(f.id) ? f.commands.map((c) => `c:${c.id}`) : [])])
	);

	// ---- selection (in the URL: ?c=, ?t=, ?new=command|tool) ----
	let selected = $derived.by(() => {
		const q = page.url.searchParams;
		if (q.get('c')) return `c:${q.get('c')}`;
		if (q.get('t')) return `t:${q.get('t')}`;
		if (q.get('new')) return `new:${q.get('new')}`;
		return null;
	});
	let selectedCommand = $derived(selected?.startsWith('c:') ? commands.find((c) => c.id === selected.slice(2)) ?? null : null);
	let selectedTool = $derived(selected?.startsWith('t:') ? tools.find((t) => t.id === selected.slice(2)) ?? null : null);
	let cursor = $state<string | null>(null);
	$effect(() => {
		if (selected && !selected.startsWith('new:')) cursor = selected;
	});

	let dirty = $state(false);
	let draft = $state('');

	async function canLeave(): Promise<boolean> {
		if (!dirty) return true;
		const ok = await confirmAction({ title: 'Discard your changes?', message: 'The YAML you edited hasn’t been saved.', confirmLabel: 'Discard', danger: true });
		if (ok) dirty = false;
		return ok;
	}
	async function select(key: string | null, push = false) {
		if (key === selected) return;
		if (!(await canLeave())) return;
		const params = new URLSearchParams();
		if (key?.startsWith('c:')) params.set('c', key.slice(2));
		else if (key?.startsWith('t:')) params.set('t', key.slice(2));
		else if (key?.startsWith('new:')) params.set('new', key.slice(4));
		const qs = params.toString();
		await goto(qs ? `?${qs}` : '/library', { replaceState: !push, keepFocus: true, noScroll: true });
	}

	const TEMPLATE = (tool: string | null) => `id: my-command
name: My command
description: What it does
${tool ? `tool: ${tool}` : 'tool: nmap                             # a tool from the library (or: image: <ref>); omit both to run in the agent shell'}
args:
  - name: targets
    type: list
    required: true
    description: Hosts or networks
command: mkdir -p {{work}}/artifacts && echo {{ targets }} > {{work}}/artifacts/result.txt
outputs:
  - name: result
    source: "{{work}}/artifacts/result.txt"
`;
	const TOOL_TEMPLATE = `id: my-tool
name: My tool
description: What it is
# Give one or more of these. Agents use a published image first, then the recipe, then the host.
# image: docker.io/someone/tool:1.2
build:
  base: docker.io/library/python:3.12-slim
  install: |
    pip install --no-cache-dir my-tool==1.2
  check: my-tool --version
# host:
#   check: command -v my-tool
#   install: pipx install my-tool==1.2
`;
	/** The tool the cursor is in, for "new command" (it starts with `tool:` filled in). */
	let cursorTool = $derived.by(() => {
		const at = cursor ?? '';
		if (at.startsWith('t:')) return at.slice(2);
		const c = at.startsWith('c:') ? commands.find((x) => x.id === at.slice(2)) : null;
		return c?.spec.tool ?? null;
	});
	async function newCommand(yaml?: string) {
		if (!(await canLeave())) return;
		draft = yaml ?? TEMPLATE(cursorTool);
		await select('new:command', true);
	}
	async function newTool(yaml?: string) {
		if (!(await canLeave())) return;
		draft = yaml ?? TOOL_TEMPLATE;
		await select('new:tool', true);
	}
	// Opened by a link (?new=command) without a draft: start from the template.
	$effect(() => {
		if (selected === 'new:command' && !draft) draft = TEMPLATE(page.url.searchParams.get('tool'));
		if (selected === 'new:tool' && !draft) draft = TOOL_TEMPLATE;
	});

	function savedCommand(c: CommandDefinition) {
		commands = [...commands.filter((x) => x.id !== c.id), c];
		draft = '';
		void select(`c:${c.id}`);
	}
	function savedTool(t: ToolDefinition) {
		tools = [...tools.filter((x) => x.id !== t.id), t];
		draft = '';
		void select(`t:${t.id}`);
	}

	// ---- AI drafts ----
	let aiOpen = $state(false);
	let aiKind = $state<'command' | 'tool'>('command');
	let aiCurrent = $state('');
	let aiUse: (yaml: string) => void = () => {};
	function askAi(kind: 'command' | 'tool', current: string, use: (yaml: string) => void) {
		aiKind = kind;
		aiCurrent = current;
		aiUse = use;
		aiOpen = true;
	}

	// ---- keyboard ----
	let search = $state<HTMLInputElement | null>(null);
	let list = $state<HTMLDivElement | null>(null);
	const rowId = (key: string) => `lib-${key.replace(/[^a-zA-Z0-9_-]/g, '_')}`;

	function move(step: number) {
		if (!rows.length) return;
		const i = cursor ? rows.indexOf(cursor) : -1;
		const next = rows[Math.max(0, Math.min(rows.length - 1, i < 0 ? 0 : i + step))];
		cursor = next;
		document.getElementById(rowId(next))?.scrollIntoView({ block: 'nearest' });
		if (next !== 'f:') void select(next);
	}
	function folderOf(key: string): string | null {
		if (key.startsWith('t:')) return key.slice(2);
		if (key === 'f:') return NO_TOOL;
		const c = commands.find((x) => x.id === key.slice(2));
		return shown.find((f) => f.commands.some((x) => x.id === c?.id))?.id ?? null;
	}
	function listKey(e: KeyboardEvent) {
		if (e.key === 'ArrowDown' || e.key === 'j') move(1);
		else if (e.key === 'ArrowUp' || e.key === 'k') move(-1);
		else if (e.key === 'Home') move(-rows.length);
		else if (e.key === 'End') move(rows.length);
		else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && cursor) {
			const f = folderOf(cursor);
			if (f === null) return;
			if (cursor.startsWith('c:') && e.key === 'ArrowLeft') {
				cursor = f === NO_TOOL ? 'f:' : `t:${f}`;
				if (f !== NO_TOOL) void select(cursor);
			} else if (!cursor.startsWith('c:')) toggle(f, e.key === 'ArrowRight');
		} else if (e.key === 'Enter' && cursor) {
			if (cursor === 'f:') toggle(NO_TOOL);
			else {
				void select(cursor, true);
				detail?.focus();
			}
		} else return;
		e.preventDefault();
	}
	let detail = $state<HTMLElement | null>(null);
	// Opened on something (a link, search, a reload): bring its row into view once.
	let revealed = false;
	$effect(() => {
		if (revealed || !list || !selected || selected.startsWith('new:')) return;
		revealed = true;
		requestAnimationFrame(() => document.getElementById(rowId(selected))?.scrollIntoView({ block: 'center' }));
	});
	function windowKey(e: KeyboardEvent) {
		const el = e.target as HTMLElement;
		const typing = el.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]');
		if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
		if (e.key === '?') {
			shortcutsOpen = true;
		} else if (e.key === '/') {
			e.preventDefault();
			search?.focus();
			search?.select();
		} else if (e.key === 'n' && editable) {
			e.preventDefault();
			void newCommand();
		} else if (e.key === 'Escape' && detail?.contains(el)) {
			list?.focus();
		}
	}
	function searchKey(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			// Searching is usually for a command: open the first one that matches (or the first row).
			e.preventDefault();
			list?.focus();
			const first = rows.find((r) => r.startsWith('c:')) ?? rows[0];
			if (first) {
				cursor = first;
				document.getElementById(rowId(first))?.scrollIntoView({ block: 'nearest' });
				void select(first, true);
			}
		} else if (e.key === 'ArrowDown') {
			e.preventDefault();
			list?.focus();
			if (!cursor || !rows.includes(cursor)) move(1);
		} else if (e.key === 'Escape') {
			if (filters.q) filters.q = '';
			else list?.focus();
		}
	}

	let commandCount = $derived(commands.length);
	let mineCommands = $derived(commands.filter((c) => c.origin === 'user').map((c) => c.id));
	let mineTools = $derived(tools.filter((t) => t.origin === 'user').map((t) => t.id));
	// Your commands travel with the library parsers they name (built-in names just don't match one).
	let parsersOfMine = $derived([
		...new Set(commands.filter((c) => c.origin === 'user').flatMap((c) => c.spec.outputs.map((o) => o.parser).filter((p): p is string => !!p)))
	]);
	const dot: Record<string, string> = {
		ready: 'bg-primary',
		partial: 'bg-primary/50',
		preparing: 'bg-primary animate-pulse',
		failed: 'bg-error',
		unprepared: 'bg-outline',
		cannot: 'bg-outline-variant',
		'no-agents': 'bg-outline-variant'
	};
	let newOpen = $state(false);
	let shortcutsOpen = $state(false);
	// Readiness worth a glance: per folder only when something needs doing, plus one line above the list.
	let unprepared = $derived(tools.filter((t) => ['unprepared', 'partial', 'failed'].includes(readiness(t).state)).length);
	let noAgents = $derived(tools.length > 0 && tools.every((t) => readiness(t).state === 'no-agents'));
	const NOTEWORTHY = new Set(['failed', 'preparing', 'partial']);
	let toolName = (id: string | null | undefined) => tools.find((t) => t.id === id)?.name ?? null;
</script>

<svelte:head><title>Library · Castor</title></svelte:head>
<svelte:window onkeydown={windowKey} />

<TopAppBar title="Library" subtitle="{tools.length} tools · {commandCount} commands · what cards and playbooks run">
	{#snippet actions()}
		<IconButton icon="keyboard" label="Keyboard shortcuts (?)" onclick={() => (shortcutsOpen = true)} />
		<LibraryTransfer noun="library items" request={{ commands: mineCommands, tools: mineTools, parsers: parsersOfMine, label: 'castor-library' }} count={mineCommands.length + mineTools.length} onimported={async () => {
			[commands, tools] = await Promise.all([api().commands.list(), api().tools.list()]);
		}} />
		{#if editable}
			<Menu bind:open={newOpen} align="right">
				{#snippet anchor()}<Button icon="add" trailingIcon="arrow_drop_down" class="ml-2" aria-haspopup="menu" aria-expanded={newOpen}>New</Button>{/snippet}
				<MenuItem icon="terminal" onclick={() => newCommand()}>Command <span class="ml-auto pl-6 font-mono text-on-surface-variant">n</span></MenuItem>
				<MenuItem icon="handyman" onclick={() => newTool()}>Tool</MenuItem>
				{#if aiStatus.enabled}
					<div class="my-1 border-t border-outline-variant/60"></div>
					<MenuItem icon="auto_awesome" onclick={() => askAi('command', '', (y) => newCommand(y))}>Generate a command…</MenuItem>
					<MenuItem icon="auto_awesome" onclick={() => askAi('tool', '', (y) => newTool(y))}>Generate a tool…</MenuItem>
				{/if}
			</Menu>
		{/if}
	{/snippet}
</TopAppBar>

<div class="flex min-h-0 flex-1 gap-4 px-4 pb-4 md:px-6 md:pb-6">
	<!-- The rack: tools as folders, their commands inside. -->
	<section class="min-h-0 w-full flex-col lg:flex lg:w-[24rem] lg:shrink-0 xl:w-[28rem] {selected ? 'hidden' : 'flex'}" aria-label="Tools and commands">
		<div class="flex flex-col gap-2 pb-2">
			<label class="flex h-12 items-center gap-2 rounded-full bg-surface-container-high px-4 focus-within:outline-2 focus-within:outline-secondary">
				<Icon name="search" size={20} class="text-on-surface-variant" />
				<input
					bind:this={search}
					bind:value={filters.q}
					class="h-full min-w-0 flex-1 bg-transparent body-large text-on-surface outline-none placeholder:text-on-surface-variant"
					placeholder="Search the library"
					aria-label="Search the library"
					autocomplete="off"
					onkeydown={searchKey}
				/>
				{#if filters.q}
					<button type="button" class="state-layer flex size-8 items-center justify-center rounded-full text-on-surface-variant" aria-label="Clear the search" onclick={() => (filters.q = '')}><Icon name="close" size={18} /></button>
				{:else}
					<kbd class="hidden rounded-xs border border-outline-variant px-1.5 font-mono text-[12px] text-on-surface-variant pointer-fine:inline">/</kbd>
				{/if}
			</label>
			<div class="flex flex-wrap gap-1.5" role="group" aria-label="Filters">
				<Chip variant="filter" selected={filters.mine} onclick={() => (filters.mine = !filters.mine)}>Yours</Chip>
				<Chip variant="filter" selected={filters.credentials} onclick={() => (filters.credentials = !filters.credentials)}>Credentials</Chip>
				<Chip variant="filter" selected={filters.unused} onclick={() => (filters.unused = !filters.unused)}>Unused</Chip>
				<Chip variant="filter" selected={filters.notReady} onclick={() => (filters.notReady = !filters.notReady)}>Not ready</Chip>
				{#if filters.tag}<Chip variant="input" icon="sell" onremove={() => (filters.tag = null)}>{filters.tag}</Chip>{/if}
			</div>
			{#if noAgents}
				<p class="flex items-center gap-1.5 px-1 body-small text-on-surface-variant"><Icon name="dns" size={16} />No agents are connected, so nothing is prepared.</p>
			{:else if unprepared}
				<button
					type="button"
					class="flex items-center gap-1.5 self-start rounded-sm px-1 body-small text-on-surface-variant underline-offset-4 hover:underline"
					title="Agents get a tool the first time a job needs it; preparing does that ahead of time"
					onclick={() => (filters.notReady = true)}
				>
					<span class="size-2 rounded-full bg-outline"></span>{unprepared} of {tools.length} tools aren’t ready on every agent yet
				</button>
			{/if}
		</div>

		<!-- svelte-ignore a11y_no_noninteractive_tabindex (a tree: the arrow keys move through it) -->
		<div
			bind:this={list}
			class="-mx-1 min-h-0 flex-1 overflow-y-auto rounded-md px-1 pb-4 outline-none focus-visible:outline-2 focus-visible:outline-secondary"
			role="tree"
			tabindex="0"
			aria-label="Library"
			aria-activedescendant={cursor ? rowId(cursor) : undefined}
			onkeydown={listKey}
		>
			{#each shown as f (f.id)}
				{@const open = isOpen(f.id)}
				{@const key = f.tool ? `t:${f.id}` : 'f:'}
				{@const ready = f.tool ? readiness(f.tool) : null}
				<div class="pt-1">
					<!-- svelte-ignore a11y_click_events_have_key_events, a11y_interactive_supports_focus (the tree owns focus and the keyboard) -->
					<div
						id={rowId(key)}
						role="treeitem"
						aria-selected={selected === key}
						aria-expanded={open}
						class="state-layer flex min-h-12 cursor-pointer items-center gap-2 rounded-md pr-3 pl-1 {selected === key
							? 'bg-secondary-container text-on-secondary-container'
							: cursor === key
								? 'bg-surface-container-high text-on-surface'
								: 'text-on-surface'}"
						onclick={() => {
							list?.focus();
							cursor = key;
							if (f.tool) void select(key, true);
							else toggle(f.id);
						}}
					>
						<button
							type="button"
							tabindex="-1"
							class="flex size-8 shrink-0 items-center justify-center rounded-full text-on-surface-variant hover:bg-on-surface/8"
							aria-label={open ? `Fold ${f.name}` : `Unfold ${f.name}`}
							onclick={(e) => { e.stopPropagation(); toggle(f.id); }}
						>
							<Icon name="chevron_right" size={20} class="transition-transform duration-150 {open ? 'rotate-90' : ''}" />
						</button>
						<Icon name={f.tool ? 'handyman' : 'deployed_code'} size={20} class="shrink-0 text-on-surface-variant" />
						<span class="min-w-0 flex-1">
							<span class="block truncate title-medium">{f.name}</span>
							{#if f.tool}
								<span class="flex items-center gap-1.5 body-small text-on-surface-variant">
									{#each provision(f.tool) as p (p.label)}<span class="flex items-center gap-0.5"><Icon name={p.icon} size={13} />{p.label}</span>{/each}
								</span>
							{/if}
						</span>
						{#if ready && NOTEWORTHY.has(ready.state)}
							<span class="flex items-center gap-1.5 label-small text-on-surface-variant" title="On the connected agents">
								<span class="size-2 rounded-full {dot[ready.state]}"></span>{ready.label}
							</span>
						{/if}
						<span class="min-w-6 rounded-full bg-surface-container-highest px-1.5 text-center label-small text-on-surface-variant tabular-nums" title="{f.commands.length} commands">{f.commands.length}</span>
					</div>
					{#if open}
						<div class="flex flex-col gap-px pt-px" role="group">
							{#each f.commands as c (c.id)}
								<div class={cursor === `c:${c.id}` && selected !== `c:${c.id}` ? 'rounded-md bg-surface-container-high' : ''}>
									<CommandRow command={c} role="treeitem" id={rowId(`c:${c.id}`)} selected={selected === `c:${c.id}`} onselect={() => { list?.focus(); void select(`c:${c.id}`, true); }} />
								</div>
							{:else}
								<p class="py-2 pl-10 body-small text-on-surface-variant">{filtering(filters) ? 'No commands match here.' : 'No commands run with it yet.'}</p>
							{/each}
						</div>
					{/if}
				</div>
			{:else}
				<div class="flex flex-col items-start gap-3 px-3 py-8">
					{#if filtering(filters)}
						<p class="body-medium text-on-surface">Nothing in the library matches.</p>
						<Button variant="tonal" icon="filter_alt_off" onclick={() => (filters = { ...NO_FILTERS })}>Clear the filters</Button>
					{:else}
						<p class="body-medium text-on-surface">The library is empty.</p>
						<p class="body-small text-on-surface-variant">Add a tool, then commands that run with it. Built-ins come from the server’s <code class="font-mono">tools/</code> and <code class="font-mono">commands/</code> folders.</p>
					{/if}
				</div>
			{/each}
		</div>
	</section>

	<!-- The selected tool or command. -->
	<section
		bind:this={detail}
		tabindex="-1"
		class="min-h-0 flex-1 overflow-y-auto rounded-xl bg-surface-container-low outline-none lg:block {selected ? 'block' : 'hidden'}"
		aria-label="Details"
	>
		<div class="mx-auto flex max-w-5xl flex-col gap-2 p-4 md:p-6">
			{#if selected}
				<Button variant="text" icon="arrow_back" class="-ml-3 self-start lg:hidden" onclick={() => select(null, true)}>Library</Button>
			{/if}
			{#key selected}
				{#if selectedCommand}
					<CommandDetail
						command={selectedCommand}
						{editable}
						toolName={toolName(selectedCommand.spec.tool)}
						bind:dirty
						onsaved={savedCommand}
						ondeleted={(id) => { commands = commands.filter((x) => x.id !== id); void select(null); }}
						onduplicate={(y) => newCommand(y)}
						onselecttool={(id) => select(`t:${id}`, true)}
						ontag={(t) => (filters.tag = t)}
						onai={(cur, use) => askAi('command', cur, use)}
					/>
				{:else if selectedTool}
					<ToolDetail
						tool={selectedTool}
						{editable}
						commands={all.find((f) => f.id === selectedTool.id)?.commands ?? []}
						bind:dirty
						onsaved={savedTool}
						ondeleted={(id) => { tools = tools.filter((x) => x.id !== id); void select(null); }}
						onduplicate={(y) => newTool(y)}
						onselectcommand={(id) => select(`c:${id}`, true)}
						onnewcommand={() => newCommand(TEMPLATE(selectedTool.id))}
						ontag={(t) => (filters.tag = t)}
						onai={(cur, use) => askAi('tool', cur, use)}
						onchanged={reloadTools}
					/>
				{:else if selected === 'new:command' && draft}
					<CommandDetail command={null} {draft} {editable} bind:dirty onsaved={savedCommand} ondeleted={() => {}} onduplicate={() => {}} onselecttool={() => {}} ontag={() => {}}
						onai={(cur, use) => askAi('command', cur, use)} oncancelnew={() => { dirty = false; draft = ''; void select(null); }} />
				{:else if selected === 'new:tool' && draft}
					<ToolDetail tool={null} {draft} {editable} commands={[]} bind:dirty onsaved={savedTool} ondeleted={() => {}} onduplicate={() => {}} onselectcommand={() => {}} onnewcommand={() => {}}
						ontag={() => {}} onai={(cur, use) => askAi('tool', cur, use)} onchanged={() => {}} oncancelnew={() => { dirty = false; draft = ''; void select(null); }} />
				{:else if selected}
					<p class="py-8 body-medium text-on-surface-variant">That isn’t in the library (any more).</p>
				{:else}
					<div class="flex flex-col gap-4 py-6">
						<p class="max-w-[60ch] body-medium text-on-surface-variant">
							Each tool is something agents get ready (an image, a recipe they build, or a program on their host); its commands are what cards and playbook steps run with it. Pick one to see what it takes and gives.
						</p>
						<dl class="grid w-max grid-cols-[max-content_max-content] gap-x-4 gap-y-1.5 body-small text-on-surface-variant">
							<dt><kbd class="rounded-xs border border-outline-variant px-1.5 font-mono">/</kbd></dt><dd>search</dd>
							<dt><kbd class="rounded-xs border border-outline-variant px-1.5 font-mono">↑</kbd> <kbd class="rounded-xs border border-outline-variant px-1.5 font-mono">↓</kbd></dt><dd>move through the list</dd>
							<dt><kbd class="rounded-xs border border-outline-variant px-1.5 font-mono">←</kbd> <kbd class="rounded-xs border border-outline-variant px-1.5 font-mono">→</kbd></dt><dd>fold and unfold a tool</dd>
							<dt><kbd class="rounded-xs border border-outline-variant px-1.5 font-mono">Enter</kbd></dt><dd>open the details</dd>
							<dt><kbd class="rounded-xs border border-outline-variant px-1.5 font-mono">Esc</kbd></dt><dd>back to the list</dd>
							{#if editable}<dt><kbd class="rounded-xs border border-outline-variant px-1.5 font-mono">n</kbd></dt><dd>new command (in the selected tool)</dd>{/if}
						</dl>
					</div>
				{/if}
			{/key}
		</div>
	</section>
</div>

<AiGenerateDialog bind:open={aiOpen} kind={aiKind} current={aiCurrent} onuse={(d) => aiUse(d.yaml)} />

<Dialog bind:open={shortcutsOpen} title="Keyboard shortcuts" icon="keyboard" size="sm">
	<dl class="grid grid-cols-[max-content_minmax(0,1fr)] items-baseline gap-x-6 gap-y-2 pt-2 body-medium">
		{#each [[['/'], 'Search; Enter opens the first command that matches'], [['icon:arrow_upward', 'icon:arrow_downward'], 'Move through the list (or j and k)'], [['icon:arrow_back', 'icon:arrow_forward'], 'Fold and unfold a tool'], [['Enter'], 'Open the details'], [['Esc'], 'Back to the list'], ...(editable ? [[['n'], 'New command, in the selected tool']] : []), [['?'], 'This list']] as [keys, what] (what)}
			<dt class="flex flex-wrap gap-1">
				{#each keys as k (k)}<kbd class="flex h-6 min-w-6 items-center justify-center rounded-xs border border-outline-variant bg-surface-container px-1.5 font-mono text-[12px] text-on-surface">{#if String(k).startsWith('icon:')}<Icon name={String(k).slice(5)} size={14} /><span class="sr-only">{String(k).slice(5).replace('arrow_', '').replace('ward', '')} arrow</span>{:else}{k}{/if}</kbd>{/each}
			</dt>
			<dd class="text-on-surface-variant">{what}</dd>
		{/each}
	</dl>
	{#snippet actions()}<Button variant="text" onclick={() => (shortcutsOpen = false)}>Close</Button>{/snippet}
</Dialog>
