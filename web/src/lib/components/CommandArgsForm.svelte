<script lang="ts">
	import type { ArgErrors, CommandSpec } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Icon, Select, Switch, TextField } from '$lib/m3';
	import ListArgInput from './ListArgInput.svelte';

	interface Props {
		spec: CommandSpec;
		/** Argument values keyed by name (lists are edited as comma/newline separated text). */
		values?: Record<string, unknown>;
		/** Per-argument errors from the server, shown under each field. */
		errors?: Record<string, string>;
		/** Called on every user edit (for dirty tracking). */
		onedit?: () => void;
		/** Limits saved lists to the ones this project may use. */
		projectId?: string | null;
	}
	let { spec, values = $bindable({}), errors = $bindable({}), onedit, projectId = null }: Props = $props();

	const asText = (v: unknown) => (Array.isArray(v) ? v.join(', ') : v === null || v === undefined ? '' : String(v));

	function set(name: string, value: unknown) {
		values = { ...values, [name]: value };
		if (errors[name]) {
			const { [name]: _removed, ...rest } = errors;
			errors = rest;
		}
		onedit?.();
	}

	// Live preview of the rendered command line (debounced; server does the quoting). Folded to
	// its first line by default: library commands are usually long scripts.
	let preview = $state<string | null>(null);
	let previewOpen = $state(false);
	let previewLines = $derived(preview ? preview.split('\n').length : 0);
	let firstLine = $derived(preview ? preview.split('\n').find((l) => l.trim()) ?? '' : '');
	let previewError = $state<string | null>(null);
	$effect(() => {
		const id = spec.id;
		const snapshot = { ...values };
		const timer = setTimeout(async () => {
			try {
				const r = await api().commands.render(id, snapshot);
				// An HTTP action: the request as it will be sent (variables as written).
				preview = (r.kind === 'mcp' || r.kind === 'plugin') && r.request
					? [r.request.url, ...(r.request.body ? ['', r.request.body] : [])].join('\n')
					: r.request
					? [`${r.request.method} ${r.request.url}`, ...Object.entries(r.request.headers).map(([k, v]) => `${k}: ${v}`), ...(r.request.body ? ['', r.request.body] : [])].join('\n')
					: r.command;
				previewError = null;
			} catch (err) {
				preview = null;
				const detail = err instanceof ApiError ? (err.detail as ArgErrors | null) : null;
				previewError = detail?.errors ? 'Fill in the fields above to see the command' : 'Preview unavailable';
			}
		}, 250);
		return () => clearTimeout(timer);
	});

	// show_if: an argument is shown only while the boolean it names is on.
	const isOn = (name: string) => {
		const arg = spec.args.find((a) => a.name === name);
		const v = values[name];
		return v === undefined ? Boolean(arg?.default) : Boolean(v);
	};
	const visible = (arg: { show_if?: string | null }) => !arg.show_if || isOn(arg.show_if);
	const flagHint = (arg: { flag?: string | null; type: string }) =>
		arg.flag ? (arg.type === 'boolean' ? `adds ${arg.flag}` : `adds ${arg.flag} <value> when filled`) : '';

	const hint = (description: string, example: unknown, extra = '') =>
		[description, example !== null && example !== undefined && example !== '' ? `e.g. ${asText(example)}` : '', extra]
			.filter(Boolean)
			.join(' · ');
</script>

