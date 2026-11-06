<script lang="ts">
	import type { ToolAgentState, ToolDefinition } from '$lib/api';
	import { api, ApiError, saveBlob } from '$lib/api';
	import LibraryTransfer from '$lib/components/LibraryTransfer.svelte';
	import AiGenerateDialog from '$lib/components/AiGenerateDialog.svelte';
	import { aiStatus } from '$lib/ai.svelte';
	import { Button, Card, Chip, Dialog, Icon, TopAppBar } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { canEdit } from '$lib/session';
	import { relativeTime } from '$lib/format';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let tools = $state<ToolDefinition[]>(data.tools);
	let mine = $derived(tools.filter((t) => t.origin === 'user').map((t) => t.id));
	$effect(() => void aiStatus.load());
	let aiOpen = $state(false);
	let aiFromEditor = $state(false);
	// svelte-ignore state_referenced_locally
	let loaded = data.tools;
	$effect(() => {
		if (data.tools !== loaded) {
			loaded = data.tools;
			tools = data.tools;
		}
	});

	let timer: ReturnType<typeof setTimeout> | undefined;
	async function reload() {
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
				timer = setTimeout(reload, 300);
			}
		})
	);

	const editable = $derived(canEdit());

	function agentLabel(a: ToolAgentState): { text: string; icon: string; tone: string } {
		if (a.state === 'preparing') return { text: 'preparing…', icon: 'progress_activity', tone: 'text-primary' };
		if (a.state === 'failed') return { text: 'prepare failed', icon: 'error', tone: 'text-error' };
		if (!a.way) return { text: 'can’t run it', icon: 'block', tone: 'text-on-surface-variant' };
		if (a.ready) return { text: a.way === 'build' ? 'built, ready' : 'ready', icon: 'check_circle', tone: 'text-primary' };
		if (a.way === 'build') return { text: 'builds on first use', icon: 'construction', tone: 'text-on-surface-variant' };
		if (a.way === 'image') return { text: 'pulls the image', icon: 'download', tone: 'text-on-surface-variant' };
		return { text: 'on its host', icon: 'computer', tone: 'text-on-surface-variant' };
	}

	let preparing = $state<string | null>(null);
	async function prepare(t: ToolDefinition) {
		preparing = t.id;
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
			await reload();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not prepare the tool');
		} finally {
			preparing = null;
		}
	}

	// ---- editor ----
	const TEMPLATE = `id: my-tool
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
	let editorOpen = $state(false);
	let editing = $state<ToolDefinition | null>(null);
	let readOnly = $state(false);
	let yaml = $state('');
	let error = $state<string | null>(null);
	let saving = $state(false);
	let preview = $state<{ tag: string | null; dockerfile: string | null } | null>(null);

	function openNew(text = TEMPLATE) {
		editing = null;
		readOnly = false;
		yaml = text;
		error = null;
		preview = null;
		editorOpen = true;
	}
	function openExisting(t: ToolDefinition) {
		editing = t;
		readOnly = t.origin === 'builtin' || !editable;
		yaml = t.source_yaml;
		error = null;
		preview = null;
		editorOpen = true;
	}
	function duplicate(t: ToolDefinition) {
		openNew(t.source_yaml.replace(/^id:\s*.+$/m, `id: ${t.id}-custom`).replace(/^name:\s*(.+)$/m, 'name: $1 (custom)'));
	}
	async function check() {
		const res = await api().tools.validate(yaml);
		error = res.ok ? null : res.errors.join('; ');
		preview = res.ok ? { tag: res.recipe_tag ?? null, dockerfile: res.dockerfile ?? null } : null;
	}
	async function save() {
		saving = true;
		error = null;
		try {
			const saved = editing ? await api().tools.update(editing.id, yaml) : await api().tools.create(yaml);
			tools = [...tools.filter((t) => t.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name));
			editorOpen = false;
			snackbar.show(`Saved ${saved.name}`);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}
	async function remove(t: ToolDefinition) {
		const ok = await confirmAction({ title: `Delete ${t.name}?`, message: 'Commands that use it must be changed first. Images already built on agents stay until removed there.', confirmLabel: 'Delete tool' });
		if (!ok) return;
		try {
			await api().tools.remove(t.id);
			tools = tools.filter((x) => x.id !== t.id);
			editorOpen = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete');
		}
	}
</script>

<svelte:head><title>Tools · Castor</title></svelte:head>

<TopAppBar title="Tools" subtitle="What commands run with, prepared on agents when needed">
	{#snippet actions()}
		<LibraryTransfer noun="tools" request={{ tools: mine, label: 'castor-tools' }} count={mine.length} onimported={async () => (tools = await api().tools.list())} />
		{#if editable && aiStatus.enabled}<Button variant="text" icon="auto_awesome" class="ml-1" onclick={() => { aiFromEditor = false; aiOpen = true; }}>Generate</Button>{/if}
		{#if editable}<Button icon="add" class="ml-2" onclick={() => openNew()}>New tool</Button>{/if}
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-8">
	<p class="mb-4 max-w-3xl body-medium text-on-surface-variant">
		Agents stay bare: a command names a tool, and the agent running it gets the tool first. It pulls a published image, builds a recipe once and keeps it (rebuilt only when
		the recipe changes), or checks it on its own host. <strong>Prepare</strong> does that ahead of time, so the first job doesn't wait.
	</p>
	<div class="grid grid-cols-[repeat(auto-fill,minmax(380px,1fr))] gap-4">
		{#each tools as t (t.id)}
			<Card variant="elevated" class="flex flex-col gap-3 p-5" id={t.id}>
				<div class="flex items-start gap-3">
					<span class="flex size-10 shrink-0 items-center justify-center rounded-md bg-primary-container text-on-primary-container"><Icon name="handyman" /></span>
					<div class="min-w-0 flex-1">
						<h2 class="truncate title-medium text-on-surface">{t.name}</h2>
						<p class="truncate font-mono text-[12px] text-on-surface-variant">{t.id}</p>
					</div>
					<span class="rounded-full px-2 py-0.5 label-small {t.origin === 'builtin' ? 'bg-surface-container-highest text-on-surface-variant' : 'bg-tertiary-container text-on-tertiary-container'}">{t.origin === 'builtin' ? 'built-in' : 'yours'}</span>
				</div>
				{#if t.description}<p class="body-medium text-on-surface-variant">{t.description}</p>{/if}

				<ul class="flex flex-col gap-1.5 rounded-md bg-surface-container p-3 body-small">
					{#if t.spec.image}
						<li class="flex items-start gap-2"><Icon name="download" size={16} class="mt-px text-on-surface-variant" /><span class="text-on-surface-variant">Image</span><code class="min-w-0 truncate font-mono text-on-surface" title={t.spec.image}>{t.spec.image}</code></li>
					{/if}
					{#if t.spec.build}
						<li class="flex items-start gap-2">
							<Icon name="construction" size={16} class="mt-px text-on-surface-variant" /><span class="text-on-surface-variant">Recipe</span>
							<span class="min-w-0">
								<code class="block truncate font-mono text-on-surface">on {t.spec.build.base}</code>
								<code class="block truncate font-mono text-on-surface-variant" title="Built and cached as this tag; any change to the recipe makes a new one">{t.recipe_tag}</code>
							</span>
						</li>
					{/if}
					{#if t.spec.host}
						<li class="flex items-start gap-2"><Icon name="computer" size={16} class="mt-px text-on-surface-variant" /><span class="text-on-surface-variant">Host</span><code class="min-w-0 truncate font-mono text-on-surface" title={t.spec.host.check}>{t.spec.host.check}</code></li>
					{/if}
				</ul>

				<div>
					<p class="mb-1 label-medium text-on-surface-variant">Agents</p>
					{#if t.agents.length}
						<ul class="flex flex-col gap-1">
							{#each t.agents as a (a.agent_id)}
								{@const l = agentLabel(a)}
								<li class="flex items-center gap-2 body-small">
									<Icon name="dns" size={16} class="text-on-surface-variant" />
									<span class="min-w-0 flex-1 truncate text-on-surface">{a.agent_name}</span>
									<span class="flex items-center gap-1 {l.tone}" title={a.error ?? (a.at ? `prepared ${relativeTime(a.at)}` : '')}>
										<Icon name={l.icon} size={15} class={a.state === 'preparing' ? 'animate-spin' : ''} />{l.text}
									</span>
								</li>
								{#if a.state === 'failed' && a.error}<li class="pl-6 body-small break-words text-error">{a.error}</li>{/if}
							{/each}
						</ul>
					{:else}
						<p class="body-small text-on-surface-variant">No agents connected.</p>
					{/if}
				</div>

				{#if t.used_by.length}
					<div class="flex flex-wrap items-center gap-1">
						<span class="mr-1 label-medium text-on-surface-variant">Used by</span>
						{#each t.used_by as c (c)}<Chip variant="suggestion">{c}</Chip>{/each}
					</div>
				{/if}

				<div class="mt-auto flex flex-wrap items-center gap-2 pt-1">
					{#if editable}
						<Button variant="tonal" icon="rocket_launch" loading={preparing === t.id} disabled={!t.agents.some((a) => a.way)} onclick={() => prepare(t)}>Prepare on agents</Button>
					{/if}
					<Button variant="text" icon="code" onclick={() => openExisting(t)}>{t.origin === 'builtin' || !editable ? 'View' : 'Edit'}</Button>
					{#if t.spec.homepage}<a href={t.spec.homepage} target="_blank" rel="noopener noreferrer" class="ml-auto body-small text-primary hover:underline">homepage</a>{/if}
				</div>
			</Card>
		{:else}
			<p class="body-medium text-on-surface-variant">No tools yet.</p>
		{/each}
	</div>
</div>

<Dialog bind:open={editorOpen} title={editing ? editing.name : 'New tool'} icon="handyman" size="lg">
	<div class="grid gap-4 pt-2 pb-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
		<textarea
			class="min-h-[22rem] resize-y rounded-md border border-outline-variant bg-surface-container-lowest p-3 font-mono text-[12.5px] leading-5 text-on-surface outline-none focus:border-primary"
			spellcheck="false"
			readonly={readOnly}
			bind:value={yaml}
			oninput={() => (preview = null)}
		></textarea>
		<div class="flex flex-col gap-2">
			{#if readOnly && editing?.origin === 'builtin'}
				<p class="body-small text-on-surface-variant">Built-in tools are read-only (they come from <code class="font-mono">tools/{editing.id}.yaml</code>). Duplicate one to change it.</p>
			{/if}
			{#if error}<p class="rounded-md bg-error-container/40 px-3 py-2 body-small break-words text-on-surface">{error}</p>{/if}
			{#if preview?.dockerfile}
				<p class="label-medium text-on-surface-variant">Agents build this as <code class="font-mono">{preview.tag}</code></p>
				<pre class="overflow-auto rounded-md bg-surface-container-highest p-3 font-mono text-[12px] text-on-surface">{preview.dockerfile}</pre>
			{:else if preview}
				<p class="body-small text-on-surface-variant">Looks right. (No recipe, so nothing is built.)</p>
			{/if}
			<p class="body-small text-on-surface-variant">
				The install and check scripts run with <code class="font-mono">sh -e</code> during the build, so a failing step fails the build instead of a job.
			</p>
		</div>
	</div>
	{#snippet actions()}
		{#if editing && editing.origin === 'user' && editable}<Button variant="text" icon="delete" class="mr-auto" onclick={() => editing && remove(editing)}>Delete</Button>{/if}
		{#if editing && editable}<Button variant="text" icon="content_copy" onclick={() => editing && duplicate(editing)}>Duplicate</Button>{/if}
		{#if editing}<Button variant="text" icon="download" onclick={() => editing && saveBlob(editing.source_yaml, `${editing.id}.yaml`)}>Export YAML</Button>{/if}
		{#if !readOnly && aiStatus.enabled}<Button variant="text" icon="auto_awesome" onclick={() => { aiFromEditor = true; aiOpen = true; }}>AI</Button>{/if}
		<Button variant="text" onclick={check}>Check</Button>
		<Button variant="text" onclick={() => (editorOpen = false)}>{readOnly ? 'Close' : 'Cancel'}</Button>
		{#if !readOnly}<Button icon="check" loading={saving} onclick={save}>{editing ? 'Save' : 'Create'}</Button>{/if}
	{/snippet}
</Dialog>

<AiGenerateDialog bind:open={aiOpen} kind="tool" current={aiFromEditor ? yaml : ''} onuse={(d) => (aiFromEditor ? (yaml = d.yaml) : openNew(d.yaml))} />
