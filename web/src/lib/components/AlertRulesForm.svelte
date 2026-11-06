<script lang="ts">
	import type { AlertRules, UUID } from '$lib/api';
	import { api } from '$lib/api';
	import { Chip, Select, Switch, TextField } from '$lib/m3';
	import { isAdmin } from '$lib/session';

	/** Which alert channels hear about a card's runs (or a playbook run) and when. */
	interface Props {
		rules: AlertRules;
		/** A playbook run: its events are the run's, and it can alert on decisions. */
		run?: boolean;
	}
	let { rules = $bindable(), run = false }: Props = $props();

	let channels = $state<{ id: UUID; name: string; kind: string }[] | null>(null);
	$effect(() => {
		if (channels === null) api().alerts.brief().then((c) => (channels = c)).catch(() => (channels = []));
	});

	function toggle(id: UUID) {
		rules = { ...rules, channels: rules.channels.includes(id) ? rules.channels.filter((c) => c !== id) : [...rules.channels, id] };
	}
	let longText = $state('');
	$effect(() => {
		longText = rules.long_minutes ? String(rules.long_minutes) : '';
	});
	function setLong(text: string) {
		longText = text;
		const n = Math.round(Number(text));
		rules = { ...rules, long_minutes: text.trim() && n > 0 ? Math.min(n, 10080) : null };
	}
	const noun = $derived(run ? 'the run' : 'a run');
</script>

{#if channels === null}
	<p class="body-small text-on-surface-variant">Loading channels…</p>
{:else if !channels.length}
	<p class="body-small text-on-surface-variant">
		No alert channels yet. {#if isAdmin()}Add Slack, Google Chat, ntfy or any webhook in <a href="/settings/alerts" class="text-primary hover:underline">Settings → Alerts</a>.{:else}An admin can add them in Settings → Alerts.{/if}
	</p>
{:else}
	<div class="flex flex-col gap-3">
		<div class="flex flex-wrap gap-1.5">
			{#each channels as c (c.id)}
				<Chip variant="filter" selected={rules.channels.includes(c.id)} onclick={() => toggle(c.id)}>{c.name}</Chip>
			{/each}
		</div>
		{#if rules.channels.length}
			<Select
				label="Send"
				value={rules.on}
				onchange={(v) => (rules = { ...rules, on: v as AlertRules['on'] })}
				options={[
					{ value: 'failure', label: `When ${noun} fails` },
					{ value: 'finish', label: `When ${noun} finishes (either way)` },
					{ value: 'none', label: 'Not when it ends' }
				]}
			/>
			<TextField label="Also if it's still running after (minutes)" type="number" value={longText} oninput={(e) => setLong((e.currentTarget as HTMLInputElement).value)} placeholder="e.g. 60" supporting="Leave empty for no long-running alert" />
			{#if run}<Switch checked={rules.decisions} onchange={(v) => (rules = { ...rules, decisions: v })} label="When a step needs a decision" />{/if}
		{/if}
	</div>
{/if}
