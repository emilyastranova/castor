<script lang="ts">
	import type { AiProvider } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon, IconButton, Select, TextField, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import AiProviderDialog from '$lib/components/AiProviderDialog.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let providers = $state<AiProvider[]>(data.providers);
	// svelte-ignore state_referenced_locally
	let chains = $state<Record<string, { provider: string; model: string }[]>>(JSON.parse(JSON.stringify(data.settings.ai_chains ?? {})));
	// svelte-ignore state_referenced_locally
	let usage = $state(data.usage);
	let dialogOpen = $state(false);
	let editing = $state<AiProvider | null>(null);
	let dirty = $state(false);
	let saving = $state(false);

	const PURPOSES = [
		{ key: 'default', title: 'Default', detail: 'Generating commands, tools, playbooks and variable kinds, regex suggestions, explaining failures.' },
		{ key: 'resolver', title: 'Playbook resolver', detail: 'Deciding paused steps (on_failure: ai, and Ask AI). Empty: uses Default.' },
		{ key: 'summary', title: 'Run summaries', detail: 'A few lines on a card when a run finishes (projects that turn them on, or Summarise). Empty: uses Default.' }
	];
	const kindLabel = (k: string) => data.kinds.find((x) => x.kind === k)?.label ?? k;

	async function reload() {
		providers = await api().ai.providers();
		usage = await api().ai.usage();
	}
	function modelsFor(providerId: string): string[] {
		const p = providers.find((x) => x.id === providerId);
		return data.kinds.find((k) => k.kind === p?.kind)?.models ?? [];
	}
	function edit(purpose: string, fn: (list: { provider: string; model: string }[]) => { provider: string; model: string }[]) {
		chains = { ...chains, [purpose]: fn([...(chains[purpose] ?? [])]) };
		dirty = true;
	}
	function add(purpose: string) {
		const p = providers.find((x) => x.enabled) ?? providers[0];
		if (!p) return;
		edit(purpose, (l) => [...l, { provider: p.id, model: modelsFor(p.id)[0] ?? '' }]);
	}
	function move(purpose: string, i: number, by: number) {
		edit(purpose, (l) => {
			const [x] = l.splice(i, 1);
			l.splice(i + by, 0, x);
			return l;
		});
	}
	async function saveChains() {
		saving = true;
		try {
			const clean = Object.fromEntries(Object.entries(chains).map(([k, l]) => [k, l.filter((c) => c.provider && c.model.trim())]));
			const s = await api().settings.update({ ai_chains: clean });
			chains = JSON.parse(JSON.stringify(s.ai_chains));
			dirty = false;
			snackbar.show('Saved the model chains');
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save');
		} finally {
			saving = false;
		}
	}
	const tokens = (n: number) => (n >= 10000 ? `${Math.round(n / 1000)}k` : String(n));
</script>

<svelte:head><title>AI · Settings · Castor</title></svelte:head>

