<script lang="ts">
	import type { Job, ManualSource, ParserCandidate, ParserInfo, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, Progress, Select, TextField } from '$lib/m3';
	import { aiStatus } from '$lib/ai.svelte';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Bring output in to be parsed (a comment's, a post's, a run's, or pasted): the parsers that read
	 *  something from it, best first, or the AI drafts one; it becomes a manual run on a card, whose
	 *  results become the project's assets like any run's. */
	interface Props {
		open: boolean;
		projectId: UUID;
		/** Where it comes from; empty: pasted here. */
		source?: ManualSource;
		/** The card it goes on; none: the source's card, else one chosen here (or a new one). */
		taskId?: UUID | null;
		/** The run it went on. */
		onparsed?: (job: Job, taskId: UUID) => void;
	}
	let { open = $bindable(false), projectId, source = {}, taskId = null, onparsed }: Props = $props();

	let text = $state('');
	let fetched = $state('');
	let candidates = $state<ParserCandidate[]>([]);
	let trying = $state(false);
	let tried = $state(false);
	let choice = $state('');
	let other = $state('');
	let allParsers = $state<ParserInfo[]>([]);
	let sourceTask = $state<UUID | null>(null);
	let cards = $state<Task[]>([]);
	let cardChoice = $state('');
	let saving = $state(false);
	const pasted = $derived(!source.comment_id && !source.post_id && !source.job_id);
	const edited = $derived(!pasted && tried && text !== fetched);

	$effect(() => void aiStatus.load());
	let openedFor = '';
	$effect(() => {
		if (!open) {
			openedFor = '';
			return;
		}
		const key = JSON.stringify(source);
		if (openedFor === key) return;
		openedFor = key;
		text = fetched = '';
		candidates = [];
		tried = false;
		choice = other = '';
		sourceTask = null;
		cardChoice = '';
		api().parsers.list().then((p) => (allParsers = p)).catch(() => {});
		if (!pasted) void tryNow(source);
	});
	$effect(() => {
		if (!open || taskId || sourceTask) return;
		api()
			.projects.board(projectId)
			.then((b) => (cards = b.tasks.filter((t) => !t.archived_at)))
			.catch(() => (cards = []));
	});

	async function tryNow(from: ManualSource) {
		trying = true;
		try {
			const got = await api().parsers.tryAll(from);
			text = fetched = got.text;
			candidates = got.candidates;
			sourceTask = got.task_id;
			tried = true;
			const best = got.candidates.find((c) => !c.generic) ?? got.candidates[0];
			choice = best ? `parser:${best.parser}` : aiStatus.enabled ? 'ai' : 'other';
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Couldn’t try the parsers');
		} finally {
			trying = false;
		}
	}

	const specific = $derived(candidates.filter((c) => !c.generic));
	const general = $derived(candidates.filter((c) => c.generic));
	const target = $derived(taskId ?? sourceTask ?? (cardChoice && cardChoice !== 'new' ? cardChoice : null));
	const cardOptions = $derived([
		{ value: 'new', label: 'A new card', description: 'Made for this output' },
		...cards.map((c) => ({ value: c.id, label: c.title, description: c.kind === 'command' ? 'Command card' : 'Card', group: 'Cards' }))
	]);
	const parserOptions = $derived(allParsers.map((p) => ({ value: p.name, label: p.name, description: `${p.type} · ${p.description}` })));
	const chosenParser = $derived(choice.startsWith('parser:') ? choice.slice(7) : choice === 'other' ? other : '');
	const ready = $derived(
		tried && !!text.trim() && !!(chosenParser || choice === 'ai') && (!!target || cardChoice === 'new') && !saving && !trying
	);

	function preview(c: ParserCandidate): string {
		const first = Array.isArray(c.preview) ? c.preview[0] : c.preview;
		if (first == null) return '';
		const said = typeof first === 'string' ? first : Object.entries(first as Record<string, unknown>).filter(([, v]) => v !== null && v !== '' && typeof v !== 'object').slice(0, 4).map(([k, v]) => `${k}: ${v}`).join(' · ');
		return said.length > 120 ? `${said.slice(0, 120)}…` : said;
	}

	async function parse() {
		if (!ready) return;
		saving = true;
		try {
			let card = target;
			if (!card) {
				const made = await api().projects.createTask(projectId, { title: 'Parsed output', kind: 'manual' });
				card = made.id;
			}
			const from = pasted ? { text } : edited ? { ...source, text } : source;
			const job = await api().tasks.manualRun(card, { ...from, ...(choice === 'ai' ? { ai: true } : { parser: chosenParser }) });
			snackbar.show(choice === 'ai' ? 'Brought it in: the AI is drafting a parser for it' : `Read it with ${chosenParser}`);
			open = false;
			onparsed?.(job, card);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Couldn’t parse it');
		} finally {
			saving = false;
		}
	}
</script>

<Dialog bind:open title="Parse output" icon="data_object" size="lg">
	<div class="flex flex-col gap-4 pb-2">
		<p class="body-medium text-on-surface-variant">
			It becomes a run on {taskId || sourceTask ? 'the card' : 'a card'}: what it reads shows in the card’s Output and becomes the project’s assets, like any run’s.
		</p>
		{#if trying && !tried}
			<p class="flex items-center gap-2 body-medium text-on-surface-variant"><Progress variant="circular" size={18} />Trying the parsers…</p>
		{:else}
			<TextField label={pasted ? 'The output' : 'The output (you can trim it)'} bind:value={text} multiline rows={8} mono
				placeholder="Paste what the tool printed" supporting={source.comment_id || source.post_id ? 'Its code blocks, when it has any' : ''} />
			{#if !tried || (pasted && text !== fetched) || edited}
				<div><Button variant="tonal" icon="manage_search" loading={trying} disabled={!text.trim()} onclick={() => tryNow({ text })}>
					{tried ? 'Try the parsers again' : 'Try the parsers'}</Button></div>
			{/if}
		{/if}

		{#if tried}
			<div class="flex flex-col gap-1 rounded-md border border-outline-variant/70 p-2" role="radiogroup" aria-label="Read it with">
				<span class="px-1 label-large text-on-surface">Read it with</span>
				{#each [...specific, ...general] as c, i (c.parser)}
					{#if i === specific.length && general.length}
						<span class="px-1 pt-2 label-medium text-on-surface-variant">General parsers</span>
					{/if}
					{@const on = choice === `parser:${c.parser}`}
					<button type="button" role="radio" aria-checked={on} class="state-layer flex w-full items-start gap-3 rounded-sm px-2 py-1.5 text-left" onclick={() => (choice = `parser:${c.parser}`)}>
						<Icon name={on ? 'radio_button_checked' : 'radio_button_unchecked'} size={20} class="mt-0.5 {on ? 'text-primary' : 'text-on-surface-variant'}" />
						<span class="min-w-0 flex-1">
							<span class="flex items-baseline gap-2">
								<span class="truncate font-mono body-medium text-on-surface">{c.parser}</span>
								<span class="shrink-0 body-small text-on-surface-variant">{c.count} {c.count === 1 ? 'result' : 'results'} · {c.type}</span>
							</span>
							{#if preview(c)}<span class="block truncate font-mono text-[12px] text-on-surface-variant">{preview(c)}</span>{/if}
						</span>
					</button>
				{:else}
					<p class="px-2 py-1 body-small text-on-surface-variant">No parser in the library reads this.</p>
				{/each}
				{#if aiStatus.enabled}
					<button type="button" role="radio" aria-checked={choice === 'ai'} class="state-layer flex w-full items-start gap-3 rounded-sm px-2 py-1.5 text-left" onclick={() => (choice = 'ai')}>
						<Icon name={choice === 'ai' ? 'radio_button_checked' : 'radio_button_unchecked'} size={20} class="mt-0.5 {choice === 'ai' ? 'text-primary' : 'text-on-surface-variant'}" />
						<span class="min-w-0 flex-1">
							<span class="flex items-center gap-1.5 body-medium text-on-surface"><Icon name="auto_awesome" size={16} class="text-primary" />Have the AI draft a parser</span>
							<span class="block body-small text-on-surface-variant">It writes one for this format and tries it on this output; you look at what it reads, and can keep it in the library.</span>
						</span>
					</button>
				{/if}
				<button type="button" role="radio" aria-checked={choice === 'other'} class="state-layer flex w-full items-start gap-3 rounded-sm px-2 py-1.5 text-left" onclick={() => (choice = 'other')}>
					<Icon name={choice === 'other' ? 'radio_button_checked' : 'radio_button_unchecked'} size={20} class="mt-0.5 {choice === 'other' ? 'text-primary' : 'text-on-surface-variant'}" />
					<span class="body-medium text-on-surface">Another parser</span>
				</button>
				{#if choice === 'other'}
					<div class="px-2 pb-1"><Select label="Parser" bind:value={other} options={parserOptions} /></div>
				{/if}
			</div>
		{/if}

		{#if tried && !taskId && !sourceTask}
			<Select label="Bring it into" bind:value={cardChoice} options={cardOptions} supporting="The card its run and results go on" />
		{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon={choice === 'ai' ? 'auto_awesome' : 'data_object'} loading={saving} disabled={!ready} onclick={parse}>{choice === 'ai' ? 'Bring it in' : 'Parse'}</Button>
	{/snippet}
</Dialog>
