<script lang="ts">
	import type { CommandDefinition, ToolDefinition } from '$lib/api';
	import { api, ApiError, saveBlob } from '$lib/api';
	import { Button, Icon, Tabs } from '$lib/m3';
	import { aiStatus } from '$lib/ai.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime } from '$lib/format';
	import { agentState, readiness } from '$lib/library';
	import CommandRow from './CommandRow.svelte';
	import YamlPane from './YamlPane.svelte';

	/** A tool in the Library's detail pane: how agents get it, whether they have it, the commands
	 *  that run with it, and its YAML. `tool` null is a new one, starting from `draft`. */
	interface Props {
		tool: ToolDefinition | null;
		draft?: string;
		editable: boolean;
		commands: CommandDefinition[];
		dirty?: boolean;
		onsaved: (t: ToolDefinition) => void;
		ondeleted: (id: string) => void;
		onduplicate: (yaml: string) => void;
		onselectcommand: (id: string) => void;
		onnewcommand: () => void;
		ontag: (tag: string) => void;
		onai: (current: string, use: (yaml: string) => void) => void;
		onchanged: () => void;
		oncancelnew?: () => void;
	}
	let { tool: t, draft = '', editable, commands, dirty = $bindable(false), onsaved, ondeleted, onduplicate, onselectcommand, onnewcommand, ontag, onai, onchanged, oncancelnew }: Props = $props();

	// svelte-ignore state_referenced_locally
	const original = t?.source_yaml ?? draft;
	// svelte-ignore state_referenced_locally
	let tab = $state(t ? 'overview' : 'yaml');
	let yaml = $state(original);
	let error = $state<string | null>(null);
	let saving = $state(false);
	let preview = $state<{ tag: string | null; dockerfile: string | null } | null>(null);
	let readOnly = $derived(!editable || t?.origin === 'builtin');
	$effect(() => {
		dirty = yaml !== original;
	});
	let ready = $derived(t ? readiness(t) : null);

	let preparing = $state(false);
	async function prepare() {
		if (!t) return;
		preparing = true;
		try {
			const res = await api().tools.prepare(t.id);
			const skipped = Object.keys(res.skipped);
			snackbar.show(
				res.started.length
					? `Preparing ${t.name} on ${res.started.join(', ')}${skipped.length ? ` (skipped ${skipped.join(', ')})` : ''}`
					: skipped.length
						? `No agent can run ${t.name}: ${Object.values(res.skipped)[0]}`
						: 'No agents are connected'
			);
			onchanged();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not prepare the tool');
		} finally {
			preparing = false;
		}
	}

	async function check() {
		const res = await api().tools.validate(yaml);
		error = res.ok ? null : res.errors.join('; ');
		preview = res.ok ? { tag: res.recipe_tag ?? null, dockerfile: res.dockerfile ?? null } : null;
	}
	async function save() {
		if (readOnly) return;
		saving = true;
		error = null;
		try {
			const saved = t ? await api().tools.update(t.id, yaml) : await api().tools.create(yaml);
			snackbar.show(`Saved ${saved.name}`);
			dirty = false;
			onsaved(saved);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}
	async function remove() {
		if (!t) return;
		const ok = await confirmAction({
			title: `Delete ${t.name}?`,
			message: 'Commands that use it must be changed first. Images already built on agents stay until removed there.',
			confirmLabel: 'Delete tool',
			danger: true
		});
		if (!ok) return;
		try {
			await api().tools.remove(t.id);
			dirty = false;
			ondeleted(t.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete');
		}
	}
	const duplicateYaml = (src: string, id: string) => src.replace(/^id:\s*.+$/m, `id: ${id}-custom`).replace(/^name:\s*(.+)$/m, 'name: $1 (custom)');
	const tone = { good: 'text-primary', busy: 'text-primary', bad: 'text-error', idle: 'text-on-surface-variant' };
</script>

<div class="flex flex-col gap-5">
	<header class="flex flex-wrap items-start gap-x-4 gap-y-3">
		<span class="flex size-12 shrink-0 items-center justify-center rounded-lg bg-secondary-container text-on-secondary-container"><Icon name="handyman" /></span>
		<div class="min-w-0 flex-1">
			<h2 class="title-large text-on-surface">{t?.name ?? 'New tool'}</h2>
			{#if t}
				<p class="flex flex-wrap items-center gap-x-2 body-small text-on-surface-variant">
					<code class="font-mono">{t.id}</code>
					<span aria-hidden="true">·</span>
					<span>{t.origin === 'builtin' ? 'built-in' : 'yours'}</span>
					{#if t.spec.homepage}
						<span aria-hidden="true">·</span>
						<a href={t.spec.homepage} target="_blank" rel="noopener noreferrer" class="text-primary underline-offset-4 hover:underline">homepage</a>
					{/if}
				</p>
			{/if}
		</div>
		{#if t}
			<div class="flex flex-wrap items-center gap-1">
				<Button variant="text" icon="download" onclick={() => saveBlob(t.source_yaml, `${t.id}.yaml`)}>Export</Button>
				{#if editable}
					{#if t.origin === 'user'}<Button variant="text" icon="delete" onclick={remove}>Delete</Button>{/if}
					<Button variant="text" icon="content_copy" onclick={() => onduplicate(duplicateYaml(t.source_yaml, t.id))}>Duplicate</Button>
					{#if t.origin === 'user'}<Button variant="text" icon="edit" onclick={() => (tab = 'yaml')}>Edit</Button>{/if}
					<Button variant="tonal" icon="rocket_launch" loading={preparing} disabled={!t.agents.some((a) => a.way)} onclick={prepare}>Prepare on agents</Button>
				{/if}
			</div>
		{/if}
	</header>

	{#if t}
		<Tabs tabs={[{ id: 'overview', label: 'Overview' }, { id: 'yaml', label: dirty ? 'YAML (edited)' : 'YAML' }]} bind:value={tab} />
	{/if}

	{#if t && tab === 'overview'}
		{#if t.description}<p class="max-w-[70ch] body-medium text-on-surface">{t.description}</p>{/if}

		<section class="flex flex-col gap-2">
			<h3 class="title-small text-on-surface">How agents get it</h3>
			<p class="max-w-[70ch] body-small text-on-surface-variant">Agents stay bare: they pull a published image first, then build the recipe once and keep it, then check their own host.</p>
			<dl class="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-2 rounded-md bg-surface-container p-3 body-small">
				{#if t.spec.image}
					<dt class="flex items-center gap-1.5 text-on-surface-variant"><Icon name="download" size={16} />Image</dt>
					<dd><code class="font-mono text-[12.5px] break-all text-on-surface">{t.spec.image}</code></dd>
				{/if}
				{#if t.spec.build}
					<dt class="flex items-center gap-1.5 text-on-surface-variant"><Icon name="construction" size={16} />Recipe</dt>
					<dd class="flex min-w-0 flex-col gap-1">
						<span>on <code class="font-mono text-[12.5px] text-on-surface">{t.spec.build.base}</code>, cached as <code class="font-mono text-[12.5px] break-all text-on-surface" title="Any change to the recipe makes a new tag">{t.recipe_tag}</code></span>
						<pre class="overflow-x-auto rounded-xs bg-surface-container-highest p-2 font-mono text-[12px] leading-5 whitespace-pre text-on-surface">{t.spec.build.install.trim()}</pre>
						<span class="text-on-surface-variant">check: <code class="font-mono text-[12px] text-on-surface">{t.spec.build.check}</code></span>
					</dd>
				{/if}
				{#if t.spec.host}
					<dt class="flex items-center gap-1.5 text-on-surface-variant"><Icon name="computer" size={16} />Host</dt>
					<dd><code class="font-mono text-[12.5px] text-on-surface">{t.spec.host.check}</code></dd>
				{/if}
			</dl>
		</section>

		<section class="flex flex-col gap-2">
			<h3 class="flex items-baseline gap-2 title-small text-on-surface">Agents {#if ready}<span class="font-normal text-on-surface-variant">{ready.label}</span>{/if}</h3>
			{#if t.agents.length}
				<ul class="flex flex-col divide-y divide-outline-variant/60 rounded-md border border-outline-variant">
					{#each t.agents as a (a.agent_id)}
						{@const l = agentState(a)}
						<li class="flex flex-col gap-1 px-3 py-2 body-small">
							<span class="flex items-center gap-2">
								<Icon name="dns" size={16} class="text-on-surface-variant" />
								<span class="min-w-0 flex-1 truncate text-on-surface">{a.agent_name}</span>
								<span class="flex items-center gap-1 {tone[l.tone]}">
									<Icon name={l.icon} size={16} class={a.state === 'preparing' ? 'animate-spin' : ''} />{l.text}
								</span>
								{#if a.at}<span class="text-on-surface-variant" title="Last prepared">{relativeTime(a.at)}</span>{/if}
							</span>
							{#if a.state === 'failed' && a.error}<span class="pl-6 break-words text-error">{a.error}</span>{/if}
						</li>
					{/each}
				</ul>
			{:else}
				<p class="body-small text-on-surface-variant">No agents are connected, so nothing is prepared yet.</p>
			{/if}
		</section>

		<section class="flex flex-col gap-2">
			<h3 class="flex items-center gap-2 title-small text-on-surface">
				Commands <span class="font-normal text-on-surface-variant">{commands.length}</span>
				{#if editable}<Button variant="text" icon="add" class="ml-auto" onclick={onnewcommand}>New command</Button>{/if}
			</h3>
			{#if commands.length}
				<div class="flex flex-col gap-0.5" role="listbox" aria-label="Commands that run with {t.name}">
					{#each commands as c (c.id)}<CommandRow command={c} nested={false} onselect={() => onselectcommand(c.id)} />{/each}
				</div>
			{:else}
				<p class="body-small text-on-surface-variant">No command runs with it yet.</p>
			{/if}
		</section>

		{#if t.spec.tags.length}
			<div class="flex flex-wrap items-center gap-1">
				<span class="mr-1 label-large text-on-surface-variant">Tags</span>
				{#each t.spec.tags as tag (tag)}
					<button type="button" class="state-layer rounded-sm border border-outline-variant px-2 py-0.5 label-medium text-on-surface-variant" title="Show everything tagged {tag}" onclick={() => ontag(tag)}>{tag}</button>
				{/each}
			</div>
		{/if}
	{:else}
		<YamlPane bind:value={yaml} readonly={readOnly} {error} label="Tool definition (YAML)" onsave={save}>
			{#snippet aside()}
				{#if readOnly}
					<p class="body-small text-on-surface-variant">
						{#if t?.origin === 'builtin'}Built-in tools come from <code class="font-mono">tools/{t.id}.yaml</code> and are read-only here. Duplicate one to change it.{:else}You can view this but not change it.{/if}
					</p>
				{:else}
					<p class="body-small text-on-surface-variant">The install and check scripts run with <code class="font-mono">sh -e</code> during the build, so a failing step fails the build instead of a job. Tab indents; Ctrl+S saves.</p>
				{/if}
				{#if preview?.dockerfile}
					<p class="label-medium text-on-surface-variant">Agents build this as <code class="font-mono">{preview.tag}</code></p>
					<pre class="overflow-auto rounded-md bg-surface-container-highest p-3 font-mono text-[12px] whitespace-pre text-on-surface">{preview.dockerfile}</pre>
				{:else if preview}
					<p class="body-small text-on-surface-variant">Looks right. (No recipe, so nothing is built.)</p>
				{/if}
			{/snippet}
		</YamlPane>
		<div class="flex flex-wrap items-center justify-end gap-2">
			<Button variant="text" icon="fact_check" onclick={check}>Check</Button>
			{#if !readOnly}
				{#if aiStatus.enabled}<Button variant="text" icon="auto_awesome" onclick={() => onai(yaml, (y) => (yaml = y))}>Ask AI</Button>{/if}
				{#if t}
					<Button variant="text" disabled={!dirty} onclick={() => { yaml = original; error = null; preview = null; }}>Discard changes</Button>
				{:else}
					<Button variant="text" onclick={oncancelnew}>Cancel</Button>
				{/if}
				<Button icon="check" loading={saving} disabled={!!t && !dirty} onclick={save}>{t ? 'Save' : 'Create tool'}</Button>
			{/if}
		</div>
	{/if}
</div>
