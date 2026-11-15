<script lang="ts">
	import type { PlaybookRun, RunChanges, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon, Select } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { absoluteTime, relativeTime } from '$lib/format';
	import { anyChange, countWords, toMarkdown } from '$lib/changes';
	import ResultChanges from './ResultChanges.svelte';

	/** A finished playbook run against the playbook's run before it in the project: what each
	 *  step found that's new, gone or changed. Folded to one line until opened. */
	interface Props {
		run: PlaybookRun;
	}
	let { run }: Props = $props();

	let data = $state<RunChanges | null>(null);
	let failed = $state<string | null>(null);
	let open = $state(false);
	let finished = $derived(run.status === 'succeeded' || run.status === 'failed');

	async function load(id: UUID, base: UUID | null) {
		try {
			const c = await api().playbookRuns.changes(id, base);
			if (id === run.id) {
				data = c;
				failed = null;
			}
		} catch (err) {
			failed = err instanceof ApiError ? err.message : 'Could not compare the runs';
		}
	}
	$effect(() => {
		const id = run.id;
		data = null;
		if (!finished) return;
		void load(id, null);
		// A step's results parsed again: compare again.
		return realtime.subscribe((e) => {
			if (e.name !== 'job.outputs') return;
			const taskId = (e.data as { task_id: UUID }).task_id;
			if (run.tasks?.some((t) => t.id === taskId)) void load(id, data?.base?.run_id ?? null);
		});
	});

	let options = $derived(
		(data?.candidates ?? []).map((c) => ({
			value: c.run_id,
			label: `Run of ${relativeTime(c.created_at)}`,
			description: `${absoluteTime(c.created_at)} · ${c.status}${Object.keys(c.inputs).length ? ` · ${Object.entries(c.inputs).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ')}` : ''}`
		}))
	);
	let changedSteps = $derived((data?.steps ?? []).filter((s) => s.status !== 'same'));
	let sameSteps = $derived((data?.steps ?? []).filter((s) => s.status === 'same'));

	async function copy() {
		if (!data?.base) return;
		const md = changedSteps.map((s) => toMarkdown(s.outputs, s.name)).join('\n');
		try {
			await navigator.clipboard.writeText(`# Changes since the run of ${absoluteTime(data.base.created_at)}\n\n${md}`);
			snackbar.show('Copied the changes as Markdown');
		} catch {
			snackbar.error('Copying needs clipboard access');
		}
	}
</script>

{#if finished && failed}
	<p class="body-small text-error">{failed}</p>
{:else if finished && data}
	<section class="rounded-md border border-outline-variant/60">
		{#if !data.base}
			<p class="flex items-center gap-2 px-3 py-2 body-small text-on-surface-variant">
				<Icon name="difference" size={18} />The first run of this playbook in the project: the next one will show what changed since.
			</p>
		{:else}
			<button type="button" class="state-layer flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left" aria-expanded={open} onclick={() => (open = !open)}>
				<Icon name="difference" size={20} class="shrink-0 {anyChange(data.totals) ? 'text-primary' : 'text-on-surface-variant'}" />
				<span class="min-w-0 flex-1">
					<span class="block title-small text-on-surface">{countWords(data.totals)}</span>
					<span class="block body-small text-on-surface-variant">since the run {relativeTime(data.base.created_at)}{changedSteps.length ? ` · in ${changedSteps.map((s) => s.name).join(', ')}` : ''}</span>
				</span>
				<Icon name={open ? 'expand_less' : 'expand_more'} size={20} class="shrink-0 text-on-surface-variant" />
			</button>
			{#if open}
				<div class="flex flex-col gap-3 border-t border-outline-variant/60 p-3">
					<div class="flex flex-wrap items-center gap-2">
						<Select label="Compared with" value={data.base.run_id} {options} onchange={(v) => void load(run.id, v as UUID)} class="w-full max-w-sm" />
						<span class="flex-1"></span>
						{#if anyChange(data.totals)}<Button variant="text" icon="content_copy" onclick={copy}>Copy as Markdown</Button>{/if}
					</div>
					{#each changedSteps as s (s.step_id)}
						<div class="flex flex-col gap-2">
							<p class="flex items-baseline gap-2">
								<span class="title-small text-on-surface">{s.name}</span>
								<span class="body-small text-on-surface-variant">
									{s.status === 'new' ? 'A step the earlier run didn’t have results for · ' : s.status === 'gone' ? 'No results in this run · ' : ''}{countWords(s.totals)}
								</span>
							</p>
							<ResultChanges outputs={s.outputs} />
						</div>
					{/each}
					{#if sameSteps.length}
						<p class="body-small text-on-surface-variant">The same as before: {sameSteps.map((s) => s.name).join(', ')}.</p>
					{/if}
				</div>
			{/if}
		{/if}
	</section>
{/if}
