<script lang="ts">
	import type { PlaybookStepResult, StepResolution } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { wordDiff } from '$lib/wordDiff';

	/** Every decision taken on a paused step, oldest first: who (a person or the AI, with its
	 *  model and how sure it was), what, why, and exactly how the command changed. */
	let { result }: { result: PlaybookStepResult | null } = $props();

	const ACTIONS: Record<string, string> = { retry: 'retried it', continue: 'continued', skip: 'skipped it and what needs it', fail: 'failed it' };
	let decisions = $derived<StepResolution[]>(result?.history?.length ? result.history : result?.resolution ? [result.resolution] : []);
	// Decisions from before the `ai` flag still name the model: "AI (provider / model)".
	const isAi = (d: StepResolution) => (!!d.ai || d.by.startsWith('AI (')) && !d.suggested;
	const modelOf = (d: StepResolution) => d.model || d.by.match(/^AI \((.*)\)$/)?.[1] || '';
	/** Inline code spans in the server's "replaced `a` with `b`". */
	const pieces = (text: string) => (text.charAt(0).toUpperCase() + text.slice(1)).split('`').map((t, i) => ({ t, code: i % 2 === 1 }));
</script>

{#if decisions.length}
	<section class="flex flex-col gap-2" aria-label="Decisions on this step">
		{#each decisions as d, i (i)}
			<div class="flex items-start gap-2.5 rounded-lg px-3 py-2.5 {isAi(d) || d.suggested ? 'bg-primary-container/35' : 'bg-surface-container'}">
				<Icon name={isAi(d) || d.suggested ? 'auto_awesome' : 'how_to_reg'} size={18} class="mt-0.5 shrink-0 {isAi(d) || d.suggested ? 'text-primary' : 'text-on-surface-variant'}" />
				<div class="min-w-0 flex-1 body-small text-on-surface">
					<p>
						{#if isAi(d)}<strong>The AI</strong>{#if modelOf(d)}<span class="text-on-surface-variant"> ({modelOf(d)})</span>{/if}{:else}<strong>{d.by}</strong>{/if}
						{d.command_after ? 'changed the command and retried' : (ACTIONS[d.action] ?? d.action)}
						{#if d.suggested}<span class="text-on-surface-variant">, as the AI suggested{modelOf(d) ? ` (${modelOf(d)})` : ''}</span>{/if}
						<span class="text-on-surface-variant" title={absoluteTime(d.at)}> · {relativeTime(d.at)}</span>
						{#if d.confidence != null}<span class="text-on-surface-variant"> · {Math.round(d.confidence * 100)}% sure</span>{/if}
					</p>
					{#if d.reason}<p class="mt-0.5 text-on-surface-variant">Paused because {d.reason}</p>{/if}
					{#if d.change}
						<p class="mt-1 font-medium">
							{#each pieces(d.change) as p, k (k)}{#if p.code}<code class="rounded-xs bg-surface-container-highest px-1 font-mono text-[12px]">{p.t}</code>{:else}{p.t}{/if}{/each}
						</p>
					{/if}
					{#if d.command_before != null && d.command_after != null}
						<details class="mt-1" open={d.command_before.length < 400}>
							<summary class="cursor-pointer text-on-surface-variant">The command, before → after</summary>
							<pre class="mt-1 max-h-60 overflow-auto rounded-md bg-surface-container-highest p-2 font-mono text-[12px] leading-5 break-all whitespace-pre-wrap">{#each wordDiff(d.command_before, d.command_after) as part, k (k)}{#if part.kind === 'removed'}<del class="rounded-xs bg-error-container text-on-error-container decoration-error/70">{part.text}</del>{:else if part.kind === 'added'}<ins class="rounded-xs bg-primary-container text-on-primary-container no-underline">{part.text}</ins>{:else}{part.text}{/if}{/each}</pre>
						</details>
					{/if}
					{#if d.note && d.note !== d.reason}<p class="mt-1 {isAi(d) ? '' : 'italic'} text-on-surface">{isAi(d) || d.suggested ? 'Why: ' : '“'}{d.note}{isAi(d) || d.suggested ? '' : '”'}</p>{/if}
				</div>
			</div>
		{/each}
	</section>
{/if}
