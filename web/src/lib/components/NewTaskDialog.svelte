<script lang="ts">
	import { selectorFrom, selectorOptions } from '$lib/agentSelector';
	import type { Agent, ArgErrors, Column, CommandDefinition, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, TextField, Select, Chip, Switch, TagInput } from '$lib/m3';
	import { loadTags, type TagSuggestion } from '$lib/tags';
	import { snackbar } from '$lib/snackbar.svelte';
	import ColumnGlyph from './ColumnGlyph.svelte';
	import PriorityGlyph, { priorityOptions } from './PriorityGlyph.svelte';
	import CommandArgsForm from './CommandArgsForm.svelte';
	import VarBindings from './VarBindings.svelte';

	interface Props {
		open?: boolean;
		projectId: UUID;
		columns: Column[];
		column?: Column | null;
		agents: Agent[];
		oncreated?: (task: Task) => void;
	}
	let { open = $bindable(false), projectId, columns, column = null, agents, oncreated }: Props = $props();

	let kind = $state<'manual' | 'command'>('manual');
	let title = $state('');
	let description = $state('');
	let command = $state('');
	let image = $state('');
	let containerFlagsText = $state('');
	let columnId = $state('');
	let agentId = $state('');
	let tags = $state<string[]>([]);
	let tagSuggestions = $state<TagSuggestion[]>([]);
	let priority = $state('0');
	let runOnMove = $state(true);
	let busy = $state(false);

	// Library commands
	let source = $state<'library' | 'custom'>('library');
	let library = $state<CommandDefinition[]>([]);
	let commandRef = $state('');
	let commandArgs = $state<Record<string, unknown>>({});
	let varBindings = $state<Record<string, UUID>>({});
	let argErrors = $state<Record<string, string>>({});
	let chosen = $derived(library.find((c) => c.id === commandRef) ?? null);

	$effect(() => {
		if (open && kind === 'command' && library.length === 0) {
			api()
				.commands.list()
				.then((list) => {
					library = list;
					if (!commandRef && list[0]) commandRef = list[0].id;
				})
				.catch(() => (source = 'custom'));
		}
	});

	function pickCommand(id: string) {
		commandRef = id;
		commandArgs = {};
		varBindings = {};
		argErrors = {};
		const def = library.find((c) => c.id === id);
		if (def && !title.trim()) title = def.name;
	}

	$effect(() => {
		if (open) {
			columnId = column?.id ?? columns.find((c) => c.role === 'todo')?.id ?? columns[0]?.id ?? '';
			void loadTags('cards', projectId).then((t) => (tagSuggestions = t));
		}
	});

	function reset() {
		kind = 'manual';
		commandArgs = {};
		varBindings = {};
		argErrors = {};
		title = description = command = image = containerFlagsText = agentId = '';
		tags = [];
		priority = '0';
		runOnMove = true;
	}

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		try {
			const fromLibrary = kind === 'command' && source === 'library' && !!commandRef;
			const flags = containerFlagsText.trim()
				? containerFlagsText.split(/\s+/).filter(Boolean)
				: [];
			const task = await api().projects.createTask(projectId, {
				title,
				description,
				kind,
				command: kind === 'command' && !fromLibrary ? command : '',
				image: kind === 'command' && !fromLibrary && image.trim() ? image.trim() : null,
				container_flags: kind === 'command' && !fromLibrary ? flags : undefined,
				command_ref: fromLibrary ? commandRef : null,
				command_args: fromLibrary ? commandArgs : {},
				var_bindings: fromLibrary ? varBindings : {},
				column_id: columnId,
				agent_selector: selectorFrom(agentId),
				tags,
				priority: Number(priority),
				run_on_move: runOnMove
			});
			oncreated?.(task);
			open = false;
			reset();
		} catch (err) {
			const detail = err instanceof ApiError ? (err.detail as ArgErrors | null) : null;
			if (detail?.errors) {
				argErrors = detail.errors;
				snackbar.error('Some command arguments need fixing');
				return;
			}
			snackbar.error(err instanceof ApiError ? err.message : 'Could not create card');
		} finally {
			busy = false;
		}
	}

	let targetColumn = $derived(columns.find((c) => c.id === columnId));
	// Your commands first, then the built-ins; search matches the id, tags and tool too.
	let commandOptions = $derived(
		[...library]
			.sort((a, b) => (a.origin === b.origin ? a.name.localeCompare(b.name) : a.origin === 'user' ? -1 : 1))
			.map((c) => ({
				value: c.id,
				label: c.name,
				description: c.description || c.id,
				group: c.origin === 'user' ? 'Yours' : 'Built-in',
				keywords: [c.id, ...c.tags, c.spec.tool ?? '', c.spec.image ?? ''].join(' ')
			}))
	);
	let agentOptions = $derived(selectorOptions(agents));
