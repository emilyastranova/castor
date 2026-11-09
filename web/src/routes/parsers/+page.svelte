<script lang="ts">
	import type { ParserInfo, ParserOption, ParseTestResult } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { page } from '$app/state';
	import { replaceState } from '$app/navigation';
	import { untrack } from 'svelte';
	import OutputCard from '$lib/components/OutputCard.svelte';
	import ParserEditor from '$lib/components/ParserEditor.svelte';
	import LibraryTransfer from '$lib/components/LibraryTransfer.svelte';
	import { canEdit } from '$lib/session';
	import { confirmAction } from '$lib/confirm.svelte';
	import { onMount } from 'svelte';
	import { Button, Chip, Icon, IconButton, Progress, Switch, TextField, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { bytes } from '$lib/format';
	import { aiStatus } from '$lib/ai.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let parsers = $state<ParserInfo[]>(data.parsers);
	let builtins = $derived(parsers.filter((p) => p.origin === 'builtin'));
	let library = $derived(parsers.filter((p) => p.origin === 'user'));
	const MAX_INPUT = 2 * 1024 * 1024; // matches the server's test limit
	const editable = $derived(canEdit());
	const NEW = '__new';

	// Option values are kept as typed text (booleans as booleans) and converted on send.
	type Draft = { input: string; options: Record<string, string | boolean> };
	const drafts = $state<Record<string, Draft>>(
		untrack(() => Object.fromEntries(data.parsers.map((p) => [p.name, { input: '', options: {} }])))
	);

	let selectedName = $state(
		untrack(() => (data.parsers.some((p) => p.name === page.url.searchParams.get('parser')) ? page.url.searchParams.get('parser')! : data.parsers[0]?.name))
	);
	let selected = $derived(parsers.find((p) => p.name === selectedName) ?? parsers[0]);
	let draft = $derived(drafts[selectedName] ?? { input: '', options: {} });
	let editorDirty = $state(false);

	async function select(name: string) {
		if (name === selectedName) return;
		if (editorDirty && !(await confirmAction({ title: 'Discard your changes?', message: 'The parser you edited hasn’t been saved.', confirmLabel: 'Discard', danger: true }))) return;
		editorDirty = false;
		newFrom = null;
		selectedName = name;
		const url = new URL(page.url);
		for (const k of ['new', 'job', 'output', 'draft']) url.searchParams.delete(k);
		if (name === NEW) url.searchParams.set('new', '1');
		else url.searchParams.set('parser', name);
		replaceState(url, {});
	}

	// ---- a new parser, maybe for a job output its parser couldn't read (?new=1&job=&output=) ----
	let newFrom = $state<{ yaml: string; sample: string; from: string; type: string } | null>(null);
	onMount(async () => {
		const q = page.url.searchParams;
		if (!q.get('new')) return;
		const job = q.get('job');
		const output = q.get('output');
		let yamlDraft = '';
		if (q.get('draft')) {
			try {
				const saved = JSON.parse(sessionStorage.getItem('castor.parserDraft') ?? 'null');
				if (saved?.job === job && saved?.output === output) yamlDraft = saved.yaml;
				sessionStorage.removeItem('castor.parserDraft');
			} catch {
				/* start from the template */
			}
		}
		if (job && output) {
			try {
				const input = await api().jobs.outputInput(job, output);
				newFrom = { yaml: yamlDraft, sample: input.text, from: `the ${output} output of a job (${input.sources.join(', ')})`, type: input.type };
			} catch (err) {
				snackbar.error(err instanceof ApiError ? err.message : 'Could not load that output');
				newFrom = { yaml: yamlDraft, sample: '', from: '', type: '' };
			}
		} else newFrom = { yaml: yamlDraft, sample: '', from: '', type: '' };
		selectedName = NEW;
	});
	function startNew() {
		void select(NEW).then(() => {
			if (selectedName === NEW && !newFrom) newFrom = { yaml: '', sample: '', from: '', type: '' };
		});
	}
	async function saved(p: ParserInfo) {
		parsers = await api().parsers.list();
		drafts[p.name] ??= { input: '', options: {} };
		editorDirty = false;
		newFrom = null;
		selectedName = '';
		await select(p.name);
	}
	async function deleted(name: string) {
		parsers = parsers.filter((p) => p.name !== name);
		editorDirty = false;
		selectedName = '';
		await select(parsers[0]?.name ?? '');
	}

	// ---- options: text in the form <-> JSON for the API ----
	const asText = (v: unknown) => (Array.isArray(v) ? v.join(', ') : v === null || v === undefined ? '' : String(v));

	function toValue(opt: ParserOption, raw: string | boolean | undefined): unknown {
		if (raw === undefined || raw === '') return undefined;
		if (opt.kind === 'boolean') return raw;
		const text = String(raw).trim();
		if (opt.kind === 'number') return Number.isFinite(Number(text)) ? Number(text) : text;
		if (opt.kind === 'list') {
			const items = text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
			return items.every((s) => /^-?\d+$/.test(s)) ? items.map(Number) : items;
		}
		// e.g. regex `group`: a number or a name. Otherwise keep text (patterns) exactly as typed.
		if (typeof opt.default === 'number' && /^\d+$/.test(text)) return Number(text);
		return String(raw);
	}

	function optionsFor(p: ParserInfo, d: Draft): Record<string, unknown> {
		const out: Record<string, unknown> = {};
		for (const opt of p.options) {
			const v = toValue(opt, d.options[opt.name]);
			if (v !== undefined) out[opt.name] = v;
		}
		return out;
	}

	function boolValue(opt: ParserOption): boolean {
		const v = draft.options[opt.name];
		return typeof v === 'boolean' ? v : Boolean(opt.default);
	}

	function setOption(name: string, value: string | boolean) {
		drafts[selectedName].options = { ...draft.options, [name]: value };
	}

	// ---- AI: regex options from a description of what to pull out of the sample ----
	$effect(() => void aiStatus.load());
	let want = $state('');
	let suggesting = $state(false);
	let suggestion = $state<{ explanation: string; count: number; model: string } | null>(null);
	async function suggestRegex() {
		suggesting = true;
		try {
			const r = await api().ai.regex(draft.input, want);
			drafts[selectedName].options = Object.fromEntries(
				Object.entries(r.options).map(([k, v]) => [k, typeof v === 'boolean' ? v : asText(v)])
			);
			suggestion = { explanation: r.explanation, count: r.count, model: r.model };
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'The AI could not answer');
		} finally {
			suggesting = false;
		}
	}

	// ---- sample input ----
	function insertSample() {
		const d = drafts[selectedName];
		d.input = selected.sample;
		const fromSample = Object.fromEntries(
			Object.entries(selected.sample_options).map(([k, v]) => [k, typeof v === 'boolean' ? v : asText(v)])
		);
		d.options = { ...d.options, ...fromSample };
	}

	let fileInput: HTMLInputElement | undefined = $state();
	async function openFile(e: Event) {
		const file = (e.currentTarget as HTMLInputElement).files?.[0];
		(e.currentTarget as HTMLInputElement).value = '';
		if (!file) return;
		if (file.size > MAX_INPUT) {
			snackbar.error(`${file.name} is ${bytes(file.size)}; test input is limited to ${bytes(MAX_INPUT)}`);
			return;
		}
		drafts[selectedName].input = await file.text();
	}

	// ---- live parsing (debounced; stale responses are dropped) ----
	let result = $state<ParseTestResult | null>(null);
	let resultFor = $state('');
	let running = $state(false);
	let failed = $state<string | null>(null);
	let seq = 0;

	$effect(() => {
		const p = selected;
		const input = draft.input;
		const options = optionsFor(p, draft);
		const mine = ++seq;
		if (!input.trim()) {
			result = null;
			failed = null;
			running = false;
			return;
		}
		running = true;
		const timer = setTimeout(async () => {
			try {
				const r = await api().parsers.test(p.name, input, options);
				if (mine !== seq) return;
				result = r;
				resultFor = p.name;
				failed = null;
			} catch (err) {
				if (mine !== seq) return;
				failed = err instanceof ApiError ? err.message : 'Could not reach the server';
			} finally {
				if (mine === seq) running = false;
			}
		}, 300);
		return () => clearTimeout(timer);
	});

	let shown = $derived(result && resultFor === selectedName ? result : null);
	let inputBytes = $derived(new Blob([draft.input]).size);

	// ---- "use in a command" snippet ----
	const OUTPUT_NAMES: Record<string, string> = {
		'list<host>': 'hosts',
		'list<service>': 'services',
		'list<url>': 'urls',
		'list<string>': 'values',
		string: 'text'
	};
	const yamlScalar = (v: unknown): string => {
		if (typeof v === 'string') return /^[\w./-]+$/.test(v) && !/^(true|false|null|\d+)$/i.test(v) ? v : `'${v.replaceAll("'", "''")}'`;
		if (Array.isArray(v)) return `[${v.map(yamlScalar).join(', ')}]`;
		return String(v);
	};
	let snippet = $derived.by(() => {
		const opts = Object.entries(optionsFor(selected, draft)).filter(([k, v]) => {
			const def = selected.options.find((o) => o.name === k)?.default;
			return JSON.stringify(v) !== JSON.stringify(def);
		});
		return [
			'outputs:',
			`  - name: ${OUTPUT_NAMES[selected.type] ?? 'result'}`,
			`    parser: ${selected.name}`,
			'    source: "{{ work }}/output.txt"   # file the tool writes; omit to parse stdout',
			...(opts.length ? ['    options:', ...opts.map(([k, v]) => `      ${k}: ${yamlScalar(v)}`)] : [])
		].join('\n');
	});

	async function copySnippet() {
		await navigator.clipboard.writeText(snippet + '\n');
		snackbar.show('Copied output definition');
	}
