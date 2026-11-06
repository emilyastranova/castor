<script lang="ts">
	import type { AlertChannel } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon, IconButton, Switch, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import AlertChannelDialog from '$lib/components/AlertChannelDialog.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let channels = $state<AlertChannel[]>(data.channels);
	let dialogOpen = $state(false);
	let editing = $state<AlertChannel | null>(null);

	const presetOf = (kind: string) => data.presets.presets.find((p) => p.kind === kind);
	async function reload() {
		channels = await api().alerts.channels();
	}
	function open(ch: AlertChannel | null) {
		editing = ch;
		dialogOpen = true;
	}
	async function setEnabled(ch: AlertChannel, on: boolean) {
		try {
			await api().alerts.update(ch.id, { name: ch.name, kind: ch.kind, enabled: on, fields: ch.fields, events: ch.events, long_minutes: ch.long_minutes });
			await reload();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save');
		}
	}
	async function test(ch: AlertChannel) {
		const r = await api().alerts.test(ch.id);
		if (r.ok) snackbar.show(`Sent a test to ${ch.name}`);
		else snackbar.error(`${ch.name}: ${r.error}`);
		await reload();
	}
	const summary = (ch: AlertChannel) =>
		ch.events.length
			? ch.events.map((e) => (e === 'long' ? `runs over ${ch.long_minutes} min` : data.presets.events[e]?.replace(/^A |^Something /, '').toLowerCase() ?? e)).join(', ')
			: 'Only cards and runs that pick it';
</script>

<svelte:head><title>Alerts · Settings · Castor</title></svelte:head>

<TopAppBar title="Alerts" subtitle="Post to chat or any webhook when runs finish, fail, run long or need a decision">
	{#snippet actions()}
		<Button icon="add" onclick={() => open(null)}>New channel</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-3 pt-2">
		{#each channels as ch (ch.id)}
			{@const p = presetOf(ch.kind)}
			<Card variant="outlined" class="flex items-center gap-4 p-4 {ch.enabled ? '' : 'opacity-60'}">
				<span class="flex size-10 shrink-0 items-center justify-center rounded-md bg-secondary-container text-on-secondary-container"><Icon name={p?.icon ?? 'campaign'} /></span>
				<button type="button" class="min-w-0 flex-1 text-left" onclick={() => open(ch)}>
					<span class="flex items-center gap-2">
						<span class="truncate title-small text-on-surface">{ch.name}</span>
						<span class="rounded-full bg-surface-container-highest px-2 py-0.5 label-small text-on-surface-variant">{p?.label ?? ch.kind}</span>
					</span>
					<span class="block truncate body-small text-on-surface-variant">Every {summary(ch)}</span>
					{#if ch.last}
						<span class="flex items-center gap-1 body-small {ch.last.ok ? 'text-on-surface-variant' : 'text-error'}" title={absoluteTime(ch.last.created_at)}>
							<Icon name={ch.last.ok ? 'check' : 'error'} size={14} />
							{ch.last.ok ? 'Last sent' : `Failed: ${ch.last.error}`} · {relativeTime(ch.last.created_at)}{#if ch.failures_24h > 1}{' '}· {ch.failures_24h} failures today{/if}
						</span>
					{/if}
				</button>
				<IconButton icon="send" label="Send a test" onclick={() => test(ch)} />
				<Switch checked={ch.enabled} onchange={(on) => setEnabled(ch, on)} label="" />
			</Card>
		{:else}
			<div class="flex flex-col items-start gap-3 rounded-lg border border-dashed border-outline-variant p-6">
				<p class="body-medium text-on-surface">No alert channels yet.</p>
				<p class="body-small text-on-surface-variant">Add Slack, Google Chat, Discord, Teams, ntfy, Telegram, Gotify, Pushover or any webhook. Then a channel can hear about every failed run, or cards and playbook runs can pick it for themselves.</p>
				<Button variant="tonal" icon="add" onclick={() => open(null)}>New channel</Button>
			</div>
		{/each}
	</div>
</div>

<AlertChannelDialog bind:open={dialogOpen} presets={data.presets} channel={editing} onsaved={reload} />
