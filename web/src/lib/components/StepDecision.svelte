<script lang="ts">
	import type { PlaybookRun, PlaybookStepResult, PlaybookStepSpec, ResolveAction } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { aiStatus } from '$lib/ai.svelte';
	import ParserFix from './ParserFix.svelte';

	/** Shown on a step waiting for a person (status attention): why, and the choices. */
	interface Props {
		run: PlaybookRun;
		step: PlaybookStepSpec;
		result: PlaybookStepResult | null;
		onresolved?: (run: PlaybookRun) => void;
	}
	let { run, step, result, onresolved }: Props = $props();

	let note = $state('');
	let busy = $state<ResolveAction | 'ask-ai' | 'apply' | null>(null);
	$effect(() => void aiStatus.load());
	let suggestion = $derived(result?.ai && result.ai.action ? result.ai : null);
	const ACTION_LABEL: Record<string, string> = { retry: 'Retry', continue: 'Continue', skip: 'Skip branch', fail: 'Fail', ask: 'Leave it to you' };

	async function askAi() {
		busy = 'ask-ai';
		try {
			await api().playbookRuns.aiSuggest(run.id, step.id);
			onresolved?.(await api().playbookRuns.get(run.id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'The AI could not answer');
		} finally {
			busy = null;
		}
	}
	async function applyAi() {
		busy = 'apply';
		try {
			onresolved?.(await api().playbookRuns.aiApply(run.id, step.id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not apply it');
		} finally {
			busy = null;
		}
	}

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

	let choice = $state<ResolveAction | null>(null);
	/** Skip and Fail change what runs next and can't be taken back: ask first. */
	async function apply() {
		const action = choice;
		if (!action) return;
		if (action === 'skip' || action === 'fail') {
			const c = CHOICES.find((x) => x.action === action)!;
			const ok = await confirmAction({
				title: action === 'skip' ? `Skip “${step.name}” and what needs it?` : `Fail “${step.name}”?`,
				message: c.hint + '. This can’t be undone.',
				confirmLabel: c.label,
				danger: action === 'fail'
			});
			if (!ok) return;
		}
		await resolve(action);
	}

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
	{#if result?.parse && result.job_id}
		<ParserFix jobId={result.job_id} output={result.parse.output} parser={result.parse.parser} inRun />
	{/if}
	{#if suggestion}
		<div class="flex flex-col gap-2 rounded-md bg-surface-container px-3 py-2.5">
			<p class="flex items-center gap-1.5 label-large text-on-surface">
				<Icon name="auto_awesome" size={18} class="text-primary" />AI suggests: {ACTION_LABEL[suggestion.action] ?? suggestion.action}
				<span class="body-small text-on-surface-variant">· {Math.round(suggestion.confidence * 100)}% sure · {suggestion.model}</span>
			</p>
			{#if suggestion.reason}<p class="body-medium text-on-surface">{suggestion.reason}</p>{/if}
			{#if suggestion.command}
				<div class="rounded-sm bg-surface-container-highest p-2 font-mono text-[12px] break-all">
					<p class="text-on-surface"><span class="text-primary">new command:</span> {suggestion.command}</p>
				</div>
			{/if}
			{#if suggestion.note}<p class="body-small text-on-surface-variant">{suggestion.note}</p>{/if}
			{#if suggestion.action !== 'ask'}
				<span><Button variant="tonal" icon="check" loading={busy === 'apply'} disabled={busy !== null} onclick={applyAi}>Apply: {ACTION_LABEL[suggestion.action]}{suggestion.command ? ' with the new command' : ''}</Button></span>
			{/if}
		</div>
	{:else if result?.ai?.note}
		<p class="flex items-center gap-1.5 body-small text-on-surface-variant"><Icon name="auto_awesome" size={16} />{result.ai.note}</p>
	{/if}
	<fieldset class="flex flex-col gap-1.5">
		<legend class="mb-1.5 label-large text-on-surface">What should happen?</legend>
		{#each CHOICES as c (c.action)}
			<label class="flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 transition-colors {choice === c.action ? 'border-primary bg-primary-container/40' : 'border-outline-variant hover:bg-on-surface/5'}">
				<input type="radio" name="decision-{step.id}" value={c.action} class="mt-1 size-4 shrink-0 accent-primary" bind:group={choice} disabled={busy !== null} />
				<Icon name={c.icon} size={20} class="mt-0.5 shrink-0 {choice === c.action ? 'text-primary' : 'text-on-surface-variant'}" />
				<span class="min-w-0">
					<span class="block label-large text-on-surface">{c.label}</span>
					<span class="block body-small text-on-surface-variant">{c.hint}</span>
				</span>
			</label>
		{/each}
	</fieldset>
	<TextField label="Note (optional)" bind:value={note} placeholder="Why you chose this, for the run's history" />
	<div class="flex flex-wrap items-center gap-2">
		<Button icon="check" class="max-sm:flex-1" loading={busy !== null && busy === choice} disabled={!choice || busy !== null} onclick={apply}>Apply decision</Button>
		{#if aiStatus.resolver}
			<span title="Ask the AI what it would do (it only suggests)" class="sm:ml-auto">
				<Button variant="text" icon="auto_awesome" loading={busy === 'ask-ai'} disabled={busy !== null} onclick={askAi}>{suggestion ? 'Ask AI again' : 'Ask AI'}</Button>
			</span>
		{/if}
	</div>
</div>