<div class="flex flex-col gap-4">
	{#if spec.description}<p class="body-small text-on-surface-variant">{spec.description}</p>{/if}
	{#each spec.args.filter(visible) as arg (arg.name)}
		<div class={arg.show_if ? 'ml-4 border-l-2 border-outline-variant pl-4' : ''}>
		{#if arg.type === 'boolean'}
			<div class="flex flex-col gap-1">
				<Switch
					checked={values[arg.name] === undefined ? Boolean(arg.default) : Boolean(values[arg.name])}
					label={arg.name}
					onchange={(v) => set(arg.name, v)}
				/>
				{#if arg.description || arg.flag}<p class="pl-16 body-small text-on-surface-variant">{[arg.description, flagHint(arg)].filter(Boolean).join(' · ')}</p>{/if}
			</div>
		{:else if arg.choices?.length}
			<Select
				label="{arg.name}{arg.required ? ' *' : ''}"
				value={asText(values[arg.name] ?? arg.default ?? '')}
				options={[...(arg.required ? [] : [{ value: '', label: '(default)' }]), ...arg.choices.map((c) => ({ value: c, label: c }))]}
				supporting={errors[arg.name] ??
					hint(arg.description, null, [arg.choices_from ? `options from “${arg.choices_from.set}”` : '', flagHint(arg)].filter(Boolean).join(' · '))}
				onchange={(v) => set(arg.name, v)}
			/>
		{:else if arg.type === 'string' || arg.type === 'list'}
			<ListArgInput
				label={arg.name}
				type={arg.type}
				required={arg.required}
				value={values[arg.name]}
				placeholder={arg.default !== null && arg.default !== undefined ? `default: ${asText(arg.default)}` : ''}
				error={errors[arg.name] ?? null}
				supporting={hint(arg.description, arg.example, [arg.type === 'list' ? 'one per line or comma separated' : '', flagHint(arg)].filter(Boolean).join(' · '))}
				{projectId}
				onchange={(v) => set(arg.name, v)}
			/>
		{:else if arg.type === 'text'}
			<!-- Several lines (a playbook, extra vars): one value, as written. -->
			<TextField
				label={arg.name}
				required={arg.required}
				mono
				multiline
				rows={6}
				value={asText(values[arg.name])}
				placeholder={arg.default !== null && arg.default !== undefined ? `default: ${asText(arg.default)}` : arg.example ? asText(arg.example) : ''}
				error={errors[arg.name] ?? null}
				supporting={hint(arg.description, null, flagHint(arg))}
				oninput={(e) => set(arg.name, (e.currentTarget as HTMLTextAreaElement).value)}
			/>
		{:else}
			<TextField
				label={arg.name}
				required={arg.required}
				mono
				type={arg.type === 'number' ? 'number' : 'text'}
				value={asText(values[arg.name])}
				placeholder={arg.default !== null && arg.default !== undefined ? `default: ${asText(arg.default)}` : ''}
				error={errors[arg.name] ?? null}
				supporting={hint(arg.description, arg.example, flagHint(arg))}
				oninput={(e) => set(arg.name, (e.currentTarget as HTMLInputElement).value)}
			/>
		{/if}
		</div>
	{/each}
	<div class="rounded-sm bg-surface-container-highest">
		<button
			type="button"
			class="state-layer flex w-full items-start gap-2 rounded-sm px-3 py-2 text-left"
			aria-expanded={previewOpen}
			onclick={() => (previewOpen = !previewOpen)}
			disabled={!preview}
		>
			<span class="min-w-0 flex-1">
				<span class="block label-small text-on-surface-variant">
					{#if spec.kind === 'plugin'}
						Will run in the plugin {spec.plugin?.id}{preview && !previewOpen && previewLines > 1 ? ' · its arguments' : ''}
					{:else if spec.kind === 'mcp'}
						Will call {spec.mcp?.tool} on the MCP server {spec.mcp?.server}{preview && !previewOpen && previewLines > 1 ? ' · its arguments' : ''}
					{:else if spec.kind === 'http'}
						Will send from the Castor server{spec.paginate ? ', every page' : ''}{spec.batch ? `, ${spec.batch.size} ${spec.batch.arg} per request` : ''}{preview && !previewOpen && previewLines > 1 ? ' · headers and body' : ''}
					{:else}
						Will run{spec.image ? ` in ${spec.image}` : ' in the agent shell'}{spec.container_flags?.length ? ` (flags: ${spec.container_flags.join(' ')})` : ''}{preview && !previewOpen && previewLines > 1 ? ` · ${previewLines} lines` : ''}
					{/if}
				</span>
				{#if preview && !previewOpen}
					<code class="mt-0.5 block truncate font-mono text-[12.5px] text-on-surface">{firstLine}</code>
				{/if}
			</span>
			{#if preview}
				<Icon name={previewOpen ? 'expand_less' : 'expand_more'} size={20} class="mt-0.5 shrink-0 text-on-surface-variant" />
				<span class="sr-only">{previewOpen ? (spec.kind === 'http' ? 'Hide the request' : 'Hide the command') : spec.kind === 'http' ? 'Show the whole request' : 'Show the whole command'}</span>
			{/if}
		</button>
		{#if preview && previewOpen}
			<code class="block px-3 pb-2 font-mono text-[12.5px] break-all whitespace-pre-wrap text-on-surface">{preview}</code>
		{:else if !preview}
			<p class="px-3 pb-2 -mt-1 body-small text-on-surface-variant italic">{previewError ?? '…'}</p>
		{/if}
	</div>
</div>
