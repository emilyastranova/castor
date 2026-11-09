<script lang="ts">
	import type { ParserCheck, ParserInfo } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Icon, Progress, Select, TextField } from '$lib/m3';
	import { aiStatus } from '$lib/ai.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import OutputCard from './OutputCard.svelte';
	import YamlPane from './library/YamlPane.svelte';

	/** A parser in the library (or a new one): its definition, checked live on sample output
	 *  as you type, and an AI that drafts one from that output. */
	interface Props {
		parser: ParserInfo | null;
		initialYaml?: string;
		initialSample?: string;
		/** Where the sample came from, e.g. "the hosts output of a job". */
		sampleFrom?: string;
		/** The type the results must have (e.g. what a command's output expects). */
		wantType?: string;
		editable: boolean;
		dirty?: boolean;
		onsaved: (p: ParserInfo) => void;
		ondeleted: (name: string) => void;
		oncancel?: () => void;
	}
	let { parser, initialYaml = '', initialSample = '', sampleFrom = '', wantType = '', editable, dirty = $bindable(false), onsaved, ondeleted, oncancel }: Props = $props();

	const TYPES = ['list<host>', 'list<service>', 'list<url>', 'list<smb_host>', 'list<string>', 'string', 'number', 'boolean'];
	const TEMPLATE = `id: my-parser
name: My parser
description: What it reads
type: list<host>
format: regex          # lines | regex | json | jsonl | csv | xml
pattern: '^Host: (?P<ip>\\S+)'
`;
	/** The sample lives in its own box here; on save it goes back in as the last key. */
	const withoutSample = (text: string) => text.replace(/^sample:[\s\S]*$/m, '').trimEnd() + '\n';
	// svelte-ignore state_referenced_locally
	const original = withoutSample(parser?.source_yaml || initialYaml || TEMPLATE);
	let yaml = $state(original);
	// svelte-ignore state_referenced_locally
	let sample = $state(initialSample || parser?.sample || '');
	let readOnly = $derived(!editable);
	$effect(() => {
		dirty = yaml !== original;
	});

	// ---- live check (debounced; stale answers dropped) ----
	let check = $state<ParserCheck | null>(null);
	let checking = $state(false);
	let seq = 0;
	$effect(() => {
		const text = yaml;
		const input = sample;
		const mine = ++seq;
		checking = true;
		const timer = setTimeout(async () => {
			try {
				const r = await api().parsers.validate(text, input.trim() ? input : undefined);
				if (mine === seq) check = r;
			} catch (err) {
				if (mine === seq) check = { ok: false, errors: [err instanceof ApiError ? err.message : 'Could not reach the server'], type: null, value: null, count: 0 };
			} finally {
				if (mine === seq) checking = false;
			}
		}, 350);
		return () => clearTimeout(timer);
	});

	// ---- AI ----
	$effect(() => void aiStatus.load());
	let hint = $state('');
	// svelte-ignore state_referenced_locally
	let draftType = $state(wantType || '');
	let drafting = $state(false);
	let draftNote = $state('');
	async function draftWithAi() {
		drafting = true;
		try {
			const d = await api().parsers.draft({ sample, type: draftType || null, hint, current: parser || yaml !== TEMPLATE ? yaml : '' });
			// Keep the id of an existing parser; a new one takes the AI's.
			yaml = parser ? d.yaml.replace(/^id:.*$/m, `id: ${parser.name}`) : d.yaml;
			draftNote = d.problems.length ? `The AI's draft still has a problem: ${d.problems[0]}` : `${d.notes ? d.notes + ' ' : ''}(${d.model})`;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'The AI could not answer');
		} finally {
			drafting = false;
		}
	}

	// ---- save ----
	let saving = $state(false);
	let error = $state<string | null>(null);
	/** The sample goes into the definition (the Parsers page and later edits use it). */
	function withSample(text: string): string {
		const body = withoutSample(text).trimEnd();
		const s = sample.trim() ? sample.slice(0, 8000).trimEnd() : '';
		return s ? `${body}\nsample: |\n${s.split('\n').map((l) => `  ${l}`).join('\n')}\n` : `${body}\n`;
	}
	async function save() {
		if (readOnly) return;
		saving = true;
		error = null;
		try {
			const text = withSample(yaml);
			const saved = parser ? await api().parsers.update(parser.name, text) : await api().parsers.create(text);
			snackbar.show(parser ? `Saved ${saved.name}` : `Added ${saved.name}`);
			dirty = false;
			onsaved(saved);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}
	async function remove() {
		if (!parser) return;
		if (!(await confirmAction({ title: `Delete ${parser.name}?`, message: 'It’s removed from the library. Results it already read stay.', confirmLabel: 'Delete parser', danger: true }))) return;
		try {
			await api().parsers.remove(parser.name);
			dirty = false;
			ondeleted(parser.name);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete');
		}
	}
	const typeOf = $derived(yaml.match(/^type:\s*(\S+)/m)?.[1] ?? 'list<string>');
</script>

<div class="flex min-w-0 flex-1 flex-col gap-4 pt-2">
	<header class="flex flex-wrap items-center gap-3">
		<span class="flex size-10 items-center justify-center rounded-md bg-tertiary-container text-on-tertiary-container"><Icon name="data_object" /></span>
		<div class="min-w-0 flex-1">
			<h2 class="font-mono title-large text-on-surface">{parser?.name ?? 'New parser'}</h2>
			<p class="body-medium text-on-surface-variant">
				{#if parser}
					{parser.title || parser.description} · produces <code class="font-mono">{parser.type}</code>
					{#if parser.used_by.length} · used by {parser.used_by.join(', ')}{/if}
				{:else}
					A definition of the output’s format and its fields. Declarative, never code.
				{/if}
			</p>
		</div>
		{#if parser && editable}
			<Button variant="text" icon="delete" disabled={parser.used_by.length > 0} title={parser.used_by.length ? 'Commands use it: change them first' : ''} onclick={remove}>Delete</Button>
		{/if}
	</header>

	{#if editable && aiStatus.enabled}
		<form class="flex flex-wrap items-end gap-2 rounded-lg bg-surface-container-low p-3" onsubmit={(e) => { e.preventDefault(); void draftWithAi(); }}>
			<Icon name="auto_awesome" size={20} class="mb-3 text-primary" />
			<TextField label="What to read (optional)" bind:value={hint} class="min-w-56 flex-1" placeholder="e.g. every open port with its service name" />
			<div class="w-44">
				<Select label="As" value={draftType} options={[{ value: '', label: 'Let it choose' }, ...TYPES.map((t) => ({ value: t, label: t }))]} onchange={(v) => (draftType = v)} />
			</div>
			<Button variant="tonal" type="submit" icon="auto_awesome" loading={drafting} disabled={!sample.trim()} title={sample.trim() ? 'The AI writes a parser and it’s tried on the sample' : 'Paste some output first'}>
				{parser || yaml !== TEMPLATE ? 'Change with AI' : 'Draft from the sample'}
			</Button>
			{#if draftNote}<p class="w-full pl-7 body-small text-on-surface-variant">{draftNote}</p>{/if}
		</form>
	{/if}

	<div class="grid min-h-0 gap-4 xl:grid-cols-2">
		<div class="flex min-w-0 flex-col gap-2">
			<YamlPane bind:value={yaml} readonly={readOnly} {error} label="Parser definition (YAML)" onsave={save} />
			{#if editable}
				<div class="flex flex-wrap items-center justify-end gap-2">
					<span class="mr-auto body-small text-on-surface-variant">Formats: lines, regex, json, jsonl, csv, xml. Ctrl+S saves.</span>
					{#if parser}
						<Button variant="text" disabled={!dirty} onclick={() => { yaml = original; error = null; }}>Discard changes</Button>
					{:else if oncancel}
						<Button variant="text" onclick={oncancel}>Cancel</Button>
					{/if}
					<Button icon="check" loading={saving} disabled={(!!parser && !dirty && sample === parser.sample) || (check !== null && !check.ok)} onclick={save}>
						{parser ? 'Save' : 'Add parser'}
					</Button>
				</div>
			{/if}
		</div>

		<div class="flex min-w-0 flex-col gap-3">
			<section class="flex min-w-0 flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface">
				<header class="flex min-h-11 items-center gap-2 border-b border-outline-variant py-1 pr-2 pl-4">
					<span class="title-small text-on-surface">Sample output</span>
					{#if sampleFrom}<span class="truncate body-small text-on-surface-variant">from {sampleFrom}</span>{/if}
				</header>
				<textarea
					class="min-h-48 resize-y bg-transparent p-4 font-mono text-[12.5px] leading-5 whitespace-pre text-on-surface outline-none placeholder:text-on-surface-variant"
					placeholder="Paste what the tool prints (it's saved with the parser as its sample)"
					spellcheck="false"
					autocomplete="off"
					wrap="off"
					aria-label="Sample output"
					bind:value={sample}
				></textarea>
			</section>
			<div class="flex h-6 items-center gap-2 label-medium text-on-surface-variant">
				{#if checking}<Progress variant="circular" size={14} /> Checking…
				{:else if check?.ok && check.count}<Icon name="check_circle" size={16} class="text-primary" /> Reads {check.count} result{check.count === 1 ? '' : 's'}
				{:else if check?.ok}<Icon name="check_circle" size={16} class="text-primary" /> The definition is valid; add a sample to try it
				{/if}
			</div>
			{#if check && !check.ok}
				<ul class="flex flex-col gap-1 rounded-md bg-error-container px-3 py-2 body-small text-on-error-container" role="alert">
					{#each check.errors as e (e)}<li class="break-words">{e}</li>{/each}
				</ul>
			{:else if check?.ok && check.value !== null && check.value !== undefined}
				<OutputCard name="results" type={check.type ?? typeOf} value={check.value} showDown />
			{/if}
		</div>
	</div>
</div>