</script>

<svelte:head><title>Parsers · Castor</title></svelte:head>

<TopAppBar title="Parsers" subtitle="Turn tool output into typed results · try them live on sample data">
	{#snippet actions()}
		<LibraryTransfer noun="parsers" request={{ parsers: library.map((p) => p.name), label: 'castor-parsers' }} count={library.length} onimported={async () => (parsers = await api().parsers.list())} />
		{#if editable}<Button icon="add" class="ml-2" onclick={startNew}>New parser</Button>{/if}
	{/snippet}
</TopAppBar>

<div class="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-6 pb-8 lg:flex-row">
	<!-- parser list -->
	<div class="-mx-6 flex shrink-0 gap-2 overflow-x-auto px-6 pt-2 lg:hidden" role="tablist" aria-label="Parsers">
		{#each parsers as p (p.name)}
			<Chip variant="filter" selected={p.name === selectedName} role="tab" aria-selected={p.name === selectedName} onclick={() => select(p.name)}>
				<span class="font-mono">{p.name}</span>
			</Chip>
		{/each}
	</div>
	<nav class="hidden w-72 shrink-0 flex-col gap-1 overflow-y-auto pt-2 lg:flex" aria-label="Parsers">
		{#if library.length || selectedName === NEW}
			<p class="px-4 pt-1 pb-1 label-medium text-on-surface-variant">Yours</p>
			{#if selectedName === NEW}
				<div class="flex items-center gap-2 rounded-lg bg-secondary-container px-4 py-3 text-on-secondary-container">
					<Icon name="add" size={18} /><span class="title-small">New parser</span>
				</div>
			{/if}
			{#each library as p (p.name)}
				<button
					type="button"
					class="state-layer focus-ring flex flex-col items-start gap-1 rounded-lg px-4 py-3 text-left transition-colors {p.name === selectedName
						? 'bg-secondary-container text-on-secondary-container'
						: 'text-on-surface'}"
					aria-current={p.name === selectedName ? 'true' : undefined}
					onclick={() => select(p.name)}
				>
					<span class="font-mono title-small">{p.name}</span>
					<span class="font-mono text-[11px] opacity-75">{p.type}</span>
					<span class="line-clamp-2 body-small opacity-80">{p.title || p.description}</span>
				</button>
			{/each}
			<p class="px-4 pt-3 pb-1 label-medium text-on-surface-variant">Built in</p>
		{/if}
		{#each builtins as p (p.name)}
			<button
				type="button"
				class="state-layer focus-ring flex flex-col items-start gap-1 rounded-lg px-4 py-3 text-left transition-colors {p.name === selectedName
					? 'bg-secondary-container text-on-secondary-container'
					: 'text-on-surface'}"
				aria-current={p.name === selectedName ? 'true' : undefined}
				onclick={() => select(p.name)}
			>
				<span class="flex w-full items-center gap-2">
					<span class="font-mono title-small">{p.name}</span>
					<span class="flex-1"></span>
					{#if p.options.length}<Icon name="tune" size={16} class="opacity-70" />{/if}
				</span>
				<span class="font-mono text-[11px] opacity-75">{p.type}</span>
				<span class="line-clamp-2 body-small opacity-80">{p.description}</span>
			</button>
		{/each}
	</nav>

	<!-- a parser in the library, or a new one: its definition, checked live -->
	{#if selectedName === NEW && newFrom}
		<ParserEditor parser={null} initialYaml={newFrom.yaml} initialSample={newFrom.sample} sampleFrom={newFrom.from} wantType={newFrom.type} {editable} bind:dirty={editorDirty} onsaved={saved} ondeleted={deleted} oncancel={() => { editorDirty = false; void select(builtins[0]?.name ?? ''); }} />
	{:else if selectedName === NEW}
		<div class="flex flex-1 items-center justify-center pt-8"><Progress variant="circular" size={24} /></div>
	{:else if selected && selected.origin === 'user'}
		{#key selected.name}
			<ParserEditor parser={selected} {editable} bind:dirty={editorDirty} onsaved={saved} ondeleted={deleted} />
		{/key}
	<!-- a built-in: try it live on sample data -->
	{:else if selected}
		<div class="flex min-w-0 flex-1 flex-col gap-4 pt-2">
			<div class="flex flex-wrap items-center gap-3">
				<span class="flex size-10 items-center justify-center rounded-md bg-primary-container text-on-primary-container"><Icon name="data_object" /></span>
				<div class="min-w-0 flex-1">
					<h2 class="font-mono title-large text-on-surface">{selected.name}</h2>
					<p class="body-medium text-on-surface-variant">{selected.description} · produces <code class="font-mono">{selected.type}</code></p>
				</div>
			</div>

			{#if selected.options.length}
				<section class="flex flex-col gap-3 rounded-lg bg-surface-container-low p-4">
					<h3 class="flex items-center gap-2 title-small text-on-surface"><Icon name="tune" size={20} />Options</h3>
					{#if selected.name === 'regex' && aiStatus.enabled}
						<form class="flex flex-wrap items-end gap-2" onsubmit={(e) => { e.preventDefault(); void suggestRegex(); }}>
							<TextField label="Describe what to pull out" bind:value={want} class="min-w-64 flex-1" placeholder="e.g. every IPv4 address with an open port 445" supporting={draft.input.trim() ? 'The AI writes the pattern and it’s tried on the sample below' : 'Paste a sample below first'} />
							<Button variant="tonal" type="submit" icon="auto_awesome" loading={suggesting} disabled={!draft.input.trim() || want.trim().length < 2}>Suggest</Button>
						</form>
						{#if suggestion}<p class="body-small text-on-surface-variant">{suggestion.explanation} {suggestion.count} match{suggestion.count === 1 ? '' : 'es'} in the sample · {suggestion.model}</p>{/if}
					{/if}
					<div class="grid gap-4 md:grid-cols-2">
						{#each selected.options as opt (opt.name)}
							{#if opt.kind === 'boolean'}
								<div class="flex flex-col gap-1">
									<Switch checked={boolValue(opt)} label={opt.name} onchange={(v) => setOption(opt.name, v)} />
									<p class="pl-16 body-small text-on-surface-variant">{opt.description}</p>
									{#if shown?.option_errors[opt.name]}<p class="pl-16 body-small text-error">{shown.option_errors[opt.name]}</p>{/if}
								</div>
							{:else}
								<TextField
									label={opt.name}
									required={opt.required}
									mono
									value={asText(draft.options[opt.name])}
									placeholder={opt.example !== null && opt.example !== undefined ? `e.g. ${asText(opt.example)}` : asText(opt.default) ? `default: ${asText(opt.default)}` : ''}
									error={shown?.option_errors[opt.name] ?? null}
									supporting={opt.description + (opt.kind === 'list' ? ' · comma separated' : '')}
									autocomplete="off"
									oninput={(e) => setOption(opt.name, (e.currentTarget as HTMLInputElement).value)}
								/>
							{/if}
						{/each}
					</div>
				</section>
			{/if}

			<div class="grid min-h-0 gap-4 xl:grid-cols-2">
				<!-- input -->
				<section class="flex min-w-0 flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface">
					<header class="flex min-h-12 items-center gap-1 border-b border-outline-variant py-1 pr-1 pl-4">
						<span class="title-small text-on-surface">Sample input</span>
						{#if draft.input}<span class="ml-2 body-small text-on-surface-variant">{bytes(inputBytes)}</span>{/if}
						<span class="flex-1"></span>
						<Button variant="text" icon="note_add" onclick={insertSample}>Insert sample</Button>
						<IconButton icon="upload_file" label="Load from a file" size="sm" onclick={() => fileInput?.click()} />
						<IconButton icon="backspace" label="Clear" size="sm" disabled={!draft.input} onclick={() => (drafts[selectedName].input = '')} />
						<input bind:this={fileInput} type="file" class="hidden" onchange={openFile} />
					</header>
					<textarea
						class="min-h-80 flex-1 resize-y bg-transparent p-4 font-mono text-[12.5px] leading-5 text-on-surface outline-none placeholder:text-on-surface-variant xl:min-h-[28rem]"
						placeholder="Paste tool output here, or insert the built-in sample"
						spellcheck="false"
						autocomplete="off"
						aria-label="Sample input"
						bind:value={drafts[selectedName].input}
					></textarea>
				</section>

				<!-- live result -->
				<div class="flex min-w-0 flex-col gap-3">
					<div class="flex h-6 items-center gap-2 label-medium text-on-surface-variant">
						{#if running}<Progress variant="circular" size={14} /> Parsing…
						{:else if shown && !shown.error}<Icon name="check_circle" size={16} class="text-primary" /> Parsed in {shown.elapsed_ms} ms
						{:else if draft.input.trim()}<Icon name="sync" size={16} /> Updates as you type
						{/if}
					</div>
					{#if failed}
						<p class="rounded-md bg-error-container px-3 py-2 body-medium text-on-error-container">{failed}</p>
					{:else if !draft.input.trim()}
						<div class="flex min-h-60 flex-col items-center justify-center gap-3 rounded-lg bg-surface-container p-8 text-center">
							<Icon name="science" size={40} class="text-on-surface-variant" />
							<p class="max-w-sm body-medium text-on-surface-variant">Paste output from the tool, or try it on built-in sample data. The result updates as you type and change options.</p>
							<Button variant="tonal" icon="note_add" onclick={insertSample}>Insert sample</Button>
						</div>
					{:else if shown}
						{#key selectedName}
							<OutputCard
								name={OUTPUT_NAMES[shown.type] ?? 'result'}
								type={shown.type}
								value={shown.value}
								error={Object.keys(shown.option_errors).length ? 'fix the options above' : shown.error}
								showDown
							/>
						{/key}
					{/if}

					<section class="flex flex-col overflow-hidden rounded-lg bg-surface-container-low">
						<header class="flex min-h-11 items-center gap-2 py-1 pr-1 pl-4">
							<Icon name="menu_book" size={18} class="text-on-surface-variant" />
							<span class="title-small text-on-surface">Use in a command</span>
							<span class="flex-1"></span>
							<IconButton icon="content_copy" label="Copy output definition" size="sm" onclick={copySnippet} />
						</header>
						<pre class="overflow-auto px-4 pb-3 font-mono text-[12.5px] leading-5 text-on-surface">{snippet}</pre>
						<p class="px-4 pb-3 body-small text-on-surface-variant">Paste under a command's YAML on the <a class="text-primary underline" href="/library">Library</a>. Options left at their defaults are omitted.</p>
					</section>
				</div>
			</div>
		</div>
	{/if}
</div>
