<script lang="ts">
	import type { Agent, Column, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, TextField, Select, Chip, Switch } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import ColumnGlyph from './ColumnGlyph.svelte';

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
	let columnId = $state('');
	let agentId = $state('');
	let tagsText = $state('');
	let priority = $state('0');
	let runOnMove = $state(true);
	let busy = $state(false);

	$effect(() => {
		if (open) {
			columnId = column?.id ?? columns.find((c) => c.role === 'todo')?.id ?? columns[0]?.id ?? '';
		}
	});

	function reset() {
		kind = 'manual';
		title = description = command = image = agentId = tagsText = '';
		priority = '0';
		runOnMove = true;
	}

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		try {
			const task = await api().projects.createTask(projectId, {
				title,
				description,
				kind,
				command: kind === 'command' ? command : '',
				image: kind === 'command' && image.trim() ? image.trim() : null,
				column_id: columnId,
				agent_selector: agentId ? { agent_id: agentId } : {},
				tags: tagsText.split(',').map((t) => t.trim()).filter(Boolean),
				priority: Number(priority),
				run_on_move: runOnMove
			});
			oncreated?.(task);
			open = false;
			reset();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not create card');
		} finally {
			busy = false;
		}
	}

	let targetColumn = $derived(columns.find((c) => c.id === columnId));
	let agentOptions = $derived([
		{ value: '', label: 'Any available agent' },
		...agents.map((a) => ({ value: a.id, label: `${a.name}${a.connected ? '' : ' (offline)'}` }))
	]);
</script>

<Dialog bind:open title="New card" size="md">
	<form id="new-task" class="flex flex-col gap-5 pt-2 pb-2" onsubmit={submit}>
		<div class="flex gap-2">
			<Chip variant="filter" icon="person" selected={kind === 'manual'} onclick={() => (kind = 'manual')}>Human task</Chip>
			<Chip variant="filter" icon="terminal" selected={kind === 'command'} onclick={() => (kind = 'command')}>Command</Chip>
		</div>
		<TextField label="Title" bind:value={title} required autofocus placeholder={kind === 'command' ? 'e.g. Ping sweep of 10.0.0.0/24' : 'e.g. Schedule kickoff with SOC'} />
		{#if kind === 'command'}
			<TextField label="Command" bind:value={command} multiline rows={3} mono required placeholder="nmap -sn 10.0.0.0/24" supporting="Runs in /bin/sh on the agent. Mounted workdir is /work when run in a container." />
			<TextField label="Container image (optional)" bind:value={image} mono placeholder="docker.io/instrumentisto/nmap" supporting="Leave empty to run in the agent's own shell" />
			<div class="grid gap-4 sm:grid-cols-2">
				<Select label="Agent" bind:value={agentId} options={agentOptions} />
				<div class="flex items-center">
					<Switch bind:checked={runOnMove} label="Run when moved to In Progress" />
				</div>
			</div>
		{/if}
		<TextField label="Description" bind:value={description} multiline rows={3} supporting="Markdown supported" />
		<div class="grid gap-4 sm:grid-cols-3">
			<Select label="Column" bind:value={columnId} options={columns.map((c) => ({ value: c.id, label: c.name }))}>
				{#snippet leading(id)}<ColumnGlyph role={columns.find((c) => c.id === id)?.role} />{/snippet}
			</Select>
			<Select label="Priority" bind:value={priority} options={[{ value: '0', label: 'None' }, { value: '1', label: 'Low' }, { value: '2', label: 'Medium' }, { value: '3', label: 'High' }]} />
			<TextField label="Tags" bind:value={tagsText} placeholder="recon, web" />
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
