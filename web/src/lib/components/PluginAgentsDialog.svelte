<script lang="ts">
	import type { Agent, PluginAgentType, PluginMachine, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, IconButton, TagInput } from '$lib/m3';
	import { relativeTime } from '$lib/format';
	import { snackbar } from '$lib/snackbar.svelte';
	import AgentProjectsPicker from './AgentProjectsPicker.svelte';
	import PluginIcon from './PluginIcon.svelte';

	/** Agents → Add from a plugin: pick one of the agent types plugins add (Salt minions…), then the
	 *  machines its system reaches to bring in as agents, with tags and a project limit. */
	interface Props {
		open: boolean;
		types: PluginAgentType[];
		onimported?: (agents: Agent[]) => void;
	}
	let { open = $bindable(false), types, onimported }: Props = $props();

	let chosen = $state<PluginAgentType | null>(null);
	let machines = $state<PluginMachine[] | null>(null);
	let error = $state<string | null>(null);
	let picked = $state<Set<string>>(new Set());
	let filter = $state('');
	let tags = $state<string[]>([]);
	let limitTo = $state<UUID[] | null>(null);
	let busy = $state(false);

	$effect(() => {
		if (!open) return;
		chosen = types.length === 1 ? types[0] : null;
		tags = [];
		limitTo = null;
	});
	$effect(() => {
		const t = chosen;
		machines = null;
		error = null;
		picked = new Set();
		filter = '';
		if (!t || !open) return;
		api()
			.pluginAgents.machines(t.plugin.id, t.name)
			.then((r) => (machines = r.machines))
			.catch((err) => (error = err instanceof ApiError ? err.message : 'Could not list its machines'));
	});

	let shown = $derived(
		(machines ?? []).filter((x) => {
			const q = filter.trim().toLowerCase();
			return !q || [x.name, x.hostname, x.os, ...x.tags].some((v) => v.toLowerCase().includes(q));
		})
	);
	let available = $derived(shown.filter((x) => !x.agent_id));
	function toggle(id: string) {
		const next = new Set(picked);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		picked = next;
	}
	function all() {
		picked = available.every((x) => picked.has(x.id)) ? new Set([...picked].filter((id) => !available.some((x) => x.id === id))) : new Set([...picked, ...available.map((x) => x.id)]);
	}
	async function bring() {
		if (!chosen || !picked.size) return;
		busy = true;
		try {
			const made = await api().pluginAgents.import(chosen.plugin.id, chosen.name, { machines: [...picked], tags, project_ids: limitTo });
			snackbar.show(`Added ${made.length} agent${made.length === 1 ? '' : 's'}; they connect while ${chosen.plugin.name} runs`);
			onimported?.(made);
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not add them');
		} finally {
			busy = false;
		}
	}
</script>

