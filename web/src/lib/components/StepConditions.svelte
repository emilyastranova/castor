<script lang="ts">
	import type { ConditionCheck, PlaybookStepResult, PlaybookStepSpec, PlaybookStepStatus } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { describeCondition, describeExpect } from '$lib/playbook';
	import { relativeTime } from '$lib/format';

	/** How a step decides whether to run and whether its result is right: its condition
	 *  (and how it evaluated), join, failure policy, expectations, and any decision made. */
	interface Props {
		step: PlaybookStepSpec;
		result?: PlaybookStepResult | null;
		status?: PlaybookStepStatus;
	}
	let { step, result = null, status = 'pending' }: Props = $props();

	const ACTIONS = { retry: 'Retry', continue: 'Continue', skip: 'Skip branch', fail: 'Fail' } as const;
	const FAILURE = {
		stop: 'the steps after it are blocked',
		continue: 'the steps after it go ahead anyway',
		ask: 'it pauses and notifies people to decide'
	};

	let hasAnything = $derived(
		!!step.when || !!step.expect || (step.join ?? 'all') !== 'all' || (step.on_failure ?? 'stop') !== 'stop' || !!result?.resolution
	);
</script>

{#snippet checks(list: ConditionCheck[])}
	<ul class="mt-1.5 flex flex-col gap-1">
		{#each list as c, i (i)}
			<li class="flex items-start gap-2 body-small">
				<Icon name={c.met ? 'check_circle' : 'cancel'} size={16} class="mt-px shrink-0 {c.met ? 'text-primary' : 'text-error'}" filled />
				<span class="min-w-0 font-mono text-[12px] break-words text-on-surface">{c.text}</span>
				{#if c.actual}<span class="ml-auto shrink-0 body-small text-on-surface-variant">got {c.actual}</span>{/if}
			</li>
		{/each}
	</ul>
{/snippet}

{#if hasAnything}
	<div class="flex flex-col gap-3 rounded-xl border border-outline-variant/60 bg-surface p-4">
		{#if step.when}
			<div>
				<div class="flex items-center gap-2">
					<Icon name="alt_route" size={18} class="text-tertiary" />
					<span class="label-large text-on-surface">Runs only if</span>
					{#if result?.condition}
						<span class="ml-auto rounded-full px-2 py-0.5 label-small {result.condition.met ? 'bg-primary-container text-on-primary-container' : 'bg-surface-container-highest text-on-surface-variant'}">
							{result.condition.met ? 'condition met' : 'not met · branch not taken'}
						</span>
					{:else if status === 'pending'}
						<span class="ml-auto label-small text-on-surface-variant">checked when its inputs are ready</span>
					{/if}
				</div>
				<p class="mt-1 font-mono text-[12.5px] text-on-surface">{describeCondition(step.when)}</p>
				{#if result?.condition?.checks.length}{@render checks(result.condition.checks)}{/if}
			</div>
		{/if}

		{#if step.expect}
			<div>
				<div class="flex items-center gap-2">
					<Icon name="fact_check" size={18} class="text-on-surface-variant" />
					<span class="label-large text-on-surface">Expects</span>
				</div>
				{#if result?.expect?.length}
					{@render checks(result.expect)}
				{:else}
					<ul class="mt-1 list-disc pl-6 font-mono text-[12.5px] text-on-surface">
						{#each describeExpect(step.expect) as line (line)}<li>{line}</li>{/each}
					</ul>
				{/if}
			</div>
		{/if}

		{#if (step.join ?? 'all') !== 'all' || (step.on_failure ?? 'stop') !== 'stop'}
			<ul class="flex flex-col gap-1 body-small text-on-surface-variant">
				{#if step.join === 'any'}
					<li class="flex gap-2"><Icon name="merge" size={16} />Starts once every input has ended and at least one worked (where branches meet).</li>
				{:else if step.join === 'always'}
					<li class="flex gap-2"><Icon name="merge" size={16} />Starts once every input has ended, whatever happened.</li>
				{/if}
				{#if (step.on_failure ?? 'stop') !== 'stop'}
					<li class="flex gap-2">
						<Icon name={step.on_failure === 'ask' ? 'front_hand' : 'fast_forward'} size={16} />If it fails{step.expect ? ' or its output is unexpected' : ''}, {FAILURE[step.on_failure ?? 'stop']}.
					</li>
				{/if}
			</ul>
		{/if}

		{#if result?.resolution}
			{@const r = result.resolution}
			<div class="flex items-start gap-2 rounded-lg bg-surface-container px-3 py-2 body-small text-on-surface">
				<Icon name="how_to_reg" size={18} class="shrink-0 text-on-surface-variant" />
				<div class="min-w-0">
					<p><strong>{r.by}</strong> chose <strong>{ACTIONS[r.action]}</strong> {relativeTime(r.at)}</p>
					{#if r.reason}<p class="text-on-surface-variant">Because: {r.reason}</p>{/if}
					{#if r.note}<p class="italic text-on-surface-variant">“{r.note}”</p>{/if}
				</div>
			</div>
		{/if}
	</div>
{/if}
