<script lang="ts">
	import type { PluginSettingSpec } from '$lib/api';
	import { Icon, Select, Switch, TextField } from '$lib/m3';

	/** A plugin's settings (castor-plugin.yaml `settings`). Secret ones are never shown back: a
	 *  set one says so, and typing replaces it. With `perUser` (an admin setting it up), each
	 *  setting the plugin allows can be made each person's own instead of one shared value. */
	interface Props {
		spec: PluginSettingSpec[];
		values: Record<string, unknown>;
		/** Secrets typed here (name → value); untouched ones aren't sent. */
		secrets: Record<string, string>;
		/** Secrets already stored. */
		secretsSet?: string[];
		/** Admins: the settings each person sets for themselves. Left out: no such choice here. */
		perUser?: string[];
	}
	let { spec, values = $bindable(), secrets = $bindable(), secretsSet = [], perUser = $bindable() }: Props = $props();

	const isOwn = (s: PluginSettingSpec) => s.per_user === 'required' || (perUser?.includes(s.name) ?? false);
	function setOwn(s: PluginSettingSpec, on: boolean) {
		if (!perUser) return;
		perUser = on ? [...perUser, s.name] : perUser.filter((n) => n !== s.name);
	}
</script>

<div class="flex flex-col {perUser !== undefined ? 'divide-y divide-outline-variant/60' : 'gap-3'}">
	{#each spec as s (s.name)}
		{@const own = perUser !== undefined && isOwn(s)}
		<div class="flex items-start gap-3 {perUser !== undefined ? 'py-3 first:pt-0 last:pb-0' : ''}">
			<div class="min-w-0 flex-1">
			{#if own}
				<div class="flex items-start gap-2 rounded-md bg-surface-container px-3 py-2.5">
					<Icon name="person" size={18} class="mt-0.5 shrink-0 text-on-surface-variant" />
					<p class="min-w-0 flex-1 body-medium text-on-surface">
						<b class="font-medium">{s.label}</b>: each person sets their own, in Your plugin settings.{s.required ? ' Its actions ask for it until they do.' : ''}
						{#if s.description}<span class="block body-small text-on-surface-variant">{s.description}</span>{/if}
					</p>
				</div>
			{:else if s.type === 'bool'}
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
			</div>
			{#if perUser !== undefined && s.per_user === 'allowed'}
				<label class="flex w-28 shrink-0 cursor-pointer items-start gap-1.5 pt-2 body-small text-on-surface-variant" title="Each person sets their own value in Your plugin settings, instead of this one for everyone">
					<input type="checkbox" checked={own} onchange={(e) => setOwn(s, (e.currentTarget as HTMLInputElement).checked)} class="mt-0.5 accent-primary" />
					Each person’s own
				</label>
			{:else if perUser !== undefined}
				<span class="w-28 shrink-0 pt-2 body-small text-on-surface-variant/70">{s.per_user === 'required' ? 'Always each person’s' : 'Same for everyone'}</span>
			{/if}
		</div>
	{/each}
</div>
