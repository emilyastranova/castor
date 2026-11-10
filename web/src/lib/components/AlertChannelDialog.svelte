<script lang="ts">
	import type { AlertChannel, AlertChannelIn, AlertDelivery, AlertPreset, AlertPresets, AlertTest } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, Select, Switch, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';

	/** Add or edit an alert channel: pick a kind (a preset or a custom webhook), fill its
	 *  fields, choose what it hears about for every card and run, and test it. */
	interface Props {
		open: boolean;
		presets: AlertPresets;
		channel: AlertChannel | null;
		onsaved: () => void;
	}
	let { open = $bindable(false), presets, channel, onsaved }: Props = $props();

	let kind = $state('');
	let name = $state('');
	let enabled = $state(true);
	let values = $state<Record<string, string>>({});
	let events = $state<string[]>([]);
	let longMinutes = $state('');
	let saving = $state(false);
	let testing = $state(false);
	let result = $state<AlertTest | null>(null);
	let error = $state<string | null>(null);
	let history = $state<AlertDelivery[]>([]);

	$effect(() => {
		if (!open) return;
		kind = channel?.kind ?? '';
		name = channel?.name ?? '';
		enabled = channel?.enabled ?? true;
		values = { ...(channel?.fields ?? {}) };
		events = [...(channel?.events ?? ['run.failed', 'job.failed'])];
		longMinutes = channel?.long_minutes ? String(channel.long_minutes) : '';
		result = error = null;
		history = [];
		if (channel) api().alerts.deliveries(channel.id).then((h) => (history = h));
	});

	let preset = $derived(presets.presets.find((p) => p.kind === kind));
	function choose(p: AlertPreset) {
		kind = p.kind;
		if (!name) name = p.label;
		values = Object.fromEntries(p.fields.filter((f) => f.default).map((f) => [f.name, f.default]));
	}
	/** Turn a preset into a custom webhook with the same request, to edit its body. */
	function customise() {
		const p = preset;
		if (!p?.template) return;
		const url = values.url ?? '';
		kind = 'webhook';
		values = { method: p.template.method, content_type: p.template.content_type, body: p.template.body, headers: p.template.headers, url };
		if (!url) snackbar.show('Enter the URL again: saved secrets stay hidden');
	}
	function startFrom(kindName: string) {
		const t = presets.presets.find((p) => p.kind === kindName)?.template;
		if (t) values = { ...values, method: t.method, content_type: t.content_type, body: t.body };
	}

	function body(): AlertChannelIn {
		return { name: name.trim(), kind, enabled, fields: values, events, long_minutes: events.includes('long') ? Math.max(1, Number(longMinutes) || 0) || null : null };
	}
	async function test() {
		testing = true;
		result = null;
		try {
			result = await api().alerts.testDraft({ ...body(), id: channel?.id });
		} catch (err) {
			result = { ok: false, status: null, error: err instanceof ApiError ? err.message : 'Could not test' };
		} finally {
			testing = false;
		}
	}
	async function save() {
		saving = true;
		error = null;
		try {
			if (channel) await api().alerts.update(channel.id, body());
			else await api().alerts.create(body());
			open = false;
			onsaved();
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}
	async function remove() {
		if (!channel) return;
		const ok = await confirmAction({ title: `Remove ${channel.name}?`, message: 'Cards and runs that alert to it stop doing so. Its history goes too.', confirmLabel: 'Remove' });
		if (!ok) return;
		await api().alerts.remove(channel.id);
		open = false;
		onsaved();
	}
	const missing = $derived(
		!kind || !name.trim() || (preset?.fields ?? []).some((f) => f.required && !values[f.name]?.trim() && !(f.secret && channel?.secrets[f.name] && channel.kind === kind))
	);
	const EVENT_ORDER = ['job.failed', 'job.succeeded', 'run.failed', 'run.succeeded', 'decision', 'approval', 'long'];
</script>

<Dialog bind:open title={channel ? channel.name : preset ? `New ${preset.label} channel` : 'New alert channel'} icon={preset?.icon ?? 'campaign'} size="md">
	<div class="flex flex-col gap-4 pt-2 pb-2">
		{#if !kind}
			<p class="body-medium text-on-surface-variant">Where should alerts go?</p>
			<div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
				{#each presets.presets as p (p.kind)}
					<button type="button" class="state-layer flex items-center gap-3 rounded-md border border-outline-variant px-3 py-3 text-left" onclick={() => choose(p)}>
						<Icon name={p.icon} class="text-primary" />
						<span class="min-w-0">
							<span class="block label-large text-on-surface">{p.label}</span>
							{#if p.kind === 'webhook'}<span class="block truncate body-small text-on-surface-variant">Any endpoint, your body</span>{/if}
						</span>
					</button>
				{/each}
			</div>
		{:else if preset}
			{#if preset.help}<p class="body-small text-on-surface-variant">{preset.help}</p>{/if}
			<TextField label="Name" bind:value={name} placeholder="e.g. #pentest-alerts" required />
			{#each preset.fields as f (f.name)}
				{@const kept = f.secret && channel?.kind === kind && channel?.secrets[f.name]}
				{#if f.type === 'select'}
					<Select label={f.label} value={values[f.name] ?? f.default} onchange={(v) => (values = { ...values, [f.name]: v })} options={f.options.map((o) => ({ value: o, label: o }))} supporting={f.help} />
				{:else}
					<TextField
						label={f.label}
						value={values[f.name] ?? ''}
						oninput={(e) => (values = { ...values, [f.name]: (e.currentTarget as HTMLInputElement).value })}
						type={f.type === 'password' ? 'password' : f.type === 'number' ? 'number' : 'text'}
						multiline={f.type === 'textarea'}
						rows={f.name === 'body' ? 10 : 3}
						mono={f.type === 'textarea' || f.type === 'password' || f.type === 'url'}
						placeholder={kept ? 'Saved; leave empty to keep it' : f.placeholder}
						required={f.required && !kept}
						autocomplete="off"
						supporting={kept ? `Saved (hidden). ${f.help}` : f.help}
					/>
				{/if}
			{/each}
			{#if kind === 'webhook'}
				<div class="flex flex-wrap items-center gap-2">
					<span class="body-small text-on-surface-variant">Start the body from:</span>
					{#each presets.presets.filter((p) => p.template) as p (p.kind)}
						<button type="button" class="state-layer rounded-sm border border-outline-variant px-2 py-0.5 label-medium" onclick={() => startFrom(p.kind)}>{p.label}</button>
					{/each}
				</div>
				<details>
					<summary class="cursor-pointer label-large text-on-surface-variant">Variables</summary>
					<dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 body-small">
						{#each Object.entries(presets.variables) as [v, what] (v)}
							<dt class="font-mono text-on-surface">{`{{ ${v} }}`}</dt>
							<dd class="text-on-surface-variant">{what}</dd>
						{/each}
					</dl>
				</details>
			{:else if preset.template}
				<button type="button" class="self-start body-small text-primary hover:underline" onclick={customise}>Customise the request (method, headers, body)…</button>
			{/if}

			<div>
				<p class="mb-1 label-large text-on-surface">Send for every card and run</p>
				<p class="mb-2 body-small text-on-surface-variant">On top of these, any card or playbook run can pick this channel for itself.</p>
				<div class="flex flex-col gap-1.5">
					{#each EVENT_ORDER as e (e)}
						<div class="flex items-center gap-3">
							<Switch checked={events.includes(e)} onchange={(on) => (events = on ? [...events, e] : events.filter((x) => x !== e))} label={presets.events[e]} />
							{#if e === 'long' && events.includes('long')}
								<TextField label="Minutes" type="number" bind:value={longMinutes} class="w-28" />
							{/if}
						</div>
					{/each}
				</div>
			</div>
			<Switch bind:checked={enabled} label="Enabled" />

			{#if result}
				<p class="flex items-start gap-1.5 body-small {result.ok ? 'text-primary' : 'text-error'}">
					<Icon name={result.ok ? 'check_circle' : 'error'} size={16} class="mt-0.5 shrink-0" />
					{result.ok ? `Sent (HTTP ${result.status}). Check that it arrived.` : result.error}
				</p>
			{/if}
			{#if error}<p class="body-small text-error">{error}</p>{/if}

			{#if history.length}
				<details>
					<summary class="cursor-pointer label-large text-on-surface-variant">Recent deliveries ({history.length})</summary>
					<ul class="mt-2 flex flex-col gap-1">
						{#each history as h (h.id)}
							<li class="flex items-start gap-2 body-small">
								<Icon name={h.ok ? 'check' : 'close'} size={16} class="mt-0.5 shrink-0 {h.ok ? 'text-primary' : 'text-error'}" />
								<span class="min-w-0 flex-1">
									<span class="text-on-surface">{h.title || h.event}</span>
									{#if !h.ok && h.error}<span class="block break-words text-error">{h.error}</span>{/if}
								</span>
								<span class="shrink-0 text-on-surface-variant" title={absoluteTime(h.created_at)}>{relativeTime(h.created_at)}</span>
							</li>
						{/each}
					</ul>
				</details>
			{/if}
		{/if}
	</div>
	{#snippet actions()}
		{#if channel}<Button variant="text" icon="delete" class="mr-auto text-error!" onclick={remove}>Remove</Button>
		{:else if kind}<Button variant="text" icon="arrow_back" class="mr-auto" onclick={() => (kind = '')}>Kinds</Button>{/if}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		{#if kind}
			<Button variant="tonal" icon="send" loading={testing} disabled={missing} onclick={test}>Test</Button>
			<Button icon="check" loading={saving} disabled={missing} onclick={save}>{channel ? 'Save' : 'Add channel'}</Button>
		{/if}
	{/snippet}
</Dialog>
