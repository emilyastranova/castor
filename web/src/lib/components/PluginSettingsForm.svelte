<script lang="ts">
	import type { PluginSettingSpec } from '$lib/api';
	import { Select, Switch, TextField } from '$lib/m3';

	/** A plugin's settings (castor-plugin.yaml `settings`). Secret ones are never shown back: a
	 *  set one says so, and typing replaces it. */
	interface Props {
		spec: PluginSettingSpec[];
		values: Record<string, unknown>;
		/** Secrets typed here (name → value); untouched ones aren't sent. */
		secrets: Record<string, string>;
		/** Secrets already stored. */
		secretsSet?: string[];
	}
	let { spec, values = $bindable(), secrets = $bindable(), secretsSet = [] }: Props = $props();
</script>

<div class="flex flex-col gap-3">
	{#each spec as s (s.name)}
		{#if s.type === 'bool'}
			<div>
				<Switch checked={values[s.name] === true} label={s.label} onchange={(on) => (values = { ...values, [s.name]: on })} />
				{#if s.description}<p class="mt-0.5 pl-1 body-small text-on-surface-variant">{s.description}</p>{/if}
			</div>
		{:else if s.type === 'select'}
			<Select label={s.label} value={String(values[s.name] ?? '')} supporting={s.description || undefined}
				options={s.options.map((o) => ({ value: o, label: o }))} onchange={(v) => (values = { ...values, [s.name]: v })} />
		{:else if s.type === 'secret'}
			<TextField
				label="{s.label}{s.required ? ' *' : ''}"
				type="password"
				autocomplete="off"
				value={secrets[s.name] ?? ''}
				placeholder={secretsSet.includes(s.name) ? 'Set (type to replace it)' : ''}
				supporting={s.description || (secretsSet.includes(s.name) ? 'Stored in the vault.' : 'Stored in the vault; the plugin gets it when it starts.')}
				oninput={(e) => (secrets = { ...secrets, [s.name]: (e.currentTarget as HTMLInputElement).value })}
			/>
		{:else}
			<TextField
				label="{s.label}{s.required ? ' *' : ''}"
				type={s.type === 'number' ? 'number' : 'text'}
				value={String(values[s.name] ?? '')}
				supporting={s.description || undefined}
				oninput={(e) => (values = { ...values, [s.name]: (e.currentTarget as HTMLInputElement).value })}
			/>
		{/if}
	{/each}
</div>
