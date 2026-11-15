<script lang="ts">
	import type { CommandDefinition, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Select, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import CommandArgsForm from './CommandArgsForm.svelte';
	import ListArgInput from './ListArgInput.svelte';
	import VarBindings from './VarBindings.svelte';

	/** One subtask per target (an LXC, a host in a scope group…), each running the same library
	 *  command with its target in one argument: like a playbook's for_each, but as cards to work
	 *  through and run with Run all. Targets can be typed, a saved list or an asset query. */
	interface Props {
		open: boolean;
		task: Task;
		onadded: (cards: Task[]) => void;
	}
	let { open = $bindable(false), task, onadded }: Props = $props();

	let commands = $state<CommandDefinition[]>([]);
	let commandId = $state('');
	let targetArg = $state('');
	let targets = $state<unknown>('');
	let args = $state<Record<string, unknown>>({});
	let errors = $state<Record<string, string>>({});
	let bindings = $state<Record<string, UUID>>({});
	let title = $state('');
	let saving = $state(false);

	$effect(() => {
		if (!open) return;
		api()
			.commands.list()
			.then((list) => (commands = list.filter((c) => (c.spec.args ?? []).some((a) => a.type === 'string' || a.type === 'list'))))
			.catch(() => (commands = []));
	});
	let command = $derived(commands.find((c) => c.id === commandId) ?? null);
	let targetArgs = $derived((command?.spec.args ?? []).filter((a) => a.type === 'string' || a.type === 'list'));
	$effect(() => {
		// A new command: its first text argument takes the targets, the others start at their defaults.
		const c = command;
		targetArg = c ? (targetArgs.find((a) => a.required)?.name ?? targetArgs[0]?.name ?? '') : '';
		args = c ? Object.fromEntries((c.spec.args ?? []).filter((a) => a.default !== undefined && a.default !== null).map((a) => [a.name, a.default])) : {};
		bindings = {};
		errors = {};
	});
	// The form for the other arguments, without the one the targets fill.
	let restSpec = $derived(command ? { ...command.spec, args: (command.spec.args ?? []).filter((a) => a.name !== targetArg) } : null);

	async function add() {
		if (!command || !targetArg) return;
		saving = true;
		errors = {};
		try {
			const cards = await api().tasks.subtasksForTargets(task.id, {
				command_ref: command.id, target_arg: targetArg, targets, args: { ...args, [targetArg]: undefined }, title: title.trim(), var_bindings: bindings
			});
			snackbar.show(`Added ${cards.length} subtask${cards.length === 1 ? '' : 's'}; run them with Run all`);
			onadded(cards);
			open = false;
		} catch (err) {
			const detail = err instanceof ApiError ? (err.detail as { errors?: Record<string, string> } | null) : null;
			if (detail?.errors) errors = detail.errors;
			snackbar.error(err instanceof ApiError ? err.message : 'Could not add them');
		} finally {
			saving = false;
		}
	}
</script>

<Dialog bind:open title="One subtask for each target" icon="dynamic_feed" size="md">
	<div class="flex flex-col gap-4 pt-1 pb-2">
		<p class="body-medium text-on-surface-variant">
			Each target gets its own subtask running the same command, with the target in the argument you choose. They don't start until you
			run them (Run all, or a few at a time).
		</p>
		<Select label="Command" bind:value={commandId} options={commands.map((c) => ({ value: c.id, label: c.name, description: c.id, group: c.spec.tool ?? c.spec.kind ?? undefined }))} />
		{#if command}
			{#if targetArgs.length > 1}
				<Select label="The argument each target goes into" bind:value={targetArg} options={targetArgs.map((a) => ({ value: a.name, label: a.name, description: a.description ?? undefined }))} />
			{/if}
			<ListArgInput
				label="Targets"
				value={targets}
				type="list"
				required
				projectId={task.project_id}
				placeholder={'10.0.0.5\n10.0.0.6'}
				supporting="One per line or comma separated, a saved list, or the project's assets"
				onchange={(v) => (targets = v)}
			/>
			<TextField label="Each subtask's title (optional)" bind:value={title} placeholder={`${command.name}: {{ target }}`} supporting={'{{ target }} is the target'} />
			{#if restSpec?.args?.length}
				<div class="flex flex-col gap-3">
					<p class="label-large text-on-surface-variant">The same for every subtask</p>
					<CommandArgsForm spec={restSpec} bind:values={args} bind:errors projectId={task.project_id} />
				</div>
			{/if}
			{#if command.spec.vars?.length}
				<VarBindings slots={command.spec.vars} {bindings} projectId={task.project_id} onchange={(b) => (bindings = b)} />
			{/if}
		{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="dynamic_feed" disabled={!command || !targetArg || !targets} loading={saving} onclick={add}>Add subtasks</Button>
	{/snippet}
</Dialog>
