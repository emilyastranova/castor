<script lang="ts">
	import type { PlaybookStepResult, PlaybookStepSpec, PlaybookStepStatus } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { STEP_STATUS, opDetail, opName, stepRefs, transformInputs } from '$lib/playbook';
	import OutputCard from './OutputCard.svelte';

	/** A transform step's pipeline, and in a run its result. */
	interface Props {
		step: PlaybookStepSpec;
		result?: PlaybookStepResult | null;
		status?: PlaybookStepStatus | null;
	}
	let { step, result = null, status = null }: Props = $props();

	let transform = $derived(step.transform!);
	let outputName = $derived(transform.output.name);
	let value = $derived(result?.outputs?.[outputName]);
</script>

<div class="flex flex-col gap-4">
	<div class="grid gap-3 md:grid-cols-[1fr_auto_1fr] md:items-start">
		<div class="flex flex-col gap-1.5">
			<span class="label-medium text-on-surface">Input</span>
			{#each transformInputs(transform) as input (input)}
				{@const refs = stepRefs(input)}
				<div class="rounded-lg border border-outline-variant/60 bg-surface-container-low px-3 py-2">
					{#if refs.length}
						<span class="font-mono text-[12px] text-on-surface">{refs[0].step}</span>
						<span class="font-mono text-[12px] text-on-surface-variant">.{refs[0].output}</span>
					{:else}
						<span class="font-mono text-[12px] text-primary break-all">{input}</span>
					{/if}
				</div>
			{/each}
			{#if result?.input_count !== undefined}
				<span class="body-small text-on-surface-variant">{result.input_count} item{result.input_count === 1 ? '' : 's'} in</span>
			{/if}
		</div>

		<div class="flex flex-col gap-1.5">
			<span class="label-medium text-on-surface">Operations</span>
			<ol class="flex flex-col gap-1">
				{#each transform.ops as op, i (i)}
					<li class="flex items-center gap-2 rounded-lg border border-tertiary/30 bg-tertiary-container/15 px-3 py-1.5">
						<span class="w-4 shrink-0 text-right font-mono text-[11px] text-on-surface-variant">{i + 1}</span>
						<span class="w-12 shrink-0 font-mono text-[11px] font-semibold text-tertiary">{opName(op)}</span>
						<span class="font-mono text-[12px] text-on-surface break-all">{opDetail(op)}</span>
					</li>
				{:else}
					<li class="body-small text-on-surface-variant">No operations: the input is passed through unchanged.</li>
				{/each}
			</ol>
		</div>

		<div class="flex flex-col gap-1.5">
			<span class="label-medium text-on-surface">Output</span>
			<div class="rounded-lg border border-outline-variant/60 bg-surface-container-low px-3 py-2">
				<span class="font-mono text-[12px] text-on-surface">{outputName}</span>
				<span class="font-mono text-[11px] text-on-surface-variant">
					{result?.type ?? transform.output.type ?? '(type from input)'}
				</span>
			</div>
			{#if result?.count !== undefined}
				<span class="body-small text-on-surface-variant">{result.count} item{result.count === 1 ? '' : 's'} out</span>
			{/if}
		</div>
	</div>

	{#if status}
		{#if result?.status === 'succeeded'}
			<OutputCard name={outputName} type={result.type ?? 'list<string>'} {value} />
		{:else if result?.error}
			<div class="rounded-xl border border-error/50 bg-error-container/20 p-4 body-medium text-on-surface">
				<p class="font-semibold text-error">Transform failed</p>
				<p class="mt-1 font-mono text-[12px]">{result.error}</p>
			</div>
		{:else}
			<div class="flex items-center justify-center gap-2 rounded-xl border border-dashed border-outline-variant p-6 text-center body-medium text-on-surface-variant">
				<Icon name={STEP_STATUS[status].icon} size={18} />
				{#if status === 'blocked'}
					An upstream step failed, so this transform will not run.
				{:else if status === 'skipped'}
					Skipped: {result?.reason ?? 'an upstream value was empty'}.
				{:else if status === 'cancelled'}
					The run was cancelled before this transform ran.
				{:else}
					Runs on the server as soon as its input is ready.
				{/if}
			</div>
		{/if}
	{/if}
</div>
