<script lang="ts">
	import type { MyPluginSettings } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import PluginSettingsForm from '$lib/components/PluginSettingsForm.svelte';
	import PluginIcon from '$lib/components/PluginIcon.svelte';

	/** Your own values of the settings plugins ask each person for (your own API key…): only you
	 *  and the plugin, when it runs for you, see them. */
	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let list = $state<MyPluginSettings[]>(data.plugins);
	// svelte-ignore state_referenced_locally
	let loaded = data.plugins;
	let values = $state<Record<string, Record<string, unknown>>>({});
	let secrets = $state<Record<string, Record<string, string>>>({});
	$effect(() => {
		if (data.plugins !== loaded) {
			loaded = data.plugins;
			list = data.plugins;
		}
		for (const p of list) {
			values[p.id] ??= { ...p.settings };
			secrets[p.id] ??= {};
		}
	});
	let saving = $state<string | null>(null);
	async function save(p: MyPluginSettings) {
		saving = p.id;
		try {
			const typed = Object.fromEntries(Object.entries(secrets[p.id] ?? {}).filter(([, v]) => v !== ''));
			const out = await api().plugins.setMine(p.id, { settings: values[p.id], secrets: typed });
			list = list.map((x) => (x.id === p.id ? out : x));
			secrets[p.id] = {};
			snackbar.show(`Saved your settings for ${p.name}`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save');
		} finally {
			saving = null;
		}
	}
	async function clear(p: MyPluginSettings, name: string) {
		try {
			const out = await api().plugins.setMine(p.id, { secrets: { [name]: '' } });
			list = list.map((x) => (x.id === p.id ? out : x));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove it');
		}
	}
</script>

<svelte:head><title>Your plugin settings · Castor</title></svelte:head>

<TopAppBar title="Your plugin settings" subtitle="What plugins ask each person for" />

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-3xl flex-col gap-3 pt-2">
		<p class="max-w-[70ch] body-small text-on-surface-variant">
			Some plugins use a value of your own, such as your API key for an outside service. It's used when you run their actions, and for what you add or
			run in a project (a host you add, your card's scan results). Secret ones are kept in Castor's vault and never shown again; only the plugin sees them,
			when it runs for you.
		</p>
		{#each list as p (p.id)}
			<Card variant="outlined" class="flex flex-col gap-3 px-4 py-3.5">
				<div class="flex items-start gap-3">
					<PluginIcon src={p.icon_url} />
					<div class="min-w-0 flex-1">
						<p class="title-medium text-on-surface">{p.name}</p>
						{#if p.description}<p class="body-small text-on-surface-variant">{p.description}</p>{/if}
					</div>
					{#if p.missing.length}
						<span class="flex shrink-0 items-center gap-1 rounded-full bg-tertiary-container px-2 py-0.5 label-medium text-on-tertiary-container"><Icon name="info" size={14} />Needs yours</span>
					{:else}
						<span class="flex shrink-0 items-center gap-1 rounded-full bg-primary-container px-2 py-0.5 label-medium text-on-primary-container"><Icon name="check" size={14} />Set</span>
					{/if}
				</div>
				{#if values[p.id] && secrets[p.id]}
					<PluginSettingsForm spec={p.settings_spec} bind:values={values[p.id]} bind:secrets={secrets[p.id]} secretsSet={p.secrets_set} />
				{/if}
				<div class="flex flex-wrap justify-end gap-1">
					{#each p.settings_spec.filter((s) => s.type === 'secret' && p.secrets_set.includes(s.name)) as s (s.name)}
						<Button variant="text" icon="delete" onclick={() => clear(p, s.name)}>Remove your {s.label}</Button>
					{/each}
					<Button icon="check" loading={saving === p.id} onclick={() => save(p)}>Save</Button>
				</div>
			</Card>
		{:else}
			<div class="flex flex-col items-center gap-2 rounded-lg border border-dashed border-outline-variant px-6 py-10 text-center">
				<Icon name="extension" size={32} class="text-on-surface-variant" />
				<p class="title-small text-on-surface">Nothing to set</p>
				<p class="max-w-md body-medium text-on-surface-variant">No plugin you use asks for anything of yours. When one does (an admin chose that each person brings their own API key, say), it shows here.</p>
			</div>
		{/each}
	</div>
</div>