<Dialog bind:open title={chosen ? `Add ${chosen.label} agents` : 'Add agents from a plugin'} icon="extension" size="lg">
	<div class="flex flex-col gap-4 pt-1 pb-2">
		{#if !chosen}
			<p class="body-medium text-on-surface-variant">Plugins can reach machines a system already manages (Salt minions, Puppet nodes…). Each one you add is an agent: cards and playbooks run on it through the plugin, with its system's tags.</p>
			<ul class="flex flex-col gap-2">
				{#each types as t (`${t.plugin.id}:${t.name}`)}
					<li>
						<button type="button" class="state-layer flex w-full items-start gap-3 rounded-md border border-outline-variant px-3 py-3 text-left disabled:opacity-50" disabled={!t.plugin.enabled} onclick={() => (chosen = t)}>
							<span class="flex size-10 shrink-0 items-center justify-center rounded-md bg-secondary-container text-on-secondary-container"><Icon name={t.icon} /></span>
							<span class="min-w-0 flex-1">
								<span class="flex items-center gap-2 title-small text-on-surface">{t.label}<span class="flex items-center gap-1 body-small text-on-surface-variant"><PluginIcon src={t.plugin.icon_url} size={16} />{t.plugin.name}</span></span>
								{#if t.description}<span class="block body-small text-on-surface-variant">{t.description}</span>{/if}
								<span class="block body-small text-on-surface-variant">
									{t.agents} agent{t.agents === 1 ? '' : 's'}{t.plugin.enabled ? '' : ' · the plugin is turned off'}{#if t.status.ok === false} · <span class="text-error">{t.status.message || 'its system didn’t answer'}</span>{/if}
								</span>
							</span>
							<Icon name="chevron_right" class="mt-2 text-on-surface-variant" />
						</button>
					</li>
				{/each}
			</ul>
		{:else}
			{#if types.length > 1}<div><Button variant="text" icon="arrow_back" onclick={() => (chosen = null)}>Other agent types</Button></div>{/if}
			{#if error}
				<p class="rounded-md bg-error-container px-3 py-2 body-medium text-on-error-container">{error}</p>
			{:else if !machines}
				<p class="py-6 text-center body-medium text-on-surface-variant">Asking {chosen.plugin.name} for its machines…</p>
			{:else if !machines.length}
				<p class="py-6 text-center body-medium text-on-surface-variant">{chosen.plugin.name} doesn't list any machines. Check its settings (Settings → Plugins).</p>
			{:else}
				<div class="flex flex-wrap items-center gap-2">
					<label class="flex min-w-[14rem] flex-1 items-center gap-2 rounded-full bg-surface-container-high px-4">
						<Icon name="filter_list" size={20} class="text-on-surface-variant" />
						<input class="h-10 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter: name, OS, tag" bind:value={filter} autocomplete="off" spellcheck="false" />
						{#if filter}<IconButton icon="close" label="Clear" size="sm" onclick={() => (filter = '')} />{/if}
					</label>
					<Button variant="text" icon="select_all" disabled={!available.length} onclick={all}>{available.length && available.every((x) => picked.has(x.id)) ? 'None' : `All ${available.length}`}</Button>
				</div>
				<ul class="flex max-h-80 flex-col divide-y divide-outline-variant/50 overflow-y-auto rounded-md border border-outline-variant/60">
					{#each shown as x (x.id)}
						<li>
							<label class="flex cursor-pointer items-start gap-3 px-3 py-2 {x.agent_id ? 'cursor-default opacity-60' : ''}">
								<input type="checkbox" class="mt-1 accent-primary" checked={!!x.agent_id || picked.has(x.id)} disabled={!!x.agent_id} onchange={() => toggle(x.id)} />
								<span class="min-w-0 flex-1">
									<span class="block body-medium text-on-surface">{x.name}{#if x.agent_id}<span class="ml-2 body-small text-on-surface-variant">already an agent</span>{/if}</span>
									<span class="block body-small text-on-surface-variant">{[x.hostname, x.os].filter(Boolean).join(' · ')}</span>
									{#if x.tags.length}
										<span class="mt-0.5 flex flex-wrap gap-1">{#each x.tags.slice(0, 8) as t (t)}<span class="rounded-xs bg-surface-container-highest px-1.5 py-px label-small text-on-surface-variant">{t}</span>{/each}{#if x.tags.length > 8}<span class="label-small text-on-surface-variant">+{x.tags.length - 8}</span>{/if}</span>
									{/if}
								</span>
							</label>
						</li>
					{:else}
						<li class="px-3 py-4 text-center body-medium text-on-surface-variant">Nothing matches.</li>
					{/each}
				</ul>
				<TagInput label="Tags for them" bind:value={tags} noun="agents" placeholder="Add a tag" supporting="On top of the tags their system gives them (kept up to date)." />
				<AgentProjectsPicker bind:value={limitTo} supporting="Which projects' jobs they may run." />
			{/if}
			{#if chosen.status.checked_at && chosen.status.ok === false}<p class="body-small text-error">Last asked {relativeTime(chosen.status.checked_at)}: {chosen.status.message}</p>{/if}
		{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		{#if chosen && machines?.length}<Button icon="add" loading={busy} disabled={!picked.size} onclick={bring}>Add {picked.size || ''} agent{picked.size === 1 ? '' : 's'}</Button>{/if}
	{/snippet}
</Dialog>
