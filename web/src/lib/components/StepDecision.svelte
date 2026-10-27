<script lang="ts">
	import type { PlaybookRun, PlaybookStepResult, PlaybookStepSpec, ResolveAction } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Shown on a step waiting for a person (status attention): why, and the choices. */
	interface Props {
		run: PlaybookRun;
		step: PlaybookStepSpec;
		result: PlaybookStepResult | null;
		onresolved?: (run: PlaybookRun) => void;
	}
	let { run, step, result, onresolved }: Props = $props();

	let note = $state('');
	let busy = $state<ResolveAction | null>(null);

	const CHOICES: { action: ResolveAction; label: string; icon: string; hint: string; variant: 'filled' | 'tonal' | 'outlined' }[] = $derived([
		{
			action: 'retry',
			label: 'Retry',
			icon: 'replay',
			hint: step.kind === 'command' ? 'Run the card again as it is now (edit its command below first if that’s the fix)' : 'Evaluate the step again',
			variant: 'filled'
		},
		{ action: 'continue', label: 'Continue', icon: 'arrow_forward', hint: 'Accept the result and carry on with whatever it produced', variant: 'tonal' },
		{ action: 'skip', label: 'Skip branch', icon: 'alt_route', hint: 'Leave this step and the steps that need it out', variant: 'outlined' },
		{ action: 'fail', label: 'Fail', icon: 'close', hint: 'Fail it: the steps after it are blocked, unless a branch handles the failure', variant: 'outlined' }
	]);

	async function resolve(action: ResolveAction) {
		busy = action;
		try {
			const updated = await api().playbookRuns.resolve(run.id, step.id, action, note);
			note = '';
			onresolved?.(updated);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not apply the decision');
		} finally {
			busy = null;
		}
	}
</script>

<div class="flex flex-col gap-3 rounded-xl border border-tertiary bg-tertiary-container/40 p-4">
	<div class="flex items-start gap-3">
		<Icon name="front_hand" size={22} class="mt-0.5 shrink-0 text-tertiary" filled />
		<div class="min-w-0">
			<p class="title-small text-on-surface">This step needs a decision</p>
			<p class="mt-0.5 body-medium break-words text-on-surface first-letter:uppercase">{result?.reason ?? 'It failed.'}</p>
			<p class="mt-1 body-small text-on-surface-variant">The steps after it wait until you choose. Everyone was notified.</p>
		</div>
	</div>
	<TextField label="Note (optional)" bind:value={note} placeholder="Why you chose this, for the run's history" />
	<div class="flex flex-wrap gap-2">
		{#each CHOICES as c (c.action)}
			<span title={c.hint}>
				<Button variant={c.variant} icon={c.icon} loading={busy === c.action} disabled={busy !== null} onclick={() => resolve(c.action)}>{c.label}</Button>
			</span>
		{/each}
	</div>
</div>
