<script lang="ts">
	import type { OutputChange } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { countWords, describe, fmt } from '$lib/changes';

	/** Outputs compared between two runs: what's new, gone and changed in each, one line per
	 *  record (JobChanges for a card's run, RunPanel for a playbook run's steps). */
	type Show = 'all' | 'added' | 'removed' | 'changed';
	interface Props {
		outputs: OutputChange[];
		show?: Show;
	}
	let { outputs, show = 'all' }: Props = $props();

	let changed = $derived(outputs.filter((o) => o.status !== 'same' && o.status !== 'error'));
	let same = $derived(outputs.filter((o) => o.status === 'same'));
	let errors = $derived(outputs.filter((o) => o.status === 'error'));
	const SHOWN = 50; // rows per kind before "Show all"
	let expanded = $state<Record<string, boolean>>({});
	const rows = <T,>(o: OutputChange, kind: string, list: T[]) => (expanded[`${o.name}:${kind}`] ? list : list.slice(0, SHOWN));
</script>

{#snippet more(o: OutputChange, kind: string, shown: number, total: number)}
	{#if total > shown && !expanded[`${o.name}:${kind}`]}
		<li>
			<button type="button" class="px-3 py-1.5 label-medium text-primary hover:underline" onclick={() => (expanded[`${o.name}:${kind}`] = true)}>
				Show all {Math.min(total, 500)}
			</button>
		</li>
	{/if}
	{#if total > 500 && expanded[`${o.name}:${kind}`]}
		<li class="px-3 py-1.5 body-small text-on-surface-variant">… and {total - 500} more not listed</li>
	{/if}
{/snippet}

<div class="flex flex-col gap-3">
	{#each changed as o (o.name)}
		<section class="overflow-hidden rounded-md border border-outline-variant/60 bg-surface">
			<header class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-outline-variant/60 px-3 py-2">
				<h4 class="title-small text-on-surface">{o.name}</h4>
				{#if o.type}<span class="font-mono text-[11.5px] text-on-surface-variant">{o.type}</span>{/if}
				<span class="flex-1"></span>
				<span class="body-small text-on-surface-variant">
					{o.status === 'new' ? 'Only in this run · ' : o.status === 'gone' ? 'Not in this run · ' : ''}{countWords(o.counts)}{#if o.unchanged}{` · ${o.unchanged} the same`}{/if}
				</span>
			</header>
			<ul class="divide-y divide-outline-variant/40">
				{#if o.after !== null && !Array.isArray(o.after) && !o.changed.length && o.counts.changed}
					<li class="flex items-start gap-2.5 px-3 py-1.5">
						<Icon name="change_circle" size={18} class="mt-0.5 shrink-0 text-tertiary" />
						<span class="min-w-0 body-medium"><span class="text-on-surface-variant line-through">{fmt(o.before)}</span> → <span class="text-on-surface">{fmt(o.after)}</span></span>
					</li>
				{/if}
				{#if show === 'all' || show === 'added'}
					{#each rows(o, 'added', o.added) as r, i (i)}
						{@const d = describe(r, o.key, o.type)}
						<li class="flex items-start gap-2.5 px-3 py-1.5">
							<Icon name="add_circle" size={18} class="mt-0.5 shrink-0 text-primary" />
							<span class="sr-only">New:</span>
							<span class="min-w-0 flex-1 body-medium"><span class="font-mono text-[13px] text-on-surface break-all">{d.title}</span>{#if d.detail}<span class="text-on-surface-variant"> · {d.detail}</span>{/if}</span>
						</li>
					{/each}
					{@render more(o, 'added', Math.min(o.added.length, SHOWN), o.counts.added)}
				{/if}
				{#if show === 'all' || show === 'removed'}
					{#each rows(o, 'removed', o.removed) as r, i (i)}
						{@const d = describe(r, o.key, o.type)}
						<li class="flex items-start gap-2.5 px-3 py-1.5">
							<Icon name="do_not_disturb_on" size={18} class="mt-0.5 shrink-0 text-error" />
							<span class="sr-only">Gone:</span>
							<span class="min-w-0 flex-1 body-medium text-on-surface-variant"><span class="font-mono text-[13px] line-through break-all">{d.title}</span>{#if d.detail} · {d.detail}{/if}</span>
						</li>
					{/each}
					{@render more(o, 'removed', Math.min(o.removed.length, SHOWN), o.counts.removed)}
				{/if}
				{#if show === 'all' || show === 'changed'}
					{#each rows(o, 'changed', o.changed) as c, i (i)}
						{@const d = describe(c.item, o.key, o.type)}
						<li class="flex items-start gap-2.5 px-3 py-1.5">
							<Icon name="change_circle" size={18} class="mt-0.5 shrink-0 text-tertiary" />
							<span class="sr-only">Changed:</span>
							<div class="min-w-0 flex-1">
								<span class="font-mono text-[13px] text-on-surface break-all">{d.title}</span>
								<ul class="mt-0.5 flex flex-col gap-0.5">
									{#each c.fields as f (f.field)}
										<li class="body-small text-on-surface-variant">
											<span class="text-on-surface">{f.field}</span>
											<span class="line-through">{fmt(f.before)}</span> → <span class="text-on-surface">{fmt(f.after)}</span>
										</li>
									{/each}
								</ul>
							</div>
						</li>
					{/each}
					{@render more(o, 'changed', Math.min(o.changed.length, SHOWN), o.counts.changed)}
				{/if}
			</ul>
		</section>
	{/each}
	{#each errors as o (o.name)}
		<p class="flex items-start gap-2 body-small text-on-surface-variant">
			<Icon name="error" size={16} class="mt-px shrink-0 text-error" /><span><span class="text-on-surface">{o.name}</span> couldn’t be compared: one of the runs didn’t parse it ({o.error}).</span>
		</p>
	{/each}
	{#if same.length}
		<p class="flex items-start gap-2 body-small text-on-surface-variant">
			<Icon name="check" size={16} class="mt-px shrink-0" /><span>The same as before: {same.map((o) => o.name).join(', ')}.</span>
		</p>
	{/if}
</div>
