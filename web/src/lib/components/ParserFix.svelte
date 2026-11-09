<script lang="ts">
	import type { ParserProposal, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon, Progress, Switch, TextField } from '$lib/m3';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { aiStatus } from '$lib/ai.svelte';
	import { canEdit } from '$lib/session';
	import OutputCard from './OutputCard.svelte';

	/** An output its parser couldn't read: what the AI drafted for it (or the offer to draft
	 *  one), checked on this very output. Using it reads the output again for this job (and
	 *  carries a paused playbook step on); keeping it in the library is a separate choice. */
	interface Props {
		jobId: UUID;
		output: string;
		parser: string;
		/** In a step's decision panel: a paused run carries on when it's used. */
		inRun?: boolean;
	}
	let { jobId, output, parser, inRun = false }: Props = $props();

	let proposals = $state<ParserProposal[]>([]);
	let loaded = $state(false);
	$effect(() => void aiStatus.load());
	$effect(() => {
		const id = jobId;
		let cancelled = false;
		api()
			.jobs.parserProposals(id)
			.then((list) => {
				if (!cancelled) proposals = list;
			})
			.catch(() => {})
			.finally(() => (loaded = true));
		const off = realtime.subscribe((e) => {
			if (e.name !== 'job.parser_proposal') return;
			const p = e.data as ParserProposal;
			if (p.job_id !== id) return;
			proposals = [...proposals.filter((x) => x.id !== p.id), p];
		});
		return () => {
			cancelled = true;
			off();
		};
	});

	/** The latest one for this output that hasn't been put aside. */
	let current = $derived(
		proposals
			.filter((p) => p.output_name === output && p.status !== 'dismissed')
			.sort((a, b) => a.created_at.localeCompare(b.created_at))
			.at(-1) ?? null
	);
	let draftType = $derived(current?.yaml.match(/^type:\s*(\S+)/m)?.[1] ?? 'list<string>');
	const editable = $derived(canEdit());
	const writeLink = $derived(`/parsers?new=1&job=${jobId}&output=${encodeURIComponent(output)}`);

	let busy = $state<'draft' | 'use' | 'keep' | 'dismiss' | null>(null);
	let keeping = $state(false);
	let keepId = $state('');
	let useInCommand = $state(true);
	let showYaml = $state(false);

	async function draft() {
		busy = 'draft';
		try {
			const p = await api().jobs.draftParser(jobId, output);
			proposals = [...proposals.filter((x) => x.id !== p.id), p];
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not ask the AI');
		} finally {
			busy = null;
		}
	}
	async function use(saveAs?: string) {
		if (!current) return;
		busy = saveAs ? 'keep' : 'use';
		try {
			const p = await api().parserProposals.apply(current.id, saveAs, !!saveAs && useInCommand);
			proposals = [...proposals.filter((x) => x.id !== p.id), p];
			keeping = false;
			const done = saveAs ? `Kept as ${saveAs} and read ${output} with it` : `Read ${output} with the drafted parser`;
			snackbar.show(p.command_note ? `${done}. ${p.command_note}` : done);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not use it');
		} finally {
			busy = null;
		}
	}
	async function dismiss() {
		if (!current) return;
		busy = 'dismiss';
		try {
			const p = await api().parserProposals.dismiss(current.id);
			proposals = [...proposals.filter((x) => x.id !== p.id), p];
		} finally {
			busy = null;
		}
	}
	function startKeeping() {
		keepId = (current?.yaml.match(/^id:\s*(\S+)/m)?.[1] ?? output).replace(/[^a-z0-9_.-]/g, '-');
		keeping = true;
	}
	/** Open the draft on the Parsers page to change it before keeping it. */
	function editFirst() {
		if (!current) return;
		try {
			sessionStorage.setItem('castor.parserDraft', JSON.stringify({ yaml: current.yaml, job: jobId, output }));
		} catch {
			/* the page starts from the output instead */
		}
		location.href = `${writeLink}&draft=1`;
	}
</script>

