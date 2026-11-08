<script lang="ts">
	import type { CommandDefinition, CommandUsage } from '$lib/api';
	import { api, ApiError, saveBlob } from '$lib/api';
	import { Button, Icon, Tabs } from '$lib/m3';
	import { aiStatus } from '$lib/ai.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { commandParts } from '$lib/library';
	import YamlPane from './YamlPane.svelte';

	/** A command in the Library's detail pane: what it takes, runs and gives, who uses it, and
	 *  its YAML (where it is edited). `command` null is a new one, starting from `draft`. */
	interface Props {
		command: CommandDefinition | null;
		draft?: string;
		editable: boolean;
		toolName?: string | null;
		dirty?: boolean;
		onsaved: (c: CommandDefinition) => void;
		ondeleted: (id: string) => void;
		onduplicate: (yaml: string) => void;
		onselecttool: (id: string) => void;
		ontag: (tag: string) => void;
		onai: (current: string, use: (yaml: string) => void) => void;
		oncancelnew?: () => void;
	}
	let { command: c, draft = '', editable, toolName = null, dirty = $bindable(false), onsaved, ondeleted, onduplicate, onselecttool, ontag, onai, oncancelnew }: Props = $props();

	// svelte-ignore state_referenced_locally
	const original = c?.source_yaml ?? draft;
	// svelte-ignore state_referenced_locally
	let tab = $state(c ? 'overview' : 'yaml');
	let yaml = $state(original);
	let error = $state<string | null>(null);
	let saving = $state(false);
	let readOnly = $derived(!editable || c?.origin === 'builtin');
	$effect(() => {
		dirty = yaml !== original;
	});

	let usage = $state<CommandUsage | null>(null);
	$effect(() => {
		if (c && (c.card_count || c.playbooks.length)) api().commands.usage(c.id).then((u) => (usage = u)).catch(() => {});
	});

	async function save() {
		if (readOnly) return;
		saving = true;
		error = null;
		try {
			const saved = c ? await api().commands.update(c.id, yaml) : await api().commands.create(yaml);
			snackbar.show(c ? `Saved “${saved.name}”` : `Added “${saved.name}”`);
			dirty = false;
			onsaved(saved);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}
	async function remove() {
		if (!c) return;
		const ok = await confirmAction({
			title: `Delete “${c.name}”?`,
			message: 'The command is removed from the library.' + (c.in_use ? '\nCards that use it keep their current command line as a plain shell command.' : ''),
			confirmLabel: 'Delete command',
			danger: true
		});
		if (!ok) return;
		try {
			await api().commands.remove(c.id);
			dirty = false;
			ondeleted(c.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete');
		}
	}
	const duplicateYaml = (src: string, id: string) => src.replace(/^id:\s*.+$/m, `id: ${id}-custom`).replace(/^name:\s*(.+)$/m, 'name: $1 (custom)');
	const show = (v: unknown) => (v === null || v === undefined || v === '' ? null : Array.isArray(v) ? v.join(', ') : String(v));
</script>

<div class="flex flex-col gap-5">
	<header class="flex flex-wrap items-start gap-x-4 gap-y-3">
		<span class="flex size-12 shrink-0 items-center justify-center rounded-lg bg-primary-container text-on-primary-container"><Icon name="terminal" /></span>
		<div class="min-w-0 flex-1">
			<h2 class="title-large text-on-surface">{c?.name ?? 'New command'}</h2>
			{#if c}
				<p class="flex flex-wrap items-center gap-x-2 body-small text-on-surface-variant">
					<code class="font-mono">{c.id}</code>
					<span aria-hidden="true">·</span>
					<span>{c.origin === 'builtin' ? 'built-in' : 'yours'}</span>
					{#if c.version > 1}<span aria-hidden="true">·</span><span>version {c.version}</span>{/if}
				</p>
			{/if}
		</div>
		{#if c}
			<div class="flex flex-wrap items-center gap-1">
				<Button variant="text" icon="download" onclick={() => saveBlob(c.source_yaml, `${c.id}.yaml`)}>Export</Button>
				{#if editable}
					{#if c.origin === 'user'}
						<Button variant="text" icon="delete" onclick={remove}>Delete</Button>
						<Button variant="text" icon="content_copy" onclick={() => onduplicate(duplicateYaml(c.source_yaml, c.id))}>Duplicate</Button>
						<Button variant="tonal" icon="edit" onclick={() => (tab = 'yaml')}>Edit</Button>
					{:else}
						<Button variant="tonal" icon="content_copy" onclick={() => onduplicate(duplicateYaml(c.source_yaml, c.id))}>Duplicate to edit</Button>
					{/if}
				{/if}
			</div>
		{/if}
	</header>

	{#if c}
		<Tabs
			tabs={[
				{ id: 'overview', label: 'Overview' },
				{ id: 'yaml', label: readOnly ? 'YAML' : dirty ? 'YAML (edited)' : 'YAML' }
			]}
			bind:value={tab}
		/>
	{/if}

	{#if c && tab === 'overview'}
		{#if c.description}<p class="max-w-[70ch] body-medium text-on-surface">{c.description}</p>{/if}

		<dl class="grid grid-cols-[max-content_minmax(0,1fr)] items-baseline gap-x-6 gap-y-2 body-medium">
			<dt class="label-large text-on-surface-variant">Runs with</dt>
			<dd class="min-w-0">
				{#if c.spec.tool}
					<button type="button" class="font-medium text-primary underline-offset-4 hover:underline" onclick={() => c.spec.tool && onselecttool(c.spec.tool)}>{toolName ?? c.spec.tool}</button>
					<span class="text-on-surface-variant">(tool)</span>
				{:else if c.spec.image}
					<code class="font-mono text-[12.5px] break-all">{c.spec.image}</code>
				{:else}
					<span class="text-on-surface-variant">the agent’s shell (needs <code class="font-mono">--allow-host</code>)</span>
				{/if}
			</dd>
			{#if c.spec.container_flags?.length}
				<dt class="label-large text-on-surface-variant">Container flags</dt>
				<dd class="flex flex-wrap gap-1">{#each c.spec.container_flags as f (f)}<code class="rounded-xs bg-surface-container-highest px-1.5 font-mono text-[12px]">{f}</code>{/each}</dd>
			{/if}
			{#if c.spec.tty}
				<dt class="label-large text-on-surface-variant">Terminal</dt>
				<dd>Interactive: runs in a terminal people can take control of</dd>
			{/if}
			{#if c.tags.length}
				<dt class="label-large text-on-surface-variant">Tags</dt>
				<dd class="flex flex-wrap gap-1">
					{#each c.tags as t (t)}
						<button type="button" class="state-layer rounded-sm border border-outline-variant px-2 py-0.5 label-medium text-on-surface-variant" title="Show everything tagged {t}" onclick={() => ontag(t)}>{t}</button>
					{/each}
				</dd>
			{/if}
		</dl>

		<section class="flex flex-col gap-2">
			<h3 class="title-small text-on-surface">Command line</h3>
			<pre class="overflow-x-auto rounded-md bg-surface-container-highest p-3 font-mono text-[12.5px] leading-5 whitespace-pre-wrap text-on-surface">{#each commandParts(c.spec.command) as part, i (i)}{#if part.arg}<span class="rounded-xs bg-primary-container px-0.5 text-on-primary-container">{part.text}</span>{:else}{part.text}{/if}{/each}</pre>
			<p class="body-small text-on-surface-variant">Values are inserted shell-quoted, so each one is always a single word.</p>
		</section>

		<section class="flex flex-col gap-2">
			<h3 class="title-small text-on-surface">Arguments <span class="font-normal text-on-surface-variant">{c.spec.args.length}</span></h3>
			{#if c.spec.args.length}
				<div class="overflow-x-auto rounded-md border border-outline-variant">
					<table class="w-full body-small">
						<thead class="bg-surface-container text-left label-medium text-on-surface-variant">
							<tr><th class="px-3 py-2 font-medium">Name</th><th class="px-3 py-2 font-medium">Type</th><th class="px-3 py-2 font-medium">Default or example</th><th class="px-3 py-2 font-medium">What it is</th></tr>
						</thead>
						<tbody>
							{#each c.spec.args as a (a.name)}
								<tr class="border-t border-outline-variant/60 align-top">
									<td class="px-3 py-2 whitespace-nowrap">
										<code class="font-mono text-[12.5px] text-on-surface">{a.name}</code>
										{#if a.required}<span class="ml-1 text-error" title="Required">*</span>{/if}
									</td>
									<td class="px-3 py-2 whitespace-nowrap text-on-surface-variant">{a.type}{#if a.choices?.length}<span class="block">one of {a.choices.join(', ')}</span>{/if}</td>
									<td class="px-3 py-2 font-mono text-[12px] text-on-surface-variant">
										{#if show(a.default)}{show(a.default)}{:else if show(a.example)}<span class="italic">e.g. {show(a.example)}</span>{:else}—{/if}
									</td>
									<td class="max-w-[40ch] px-3 py-2 text-on-surface-variant">{a.description || '—'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<p class="body-small text-on-surface-variant"><span class="text-error">*</span> required</p>
			{:else}
				<p class="body-small text-on-surface-variant">None: it always runs the same command line.</p>
			{/if}
		</section>

		{#if c.spec.vars?.length}
			<section class="flex flex-col gap-2">
				<h3 class="title-small text-on-surface">Credentials and variables</h3>
				<ul class="flex flex-col gap-1.5">
					{#each c.spec.vars as v (v.name)}
						<li class="flex flex-wrap items-baseline gap-x-2 body-small">
							<Icon name="key" size={16} class="translate-y-0.5 text-on-surface-variant" />
							<code class="font-mono text-[12.5px] text-on-surface">{v.name}</code>
							<span class="text-on-surface-variant">a {v.kind} set{v.required ? '' : ' (optional)'}{v.description ? ` · ${v.description}` : ''}</span>
						</li>
					{/each}
				</ul>
				<p class="body-small text-on-surface-variant">Values come from variable sets at run time; they never appear in the command line or the job history.</p>
			</section>
		{/if}

		<section class="flex flex-col gap-2">
			<h3 class="title-small text-on-surface">Outputs <span class="font-normal text-on-surface-variant">{c.spec.outputs.length}</span></h3>
			{#if c.spec.outputs.length}
				<ul class="flex flex-col divide-y divide-outline-variant/60 rounded-md border border-outline-variant">
					{#each c.spec.outputs as o (o.name)}
						<li class="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 body-small">
							<code class="font-mono text-[12.5px] text-on-surface">{o.name}</code>
							{#if o.type}<span class="rounded-xs bg-tertiary-container px-1.5 font-mono text-[11.5px] text-on-tertiary-container">{o.type}</span>{/if}
							<span class="text-on-surface-variant">{o.parser ? `parsed by ${o.parser}` : 'kept as a file'}</span>
							<code class="ml-auto font-mono text-[12px] text-on-surface-variant">{o.source ?? 'stdout'}</code>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="body-small text-on-surface-variant">Nothing is parsed; the console output is the result.</p>
			{/if}
		</section>

		<section class="flex flex-col gap-2">
			<h3 class="title-small text-on-surface">Used by</h3>
			{#if !c.card_count && !c.playbooks.length}
				<p class="body-small text-on-surface-variant">No cards or playbooks you can see use it yet.</p>
			{:else if !usage}
				<p class="body-small text-on-surface-variant">Loading…</p>
			{:else}
				{#if usage.playbooks.length}
					<div class="flex flex-wrap items-center gap-2">
						<span class="label-large text-on-surface-variant">Playbooks</span>
						{#each usage.playbooks as p (p.id)}
							<a href="/playbooks/edit/{p.id}" class="state-layer flex items-center gap-1 rounded-sm border border-outline-variant px-2 py-1 label-large text-on-surface"><Icon name="account_tree" size={16} class="text-on-surface-variant" />{p.name}</a>
						{/each}
					</div>
				{/if}
				{#if usage.cards.length}
					<ul class="flex flex-col">
						{#each usage.cards as t (t.id)}
							<li>
								<a href="/projects/{t.project_id}?task={t.id}" class="state-layer flex items-center gap-3 rounded-sm px-2 py-1.5 body-medium text-on-surface">
									<Icon name="view_kanban" size={18} class="text-on-surface-variant" />
									<span class="min-w-0 flex-1 truncate">{t.title}</span>
									<span class="label-medium text-primary">{t.project_codename}</span>
								</a>
							</li>
						{/each}
					</ul>
					{#if usage.card_count > usage.cards.length}<p class="body-small text-on-surface-variant">and {usage.card_count - usage.cards.length} more cards</p>{/if}
				{/if}
			{/if}
		</section>
	{:else}
		<YamlPane bind:value={yaml} readonly={readOnly} {error} label="Command definition (YAML)" onsave={save}>
			{#snippet aside()}
				{#if readOnly}
					<p class="body-small text-on-surface-variant">
						{#if c?.origin === 'builtin'}Built-in commands come from <code class="font-mono">commands/{c.id}.yaml</code> and are read-only here. Duplicate one to make your own.{:else}You can view this but not change it.{/if}
					</p>
				{:else}
					<p class="body-small text-on-surface-variant">Arguments are inserted shell-quoted with <code class="font-mono">{'{{ name }}'}</code>. Name a tool with <code class="font-mono">tool:</code>. Tab indents; Ctrl+S saves.</p>
				{/if}
			{/snippet}
		</YamlPane>
		{#if !readOnly}
			<div class="flex flex-wrap items-center justify-end gap-2">
				{#if aiStatus.enabled}<Button variant="text" icon="auto_awesome" onclick={() => onai(yaml, (y) => (yaml = y))}>Ask AI</Button>{/if}
				{#if c}
					<Button variant="text" disabled={!dirty} onclick={() => { yaml = original; error = null; }}>Discard changes</Button>
				{:else}
					<Button variant="text" onclick={oncancelnew}>Cancel</Button>
				{/if}
				<Button icon="check" loading={saving} disabled={!!c && !dirty} onclick={save}>{c ? 'Save' : 'Add command'}</Button>
			</div>
		{/if}
	{/if}
</div>