</script>

<Dialog bind:open title="New card" size="md">
	<form id="new-task" class="flex flex-col gap-5 pt-2 pb-2" onsubmit={submit}>
		<div class="flex gap-2">
			<Chip variant="filter" icon="person" selected={kind === 'manual'} onclick={() => (kind = 'manual')}>Human task</Chip>
			<Chip variant="filter" icon="terminal" selected={kind === 'command'} onclick={() => (kind = 'command')}>Command</Chip>
		</div>
		<TextField label="Title" bind:value={title} required autofocus placeholder={kind === 'command' ? 'e.g. Ping sweep of 10.0.0.0/24' : 'e.g. Schedule kickoff with SOC'} />
		{#if kind === 'command'}
			<div class="flex gap-2">
				<Chip variant="filter" icon="menu_book" selected={source === 'library'} onclick={() => (source = 'library')}>From library</Chip>
				<Chip variant="filter" icon="code" selected={source === 'custom'} onclick={() => (source = 'custom')}>Custom shell</Chip>
			</div>
			{#if source === 'library'}
				{#if library.length}
					<Select
						label="Command"
						value={commandRef}
						options={commandOptions}
						onchange={pickCommand}
					/>
					{#if chosen}
						{#key chosen.id}
							<CommandArgsForm spec={chosen.spec} bind:values={commandArgs} bind:errors={argErrors} {projectId} />
							{#if chosen.spec.vars?.length}
								<VarBindings slots={chosen.spec.vars} bindings={varBindings} {projectId} onchange={(b) => (varBindings = b)} />
							{/if}
						{/key}
					{/if}
				{:else}
					<p class="body-medium text-on-surface-variant">Loading the command library…</p>
				{/if}
			{:else}
				<TextField label="Command" bind:value={command} multiline rows={3} mono required placeholder="nmap -sn 10.0.0.0/24" supporting="Runs in /bin/sh on the agent, in the job's own directory ($CASTOR_WORK: /work in a container). Files under $CASTOR_WORK/artifacts come back to the card." />
				<TextField label="Container image (optional)" bind:value={image} mono placeholder="docker.io/instrumentisto/nmap" supporting="Leave empty to run in the agent's own shell" />
				{#if image.trim()}
					<TextField label="Container flags (optional)" bind:value={containerFlagsText} mono placeholder="--privileged --network=bridge" supporting="Flags passed to docker run / podman run" />
				{/if}
			{/if}
			<div class="grid gap-4 sm:grid-cols-2">
				<Select label="Agent" bind:value={agentId} options={agentOptions} />
				<div class="flex items-center">
					<Switch bind:checked={runOnMove} label="Run when moved to In Progress" />
				</div>
			</div>
		{/if}
		<TextField label="Description" bind:value={description} multiline markdown rows={3} supporting="Markdown supported" />
		<div class="grid gap-4 sm:grid-cols-2">
			<Select label="Column" bind:value={columnId} options={columns.map((c) => ({ value: c.id, label: c.name }))}>
				{#snippet leading(id)}<ColumnGlyph role={columns.find((c) => c.id === id)?.role} />{/snippet}
			</Select>
			<Select label="Priority" bind:value={priority} options={priorityOptions}>
				{#snippet leading(level)}<PriorityGlyph {level} />{/snippet}
			</Select>
			<TagInput label="Tags" bind:value={tags} suggestions={tagSuggestions} noun="cards" placeholder="Add a tag" class="sm:col-span-2" />
		</div>
		{#if kind === 'command' && targetColumn?.role === 'active' && runOnMove}
			<p class="rounded-sm bg-tertiary-container px-3 py-2 body-small text-on-tertiary-container">This card will be dispatched to an agent as soon as it is created.</p>
		{/if}
	</form>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button type="submit" form="new-task" loading={busy} icon={kind === 'command' ? 'terminal' : 'add'}>Create card</Button>
	{/snippet}
</Dialog>
