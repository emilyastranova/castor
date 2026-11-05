<script lang="ts">
	import { canEdit } from '$lib/session';
	import type { CommandDefinition } from '$lib/api';
	import { api, ApiError, saveBlob } from '$lib/api';
	import LibraryTransfer from '$lib/components/LibraryTransfer.svelte';
	import { Button, Card, Chip, Dialog, Icon, TextField, TopAppBar } from '$lib/m3';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { page } from '$app/state';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let commands = $state<CommandDefinition[]>(data.commands);
	let filter = $state('');
	let mine = $derived(commands.filter((c) => c.origin === 'user').map((c) => c.id));

	const TEMPLATE = `id: my-command
name: My command
description: What it does
tool: nmap                             # a tool from Tools (or: image: <ref>); omit both to run in the agent shell
container_flags:                       # optional; e.g. ["--privileged", "--network=bridge"]
  - "--privileged"
args:
  - name: message
    type: string            # string | number | boolean | list
    required: true
    example: hello world
command: echo {{ message }} > /work/artifacts/result.txt
outputs:
  - name: result
    source: /work/artifacts/result.txt
`;

	// Editor dialog state
	let editorOpen = $state(false);
	let editing = $state<CommandDefinition | null>(null); // null = new
	let readOnly = $state(false);
	let yaml = $state('');
	let error = $state<string | null>(null);
	let saving = $state(false);

	function openNew(text = TEMPLATE) {
		editing = null;
		readOnly = false;
		yaml = text;
		error = null;
		editorOpen = true;
	}
	function openExisting(c: CommandDefinition) {
		editing = c;
		readOnly = c.origin === 'builtin';
		yaml = c.source_yaml;
		error = null;
		editorOpen = true;
	}
	function duplicate(c: CommandDefinition) {
		openNew(c.source_yaml.replace(/^id:\s*.+$/m, `id: ${c.id}-custom`).replace(/^name:\s*(.+)$/m, 'name: $1 (custom)'));
	}

	async function save() {
		saving = true;
		error = null;
		try {
			const saved = editing ? await api().commands.update(editing.id, yaml) : await api().commands.create(yaml);
			commands = [...commands.filter((c) => c.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name));
			editorOpen = false;
			snackbar.show(editing ? `Saved “${saved.name}”` : `Added “${saved.name}”`);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}

	async function remove(c: CommandDefinition) {
		const ok = await confirmAction({
			title: `Delete “${c.name}”?`,
			message:
				'The command will be removed from the library.' +
				(c.in_use ? '\nCards that use it keep their current command line as a plain shell command.' : ''),
			confirmLabel: 'Delete command'
		});
		if (!ok) return;
		try {
			await api().commands.remove(c.id);
			commands = commands.filter((x) => x.id !== c.id);
			editorOpen = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete');
		}
	}

	// ?open=<id> (from search) opens that command.
	$effect(() => {
		const id = page.url.searchParams.get('open');
		const c = id ? commands.find((x) => x.id === id) : undefined;
		if (c) openExisting(c);
	});

	let visible = $derived(
		commands.filter((c) => {
			const q = filter.trim().toLowerCase();
			return !q || c.name.toLowerCase().includes(q) || c.id.includes(q) || c.tags.some((t) => t.toLowerCase().includes(q));
		})
	);
</script>

<svelte:head><title>Commands · Castor</title></svelte:head>

<TopAppBar title="Command library" subtitle="{commands.length} commands · cards can run these with typed arguments">
	{#snippet actions()}
		<label class="hidden items-center gap-2 rounded-full bg-surface-container-high px-4 md:flex">
			<Icon name="search" size={20} class="text-on-surface-variant" />
			<input class="h-10 w-44 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter commands" bind:value={filter} autocomplete="off" />
		</label>
		<LibraryTransfer noun="commands" request={{ commands: mine, label: 'castor-commands' }} count={mine.length} onimported={async () => (commands = await api().commands.list())} />
		{#if canEdit()}<Button icon="add" class="ml-2" onclick={() => openNew()}>New command</Button>{/if}
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-8">
	<div class="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4 pt-2">
		{#each visible as c (c.id)}
			<Card variant="elevated" interactive class="flex flex-col gap-3 p-5" role="button" tabindex={0} onclick={() => openExisting(c)} onkeydown={(e) => e.key === 'Enter' && openExisting(c)}>
				<div class="flex items-start gap-3">
					<span class="flex size-10 items-center justify-center rounded-md bg-primary-container text-on-primary-container">
						<Icon name={c.spec.tool ? 'handyman' : c.spec.image ? 'deployed_code' : 'terminal'} />
					</span>
					<div class="min-w-0 flex-1">
						<h2 class="truncate title-medium text-on-surface">{c.name}</h2>
						<p class="truncate font-mono text-[12px] text-on-surface-variant">{c.id}</p>
					</div>
					<span class="rounded-full px-2 py-0.5 label-small {c.origin === 'builtin' ? 'bg-surface-container-highest text-on-surface-variant' : 'bg-tertiary-container text-on-tertiary-container'}">{c.origin === 'builtin' ? 'built-in' : 'yours'}</span>
				</div>
				{#if c.description}<p class="line-clamp-2 body-medium text-on-surface-variant">{c.description}</p>{/if}
				<p class="body-small text-on-surface-variant">
					{#if c.spec.tool}<a href="/tools#{c.spec.tool}" class="font-medium text-primary hover:underline" onclick={(e) => e.stopPropagation()}>tool: {c.spec.tool}</a> · {/if}{c.spec.args.length} argument{c.spec.args.length === 1 ? '' : 's'}{c.spec.vars?.length ? ` · ${c.spec.vars.length} variable slot${c.spec.vars.length === 1 ? '' : 's'}` : ''}{c.spec.outputs.length ? ` · ${c.spec.outputs.length} output${c.spec.outputs.length === 1 ? '' : 's'}` : ''}{c.in_use ? ' · used by cards' : ''}
				</p>
				{#if c.tags.length}
					<div class="flex flex-wrap gap-1">{#each c.tags as t (t)}<Chip variant="suggestion">{t}</Chip>{/each}</div>
				{/if}
			</Card>
		{:else}
			<p class="body-medium text-on-surface-variant">No commands match.</p>
		{/each}
	</div>
</div>

<Dialog bind:open={editorOpen} title={editing ? editing.name : 'New command'} icon="menu_book" size="md">
	<div class="flex flex-col gap-3 pt-2 pb-2">
		{#if readOnly}
			<p class="body-small text-on-surface-variant">Built-in commands come from <code class="font-mono">commands/{editing?.id}.yaml</code> and are read-only here. Duplicate it to make your own version.</p>
		{/if}
		<TextField label="Definition (YAML)" bind:value={yaml} multiline rows={18} mono readonly={readOnly} error={error} supporting={readOnly ? '' : 'Arguments are inserted shell-quoted, so each value is always a single word.'} />
	</div>
	{#snippet actions()}
		{#if editing && !readOnly}
			<Button variant="text" icon="delete" class="mr-auto text-error" onclick={() => editing && remove(editing)}>Delete</Button>
		{/if}
		{#if editing}<Button variant="text" icon="download" onclick={() => editing && saveBlob(editing.source_yaml, `${editing.id}.yaml`)}>Export YAML</Button>{/if}
		<Button variant="text" onclick={() => (editorOpen = false)}>{readOnly ? 'Close' : 'Cancel'}</Button>
		{#if readOnly && editing}
			<Button variant="tonal" icon="content_copy" onclick={() => editing && duplicate(editing)}>Duplicate</Button>
		{:else}
			<Button icon="check" loading={saving} onclick={save}>{editing ? 'Save' : 'Add command'}</Button>
		{/if}
	{/snippet}
</Dialog>
