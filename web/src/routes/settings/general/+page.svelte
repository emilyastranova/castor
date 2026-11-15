<script lang="ts">
	import type { AppSettings } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Card, Select, Switch, TextField, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { appSettings } from '$lib/appSettings.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let settings = $state<AppSettings>(data.settings);

	async function change(patch: Partial<AppSettings>) {
		const before = settings;
		settings = { ...settings, ...patch };
		try {
			settings = await api().settings.update(patch);
			appSettings.value = settings;
			snackbar.show('Saved');
		} catch (err) {
			settings = before;
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save');
		}
	}

	// Outbound requests (HTTP actions, alert channels): one entry per line.
	const lines = (v: string[] | undefined) => (v ?? []).join('\n');
	const parse = (text: string) => text.split(/[\n,]+/).map((l) => l.trim()).filter(Boolean);
	// svelte-ignore state_referenced_locally
	let hosts = $state(lines(settings.http_allowed_hosts));
	// svelte-ignore state_referenced_locally
	let addresses = $state(lines(settings.http_allow_addresses));
	async function saveList(key: 'http_allowed_hosts' | 'http_allow_addresses', text: string) {
		const next = parse(text);
		if (JSON.stringify(next) === JSON.stringify(settings[key] ?? [])) return;
		await change({ [key]: next });
		if (key === 'http_allowed_hosts') hosts = lines(settings.http_allowed_hosts);
		else addresses = lines(settings.http_allow_addresses);
	}

	const NOTE_LIMITS = [4, 6, 8, 12].map((n) => ({
		value: String(n),
		label: n === 4 ? '4 (one row)' : `${n}`,
		description: n === 4 ? 'Fits on one row of a project dashboard' : 'More than 4 wrap onto further rows'
	}));
</script>

<svelte:head><title>General · Settings · Castor</title></svelte:head>

<TopAppBar title="General" subtitle="Settings for everyone on this Castor" />

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-3xl flex-col gap-4 pt-2">
		<Card variant="outlined" class="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:gap-6">
			<div class="min-w-0 flex-1">
				<h2 class="title-medium text-on-surface">Pinned notes per project</h2>
				<p class="body-medium text-on-surface-variant">
					How many notes a project's dashboard can hold. Projects that already have more keep them; they just can't add another until they're under the limit.
				</p>
			</div>
			<Select
				class="w-48 shrink-0"
				label="Notes"
				value={String(settings.notes_per_project)}
				options={NOTE_LIMITS.some((o) => o.value === String(settings.notes_per_project)) ? NOTE_LIMITS : [...NOTE_LIMITS, { value: String(settings.notes_per_project), label: String(settings.notes_per_project), description: '' }]}
				onchange={(v) => change({ notes_per_project: Number(v) })}
			/>
		</Card>

		<Card variant="outlined" class="flex flex-col gap-4 p-5">
			<div>
				<h2 class="title-medium text-on-surface">Outbound requests</h2>
				<p class="body-medium text-on-surface-variant">
					Where the Castor server itself may send requests: HTTP actions on cards and in playbooks, and alert channels. Loopback, link-local (where cloud metadata services live) and reserved addresses are always refused unless they're listed below.
				</p>
			</div>
			<Switch
				checked={!!settings.http_block_private}
				label="Block private networks too (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7)"
				onchange={(v) => change({ http_block_private: v })}
			/>
			<TextField
				label="Hosts HTTP actions may call"
				bind:value={hosts}
				multiline
				rows={3}
				mono
				placeholder="api.example.com&#10;*.atlassian.net"
				supporting="One per line; *.example.com for a domain's hosts. Empty: any host (alert channels aren't limited by this)."
				onblur={() => saveList('http_allowed_hosts', hosts)}
			/>
			<TextField
				label="Addresses allowed anyway"
				bind:value={addresses}
				multiline
				rows={2}
				mono
				placeholder="127.0.0.1/32"
				supporting="Addresses or networks exempt from every block above, one per line: a service on this machine, say."
				onblur={() => saveList('http_allow_addresses', addresses)}
			/>
		</Card>

		<Card variant="outlined" class="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:gap-6">
			<div class="min-w-0 flex-1">
				<h2 class="title-medium text-on-surface">Approvals</h2>
				<p class="body-medium text-on-surface-variant">
					Runs of actions that say <code class="font-mono">confirm: true</code>, and of cards that ask before each run, wait for a person to approve them. With this on, it has to be someone other than whoever started the run.
				</p>
			</div>
			<Switch checked={!!settings.approval_by_someone_else} label="Someone else approves" onchange={(v) => change({ approval_by_someone_else: v })} />
		</Card>
	</div>
</div>
