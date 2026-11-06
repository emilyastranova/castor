<script lang="ts">
	import type { AlertRules, Task } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import AlertRulesForm from './AlertRulesForm.svelte';

	/** A card's alerts: which channels hear about its runs (on a run card, the playbook run). */
	interface Props {
		open: boolean;
		task: Task;
		onsaved: (task: Task) => void;
	}
	let { open = $bindable(false), task, onsaved }: Props = $props();

	const blank = (): AlertRules => ({ channels: [], on: 'failure', long_minutes: null, decisions: true });
	let rules = $state<AlertRules>(blank());
	let saving = $state(false);
	$effect(() => {
		if (open) rules = { ...blank(), ...(task.alerts ?? {}) };
	});

	async function save() {
		saving = true;
		try {
			const updated = await api().tasks.update(task.id, { alerts: rules.channels.length || rules.people?.length ? rules : null });
			snackbar.show(rules.channels.length ? 'Chat alerts saved' : 'Chat alerts off');
			open = false;
			onsaved(updated);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save');
		} finally {
			saving = false;
		}
	}
</script>

<Dialog bind:open title="Chat alerts" icon="campaign" size="sm">
	<div class="flex flex-col gap-3 pt-2 pb-2">
		<p class="body-medium text-on-surface-variant">
			{task.run ? 'Post to chat when this playbook run ends, runs long or needs a decision.' : "Post to chat when this card's runs end or run long."}
		</p>
		<AlertRulesForm bind:rules run={!!task.run} />
	</div>
	{#snippet actions()}
		{#if task.alerts?.channels?.length}<Button variant="text" class="mr-auto" onclick={() => { rules = { ...blank(), people: rules.people }; void save(); }}>Turn off</Button>{/if}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="check" loading={saving} onclick={save}>Save</Button>
	{/snippet}
</Dialog>
