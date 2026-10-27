<script lang="ts">
	import type { ArgErrors, CommandSpec } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Select, Switch, TextField } from '$lib/m3';

	interface Props {
		spec: CommandSpec;
		/** Argument values keyed by name (lists are edited as comma/newline separated text). */
		values?: Record<string, unknown>;
		/** Per-argument errors from the server, shown under each field. */
		errors?: Record<string, string>;
		/** Called on every user edit (for dirty tracking). */
		onedit?: () => void;
	}
	let { spec, values = $bindable({}), errors = $bindable({}), onedit }: Props = $props();

	const asText = (v: unknown) => (Array.isArray(v) ? v.join(', ') : v === null || v === undefined ? '' : String(v));

	function set(name: string, value: unknown) {
		values = { ...values, [name]: value };
		if (errors[name]) {
			const { [name]: _removed, ...rest } = errors;
			errors = rest;
		}
		onedit?.();
	}

	// Live preview of the rendered command line (debounced; server does the quoting).
	let preview = $state<string | null>(null);
	let previewError = $state<string | null>(null);
	$effect(() => {
		const id = spec.id;
		const snapshot = { ...values };
		const timer = setTimeout(async () => {
			try {
				const r = await api().commands.render(id, snapshot);
				preview = r.command;
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
		{:else}
			<TextField
				label={arg.name}
				required={arg.required}
				mono
				multiline={arg.type === 'list'}
				rows={2}
				type={arg.type === 'number' ? 'number' : 'text'}
				value={asText(values[arg.name])}
				placeholder={arg.default !== null && arg.default !== undefined ? `default: ${asText(arg.default)}` : ''}
				error={errors[arg.name] ?? null}
				supporting={hint(arg.description, arg.example, [arg.type === 'list' ? 'comma or one per line' : '', flagHint(arg)].filter(Boolean).join(' · '))}
				oninput={(e) => set(arg.name, (e.currentTarget as HTMLInputElement).value)}
			/>
		{/if}
		</div>
	{/each}
	<div class="rounded-sm bg-surface-container-highest px-3 py-2">
		<p class="label-small text-on-surface-variant">
			Will run{spec.image ? ` in ${spec.image}` : ' in the agent shell'}{spec.container_flags?.length ? ` (flags: ${spec.container_flags.join(' ')})` : ''}
		</p>
		{#if preview}
			<code class="block font-mono text-[12.5px] break-all whitespace-pre-wrap text-on-surface">{preview}</code>
		{:else}
			<p class="body-small text-on-surface-variant italic">{previewError ?? '…'}</p>
		{/if}
	</div>
</div>
