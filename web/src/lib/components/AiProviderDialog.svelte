<script lang="ts">
	import type { AiKind, AiProvider, AiProviderIn } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, Select, Switch, TextField } from '$lib/m3';
	import { confirmAction } from '$lib/confirm.svelte';

	/** Add or edit an LLM provider: its kind (OpenAI, Anthropic, Gemini, Vertex, Ollama, any
	 *  OpenAI-compatible server…), its settings, and a test with a model. */
	interface Props {
		open: boolean;
		kinds: AiKind[];
		provider: AiProvider | null;
		onsaved: () => void;
	}
	let { open = $bindable(false), kinds, provider, onsaved }: Props = $props();

	let kind = $state('');
	let name = $state('');
	let enabled = $state(true);
	let values = $state<Record<string, string>>({});
	let model = $state('');
	let models = $state<string[]>([]);
	let saving = $state(false);
	let testing = $state(false);
	let listing = $state(false);
	let result = $state<{ ok: boolean; text?: string; ms?: number; error?: string } | null>(null);
	let error = $state<string | null>(null);

	$effect(() => {
		if (!open) return;
		kind = provider?.kind ?? '';
		name = provider?.name ?? '';
		enabled = provider?.enabled ?? true;
		values = { ...(provider?.fields ?? {}) };
		result = error = null;
		// Not `spec`: it depends on `kind`, and choosing a kind must not re-run this reset.
		const usual = kinds.find((k) => k.kind === provider?.kind)?.models ?? [];
		models = usual;
		model = usual[0] ?? '';
	});
	let spec = $derived(kinds.find((k) => k.kind === kind));
	function choose(k: AiKind) {
		kind = k.kind;
		name = name || k.label.replace(/ \(.*\)$/, '');
		values = Object.fromEntries(k.fields.filter((f) => f.default).map((f) => [f.name, f.default]));
		models = k.models;
		model = k.models[0] ?? '';
	}
	const body = (): AiProviderIn => ({ name: name.trim(), kind, enabled, fields: values });
	async function test() {
		testing = true;
		result = null;
		try {
			result = await api().ai.test({ ...body(), model, id: provider?.id });
		} catch (err) {
			result = { ok: false, error: err instanceof ApiError ? err.message : 'Could not test' };
		} finally {
			testing = false;
		}
	}
	async function listModels() {
		listing = true;
		try {
			const r = await api().ai.models({ ...body(), id: provider?.id });
			models = r.models;
			if (r.error) result = { ok: false, error: r.error };
			if (models.length && !models.includes(model)) model = models[0];
		} finally {
			listing = false;
		}
	}
	async function save() {
		saving = true;
		error = null;
		try {
			if (provider) await api().ai.updateProvider(provider.id, body());
			else await api().ai.createProvider(body());
			open = false;
			onsaved();
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}
	async function remove() {
		if (!provider) return;
		if (!(await confirmAction({ title: `Remove ${provider.name}?`, message: 'Model chains that use it skip it from now on.', confirmLabel: 'Remove' }))) return;
		await api().ai.removeProvider(provider.id);
		open = false;
		onsaved();
	}
	const missing = $derived(!kind || !name.trim() || (spec?.fields ?? []).some((f) => f.required && !values[f.name]?.trim() && !(f.secret && provider?.kind === kind && provider?.secrets[f.name])));
	const GROUPS: Record<string, string> = { openai: 'Hosted', anthropic: 'Hosted', gemini: 'Hosted', vertex: 'Hosted', azure_openai: 'Hosted', ollama: 'Self-hosted', lmstudio: 'Self-hosted', vllm: 'Self-hosted' };
</script>

<Dialog bind:open title={provider ? provider.name : spec ? `New ${spec.label} provider` : 'New AI provider'} icon="auto_awesome" size="md">
	<div class="flex flex-col gap-4 pt-2 pb-2">
		{#if !kind}
			{#each ['Hosted', 'Self-hosted', 'OpenAI-compatible'] as group (group)}
				<div>
					<p class="mb-1.5 label-large text-on-surface-variant">{group}</p>
					<div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
						{#each kinds.filter((k) => (GROUPS[k.kind] ?? 'OpenAI-compatible') === group) as k (k.kind)}
							<button type="button" class="state-layer rounded-md border border-outline-variant px-3 py-2.5 text-left label-large text-on-surface" onclick={() => choose(k)}>{k.label}</button>
						{/each}
					</div>
				</div>
			{/each}
		{:else if spec}
			{#if spec.help}<p class="body-small text-on-surface-variant">{spec.help}</p>{/if}
			<TextField label="Name" bind:value={name} required />
			{#each spec.fields as f (f.name)}
				{@const kept = f.secret && provider?.kind === kind && provider?.secrets[f.name]}
				<TextField
					label={f.label}
					value={values[f.name] ?? ''}
					oninput={(e) => (values = { ...values, [f.name]: (e.currentTarget as HTMLInputElement).value })}
					type={f.type === 'password' ? 'password' : 'text'}
					multiline={f.type === 'textarea'}
					rows={6}
					mono={f.type !== 'text'}
					required={f.required && !kept}
					placeholder={kept ? 'Saved; leave empty to keep it' : f.placeholder}
					supporting={kept ? `Saved (hidden). ${f.help}` : f.help}
					autocomplete="off"
				/>
			{/each}
			<div class="flex flex-wrap items-end gap-2 rounded-md bg-surface-container p-3">
				{#if models.length}
					<Select class="min-w-56 flex-1" label="Model to test" value={model} onchange={(v) => (model = v)} options={models.map((x) => ({ value: x, label: x }))} />
				{:else}
					<TextField class="min-w-56 flex-1" label="Model to test" bind:value={model} mono />
				{/if}
				<Button variant="text" icon="list" loading={listing} disabled={missing} onclick={listModels}>List models</Button>
				<Button variant="tonal" icon="send" loading={testing} disabled={missing || !model} onclick={test}>Test</Button>
			</div>
			{#if result}
				<p class="flex items-start gap-1.5 body-small {result.ok ? 'text-primary' : 'text-error'}">
					<Icon name={result.ok ? 'check_circle' : 'error'} size={16} class="mt-0.5 shrink-0" />
					<span class="break-words">{result.ok ? `It answered “${result.text}” in ${result.ms} ms.` : result.error}</span>
				</p>
			{/if}
			<Switch bind:checked={enabled} label="Enabled" />
			{#if error}<p class="body-small text-error">{error}</p>{/if}
		{/if}
	</div>
	{#snippet actions()}
		{#if provider}<Button variant="text" icon="delete" class="mr-auto text-error!" onclick={remove}>Remove</Button>
		{:else if kind}<Button variant="text" icon="arrow_back" class="mr-auto" onclick={() => (kind = '')}>Kinds</Button>{/if}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		{#if kind}<Button icon="check" loading={saving} disabled={missing} onclick={save}>{provider ? 'Save' : 'Add provider'}</Button>{/if}
	{/snippet}
</Dialog>