{#if loaded}
	<section class="flex flex-col gap-2 rounded-md border border-outline-variant bg-surface-container-low p-3" aria-label="A parser for {output}">
		{#if !current}
			<p class="flex items-start gap-2 body-medium text-on-surface">
				<Icon name="help" size={18} class="mt-0.5 shrink-0 text-on-surface-variant" />
				<span>This isn’t in the format <code class="font-mono text-[12.5px]">{parser}</code> reads. A parser written for it can read it{inRun ? ' and the run carries on' : ''}.</span>
			</p>
			{#if editable}
				<div class="flex flex-wrap gap-2">
					{#if aiStatus.enabled}<Button variant="tonal" icon="auto_awesome" loading={busy === 'draft'} onclick={draft}>Draft a parser</Button>{/if}
					<Button variant="text" icon="edit" href={writeLink}>Write one</Button>
				</div>
			{/if}
		{:else if current.status === 'drafting'}
			<p class="flex items-center gap-2 body-medium text-on-surface-variant">
				<Progress variant="circular" size={16} />The AI is writing a parser for this output and trying it on it…
			</p>
		{:else if current.status === 'failed'}
			<p class="flex items-start gap-2 body-medium text-on-surface">
				<Icon name="error" size={18} class="mt-0.5 shrink-0 text-error" />
				<span>The AI couldn’t write a parser that reads this output{current.problems[0] ? `: ${current.problems[0]}` : '.'}</span>
			</p>
			{#if editable}
				<div class="flex flex-wrap gap-2">
					{#if aiStatus.enabled}<Button variant="text" icon="refresh" loading={busy === 'draft'} onclick={draft}>Try again</Button>{/if}
					<Button variant="text" icon="edit" href={writeLink}>Write one</Button>
				</div>
			{/if}
		{:else}
			<p class="flex items-start gap-2 body-medium text-on-surface">
				<Icon name={current.status === 'applied' ? 'check_circle' : 'auto_awesome'} size={18} class="mt-0.5 shrink-0 text-primary" />
				<span>
					{#if current.status === 'applied'}
						Read with {current.saved_as ? `the parser ${current.saved_as}` : 'a parser the AI drafted'} ({current.applied_by}).
					{:else}
						{current.automatic ? 'While the run waited, the AI' : 'The AI'} wrote a parser that reads
						<strong>{current.count} result{current.count === 1 ? '' : 's'}</strong> from this output.
					{/if}
					{#if current.notes}<span class="text-on-surface-variant"> {current.notes}</span>{/if}
				</span>
			</p>
			{#if current.status === 'ready'}
				<OutputCard name="preview" type={draftType} value={current.preview} meta="first {Math.min(20, current.count)} · {current.model}" />
			{/if}
			<button type="button" class="self-start body-small text-primary underline-offset-4 hover:underline" aria-expanded={showYaml} onclick={() => (showYaml = !showYaml)}>
				{showYaml ? 'Hide' : 'Show'} the parser
			</button>
			{#if showYaml}
				<pre class="overflow-x-auto rounded-sm bg-surface-container-highest p-3 font-mono text-[12px] leading-5 whitespace-pre text-on-surface">{current.yaml}</pre>
				<p class="body-small text-on-surface-variant">A parser is a format and its fields, or JavaScript run in a sandbox with no files, network or imports.</p>
			{/if}
			{#if editable}
				{#if keeping}
					<form class="flex flex-wrap items-end gap-2" onsubmit={(e) => { e.preventDefault(); void use(keepId); }}>
						<TextField label="Keep as parser" mono bind:value={keepId} class="min-w-56 flex-1" supporting="Commands can name it in their outputs" />
						<Button type="submit" icon="bookmark_add" loading={busy === 'keep'} disabled={!keepId.trim()}>Keep it</Button>
						<Button variant="text" onclick={() => (keeping = false)}>Cancel</Button>
						<div class="w-full">
							<Switch bind:checked={useInCommand} label="Read {output} with it in this card’s command from now on" />
							<p class="pl-16 body-small text-on-surface-variant">Only for commands you made; a built-in stays as it is (duplicate it in the Library first).</p>
						</div>
					</form>
				{:else}
					<div class="flex flex-wrap gap-2">
						{#if current.status === 'ready'}
							<Button icon="check" loading={busy === 'use'} disabled={busy !== null} onclick={() => use()}>{inRun ? 'Use it and carry on' : 'Use it'}</Button>
						{/if}
						{#if !current.saved_as}
							<Button variant="tonal" icon="bookmark_add" disabled={busy !== null} onclick={startKeeping}>Keep in the library…</Button>
							<Button variant="text" icon="edit" disabled={busy !== null} onclick={editFirst}>Edit it first</Button>
						{/if}
						{#if current.status === 'ready'}
							<Button variant="text" loading={busy === 'dismiss'} disabled={busy !== null} onclick={dismiss}>Not this</Button>
						{/if}
					</div>
				{/if}
			{/if}
		{/if}
	</section>
{/if}