<TopAppBar title="AI" subtitle="LLM providers and the models Castor uses, with fallbacks">
	{#snippet actions()}
		<Button icon="add" onclick={() => { editing = null; dialogOpen = true; }}>New provider</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-6 pt-2">
		<section class="flex flex-col gap-2">
			<h2 class="title-medium text-on-surface">Providers</h2>
			{#each providers as p (p.id)}
				<Card variant="outlined" class="flex items-center gap-3 p-3 {p.enabled ? '' : 'opacity-60'}">
					<span class="flex size-9 shrink-0 items-center justify-center rounded-md bg-secondary-container text-on-secondary-container"><Icon name="auto_awesome" size={20} /></span>
					<button type="button" class="min-w-0 flex-1 text-left" onclick={() => { editing = p; dialogOpen = true; }}>
						<span class="block truncate title-small text-on-surface">{p.name}</span>
						<span class="block truncate body-small text-on-surface-variant">{kindLabel(p.kind)}{p.fields.base_url ? ` · ${p.fields.base_url}` : ''}{p.fields.project ? ` · ${p.fields.project}` : ''}{p.enabled ? '' : ' · disabled'}</span>
					</button>
					<IconButton icon="edit" label="Edit" onclick={() => { editing = p; dialogOpen = true; }} />
				</Card>
			{:else}
				<p class="body-medium text-on-surface-variant">No providers yet. Add OpenAI, Anthropic, Gemini (API key or Vertex AI), Azure OpenAI, Ollama or any OpenAI-compatible server.</p>
			{/each}
		</section>

		<section class="flex flex-col gap-3">
			<div class="flex items-center gap-3">
				<h2 class="flex-1 title-medium text-on-surface">Models</h2>
				{#if dirty}<Button icon="check" loading={saving} onclick={saveChains}>Save</Button>{/if}
			</div>
			<p class="-mt-2 body-small text-on-surface-variant">Each is tried in order; when one fails, times out or gives an answer Castor can't use, the next one gets the question. Mix providers for a fallback that doesn't share an outage.</p>
			{#each PURPOSES as purpose (purpose.key)}
				<Card variant="outlined" class="flex flex-col gap-2 p-4">
					<p class="title-small text-on-surface">{purpose.title}</p>
					<p class="body-small text-on-surface-variant">{purpose.detail}</p>
					{#each chains[purpose.key] ?? [] as c, i (i)}
						<div class="flex flex-wrap items-center gap-2">
							<span class="w-20 shrink-0 label-medium text-on-surface-variant">{i === 0 ? 'First' : `Fallback ${i}`}</span>
							<Select class="w-56" label="Provider" value={c.provider} onchange={(v) => edit(purpose.key, (l) => { l[i] = { ...l[i], provider: v }; return l; })} options={providers.map((p) => ({ value: p.id, label: p.name + (p.enabled ? '' : ' (disabled)') }))} />
							<TextField class="min-w-48 flex-1" label="Model" mono value={c.model} oninput={(e) => edit(purpose.key, (l) => { l[i] = { ...l[i], model: (e.currentTarget as HTMLInputElement).value }; return l; })} placeholder={modelsFor(c.provider)[0] ?? 'model name'} />
							<IconButton icon="arrow_upward" label="Earlier" size="sm" disabled={i === 0} onclick={() => move(purpose.key, i, -1)} />
							<IconButton icon="arrow_downward" label="Later" size="sm" disabled={i === (chains[purpose.key]?.length ?? 0) - 1} onclick={() => move(purpose.key, i, 1)} />
							<IconButton icon="close" label="Remove" size="sm" onclick={() => edit(purpose.key, (l) => l.filter((_, j) => j !== i))} />
						</div>
					{/each}
					<Button variant="text" icon="add" class="self-start" disabled={!providers.length} onclick={() => add(purpose.key)}>{chains[purpose.key]?.length ? 'Add a fallback' : 'Add a model'}</Button>
				</Card>
			{/each}
		</section>

		<section class="flex flex-col gap-2">
			<h2 class="title-medium text-on-surface">Recent calls</h2>
			<p class="body-small text-on-surface-variant">Today: {usage.calls_today} call{usage.calls_today === 1 ? '' : 's'} · {tokens(usage.input_tokens_today)} tokens in · {tokens(usage.output_tokens_today)} out</p>
			{#if usage.calls.length}
				<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container">
					{#each usage.calls as c (c.id)}
						<li class="flex items-start gap-3 px-3 py-2 body-small">
							<Icon name={c.ok ? 'check' : 'close'} size={16} class="mt-0.5 shrink-0 {c.ok ? 'text-primary' : 'text-error'}" />
							<span class="min-w-0 flex-1">
								<span class="text-on-surface">{c.purpose} · {c.provider_name} / <span class="font-mono">{c.model}</span></span>
								{#if c.error}<span class="block break-words text-error">{c.error}</span>{/if}
							</span>
							<span class="shrink-0 text-on-surface-variant">{c.input_tokens ?? '–'}/{c.output_tokens ?? '–'} · {(c.ms / 1000).toFixed(1)} s</span>
							<span class="w-20 shrink-0 text-right text-on-surface-variant" title={absoluteTime(c.created_at)}>{relativeTime(c.created_at)}</span>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="body-medium text-on-surface-variant">Nothing asked yet.</p>
			{/if}
		</section>
	</div>
</div>

<AiProviderDialog bind:open={dialogOpen} kinds={data.kinds} provider={editing} onsaved={reload} />
