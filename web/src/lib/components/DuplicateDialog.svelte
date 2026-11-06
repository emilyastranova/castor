<script lang="ts">
	import type { Task } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Switch } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Copy a card to the top of To Do: always its blueprint (command, arguments, fields),
	 *  and its comments, files and finished runs if wanted. Nothing starts running. */
	interface Props {
		open: boolean;
		task: Task;
		counts: { comments: number; files: number; runs: number };
		onduplicated: (copy: Task) => void;
	}
	let { open = $bindable(false), task, counts, onduplicated }: Props = $props();

	let assignees = $state(true);
	let comments = $state(false);
	let attachments = $state(false);
	let output = $state(false);
	let busy = $state(false);
	$effect(() => {
		if (!open) return;
		assignees = true;
		comments = attachments = output = false;
	});

	const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

	async function run() {
		busy = true;
		try {
			const copy = await api().tasks.duplicate(task.id, { assignees, comments, attachments, output });
			snackbar.show(`Added “${copy.title}” to To Do`);
			open = false;
			onduplicated(copy);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not duplicate the card');
		} finally {
			busy = false;
		}
	}
</script>

<Dialog bind:open title="Duplicate card" icon="content_copy" size="sm">
	<div class="flex flex-col gap-3 pt-2 pb-2">
		<p class="body-medium text-on-surface-variant">
			A copy of “{task.title}” goes to the top of To Do with its {task.kind === 'command' ? 'command, arguments, ' : ''}description, tags, priority and fields. It won't run until you start it.
		</p>
		{#if task.assignees.length}<Switch bind:checked={assignees} label="Keep the assignees" />{/if}
		<Switch bind:checked={comments} disabled={!counts.comments} label="Comments ({counts.comments})" />
		<Switch bind:checked={attachments} disabled={!counts.files} label="Files and screenshots ({counts.files})" />
		{#if task.kind === 'command'}
			<Switch bind:checked={output} disabled={!counts.runs} label="Output: {plural(counts.runs, 'finished run')} with logs, results and artifacts" />
		{/if}
		{#if comments && counts.files && !attachments}
			<p class="px-1 body-small text-on-surface-variant">Images in the copied comments keep showing the original card's files.</p>
		{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="content_copy" loading={busy} onclick={run}>Duplicate</Button>
	{/snippet}
</Dialog>
