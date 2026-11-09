<script lang="ts">
	import { pop, glide } from '$lib/motion';
	import { isAdmin } from '$lib/session';
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import type { Agent, AgentToken, Job, SshHost } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { TopAppBar, Card, Icon, IconButton, Chip, Dialog, Button, TextField, TagInput } from '$lib/m3';
	import { loadTags, type TagSuggestion } from '$lib/tags';
	import StatusChip from '$lib/components/StatusChip.svelte';
	import { realtime } from '$lib/realtime.svelte';
	import { relativeTime, absoluteTime, duration } from '$lib/format';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import AgentTokenDialog from '$lib/components/AgentTokenDialog.svelte';
	import SshHostDialog from '$lib/components/SshHostDialog.svelte';
	import SshConfigDialog from '$lib/components/SshConfigDialog.svelte';
	import TagManagerDialog from '$lib/components/TagManagerDialog.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let agents = $state<Agent[]>(data.agents);
	// svelte-ignore state_referenced_locally
	let jobs = $state<Job[]>(data.jobs);
	// svelte-ignore state_referenced_locally
	let tokens = $state<AgentToken[]>(data.tokens);
	// svelte-ignore state_referenced_locally
	let sharedSecretAllowed = $state(data.sharedSecretAllowed);
	// svelte-ignore state_referenced_locally
	let sshHosts = $state<SshHost[]>(data.sshHosts);
	// svelte-ignore state_referenced_locally
	let loaded = data.agents;
	$effect(() => {
		if (data.agents !== loaded) {
			loaded = data.agents;
			agents = data.agents;
			jobs = data.jobs;
			tokens = data.tokens;
			sharedSecretAllowed = data.sharedSecretAllowed;
			sshHosts = data.sshHosts;
		}
	});

	async function reloadSshHosts() {
		if (!isAdmin()) return;
		try {
			sshHosts = await api().sshHosts.list();
		} catch {
			/* keep what we have */
		}
	}
	const sshOf = (a: Agent) => (a.ssh_host_id ? sshHosts.find((h) => h.id === a.ssh_host_id) : undefined);
	let sshOpen = $state(false);
	let sshConfigOpen = $state(false);
	let agentTagsOpen = $state(false);
	let sshEditing = $state<SshHost | null>(null);
	function addSshHost() {
		sshEditing = null;
		sshOpen = true;
	}
	function editSshHost(h: SshHost) {
		sshEditing = h;
		sshOpen = true;
	}
	function sshSaved(h: SshHost) {
		sshHosts = [...sshHosts.filter((x) => x.id !== h.id), h];
	}
	async function acceptHostKey(h: SshHost) {
		const jump = Object.entries(h.jump_host_keys_pending)[0];
		const [who, now, was] = h.host_key_pending_fingerprint || !jump
			? [h.name, h.host_key_pending_fingerprint, h.host_key_fingerprint]
			: [`jump host ${jump[0]}`, jump[1], h.jump_host_keys[jump[0]]];
		const ok = await confirmAction({
			title: `Trust the new key of ${who}?`,
			message: `It now presents ${now}, not the ${was} Castor trusted. That happens when the machine is reinstalled or its SSH keys are regenerated, and also when something else answers at that address. Accept it only if you know why it changed.`,
			confirmLabel: 'Trust the new key',
			icon: 'key'
		});
		if (!ok) return;
		try {
			sshSaved(await api().sshHosts.acceptHostKey(h.id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not accept the key');
		}
	}
	async function reconnect(h: SshHost) {
		try {
			sshSaved(await api().sshHosts.reconnect(h.id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not reconnect');
		}
	}
	async function removeSshHost(a: Agent, h: SshHost) {
		const ok = await confirmAction({
			title: `Remove SSH host ${a.name}?`,
			message: `Castor disconnects from ${h.username}@${h.host} (any job running there is lost) and forgets the host and its key. Remove the key from the host's authorized_keys too.`,
			confirmLabel: 'Remove host'
		});
		if (!ok) return;
		try {
			await api().sshHosts.remove(h.id);
			agents = agents.filter((x) => x.id !== a.id);
			sshHosts = sshHosts.filter((x) => x.id !== h.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove the host');
		}
	}

	async function reloadTokens() {
		try {
			const res = await api().agentTokens.list();
			tokens = res.tokens;
			sharedSecretAllowed = res.shared_secret_allowed;
		} catch {
			/* keep what we have */
		}
	}
	/** The agent's token: its active one, else its latest revoked one. */
	const tokenOf = (a: Agent) =>
		tokens.filter((t) => t.agent_id === a.id).sort((x, y) => Number(!!x.revoked_at) - Number(!!y.revoked_at))[0];
	let waiting = $derived(tokens.filter((t) => !t.agent_id));

	let revealed = $state<(AgentToken & { token: string }) | null>(null);
	async function rotate(t: AgentToken, agentName: string) {
		const ok = await confirmAction({
			title: `Rotate ${agentName}'s token?`,
			message: 'A new token is made and the current one stops working at once: the agent is disconnected until you restart it with the new token.',
			confirmLabel: 'Rotate token',
			icon: 'autorenew'
		});
		if (!ok) return;
		try {
			revealed = await api().agentTokens.rotate(t.id);
			enrollOpen = true;
			void reloadTokens();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not rotate the token');
		}
	}
	async function revoke(t: AgentToken, what: string) {
		const ok = await confirmAction({
			title: `Revoke ${what}?`,
			message: t.agent_id
				? 'The agent is disconnected now and can’t reconnect, with this token or the shared secret. Give it a new token by forgetting it and enrolling it again.'
				: 'Nobody will be able to enroll with this token.',
			confirmLabel: 'Revoke',
			icon: 'key_off'
		});
		if (!ok) return;
		try {
			await api().agentTokens.revoke(t.id);
			void reloadTokens();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not revoke the token');
		}
	}
	async function removeToken(t: AgentToken) {
		try {
			await api().agentTokens.remove(t.id);
			tokens = tokens.filter((x) => x.id !== t.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete the token');
		}
	}

	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name === 'agent_tokens.changed') {
				void reloadTokens();
			} else if (e.name === 'ssh_hosts.changed') {
				void reloadSshHosts();
			} else if (e.name === 'agent.updated') {
				const a = e.data as Agent;
				agents = [...agents.filter((x) => x.id !== a.id), a].sort((x, y) => x.name.localeCompare(y.name));
			} else if (e.name === 'agent.deleted') {
				const id = (e.data as { id: string }).id;
				agents = agents.filter((x) => x.id !== id);
				tokens = tokens.filter((t) => t.agent_id !== id);
			} else if (e.name === 'job.updated') {
				const j = e.data as Job;
				jobs = [j, ...jobs.filter((x) => x.id !== j.id)].sort((a, b) => b.queued_at.localeCompare(a.queued_at)).slice(0, 100);
			}
		})
	);

	let editing = $state<Agent | null>(null);
	let tags = $state<string[]>([]);
	let tagSuggestions = $state<TagSuggestion[]>([]);
	let concurrency = $state('1');
	let enrollOpen = $state(false);

	function edit(a: Agent) {
		editing = a;
		tags = [...a.tags];
		void loadTags('agents').then((t) => (tagSuggestions = t));
		concurrency = String(a.max_concurrency);
	}
	async function saveAgent() {
		if (!editing) return;
		try {
			const a = await api().agents.update(editing.id, {
				tags,
				max_concurrency: Number(concurrency) || 1
			});
			agents = agents.map((x) => (x.id === a.id ? a : x));
			editing = null;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Save failed');
		}
	}
	async function removeAgent(a: Agent) {
		const ok = await confirmAction({
			title: `Forget agent ${a.name}?`,
			message: tokenOf(a)
				? 'It will be removed from the agent list with its tags, settings and token. To use it again, enroll it with a new token.'
				: 'It will be removed from the agent list, and its tags and settings will be lost. It reappears if it connects again.',
			confirmLabel: 'Forget agent'
		});
		if (!ok) return;
		try {
			await api().agents.remove(a.id);
			agents = agents.filter((x) => x.id !== a.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove agent');
		}
	}

	const statusCls: Record<string, string> = {
		idle: 'bg-primary-container text-on-primary-container',
		busy: 'bg-tertiary-container text-on-tertiary-container',
		offline: 'bg-surface-container-highest text-on-surface-variant'
	};
	let online = $derived(agents.filter((a) => a.connected).length);
	let wsUrl = $derived(`${page.url.protocol === 'https:' ? 'wss' : 'ws'}://${page.url.host}/ws/agent`);
	let agentName = (id: string | null) => agents.find((a) => a.id === id)?.name ?? '—';
</script>

<svelte:head><title>Agents · Castor</title></svelte:head>

<TopAppBar title="Agents" subtitle="{online} of {agents.length} online">
	{#snippet actions()}
		<IconButton icon="refresh" label="Refresh" onclick={() => invalidateAll()} />
		{#if isAdmin()}
			<IconButton icon="sell" label="Agent tags: rename or merge" onclick={() => (agentTagsOpen = true)} />
			<Button variant="text" icon="route" onclick={() => (sshConfigOpen = true)}>SSH config</Button>
			<Button variant="text" icon="terminal" onclick={addSshHost}>Add SSH host</Button>
			<Button variant="tonal" icon="add" onclick={() => { revealed = null; enrollOpen = true; }}>Enroll agent</Button>
		{/if}
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-8">
	{#if agents.length === 0}
		<div class="mx-auto mt-24 flex max-w-md flex-col items-center gap-4 text-center">
			<span class="flex size-20 items-center justify-center rounded-xl bg-primary-container text-on-primary-container"><Icon name="dns" size={40} /></span>
			<h2 class="headline-small text-on-surface">No agents yet</h2>
			<p class="body-medium text-on-surface-variant">Start an agent on a machine that can reach this server, or add a host Castor can reach over SSH. Command cards are dispatched to idle agents.</p>
			<div class="flex flex-wrap justify-center gap-2">
				<Button icon="add" onclick={() => { revealed = null; enrollOpen = true; }}>Enroll an agent</Button>
				{#if isAdmin()}<Button variant="outlined" icon="terminal" onclick={addSshHost}>Add SSH host</Button>{/if}
			</div>
		</div>
	{:else}
		<div class="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4 pt-2">
			{#each agents as a (a.id)}
				<div class="flex flex-col" in:pop out:pop animate:glide>
				<Card variant="elevated" class="flex flex-1 flex-col gap-3 p-5">
					<div class="flex items-start gap-3">
						<span class="flex size-10 items-center justify-center rounded-md {a.connected ? 'bg-primary-container text-on-primary-container' : 'bg-surface-container-highest text-on-surface-variant'}">
							<Icon name={a.ssh_host_id ? 'terminal' : 'dns'} filled={a.connected} />
						</span>
						<div class="min-w-0 flex-1">
							<h2 class="truncate title-medium text-on-surface">{a.name}</h2>
							<p class="truncate body-small text-on-surface-variant">
								{#if sshOf(a)}{@const h = sshOf(a)!}{h.route.at(-1)?.replace(/:22$/, '') ?? `${h.username ? `${h.username}@` : ''}${h.host}`}{:else}{a.username}@{a.hostname}{/if}
							</p>
						</div>
						<span class="rounded-full px-2 py-0.5 label-small {statusCls[a.status]}">{a.status}{a.running_jobs ? ` · ${a.running_jobs}` : ''}</span>
					</div>
					<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 body-small text-on-surface-variant">
						<dt>OS</dt><dd class="truncate text-on-surface" title={a.os}>{a.os || '—'}</dd>
						<dt>Runtimes</dt><dd class="text-on-surface">{(a.capabilities.runtimes ?? []).join(', ') || '—'}</dd>
						<dt>Slots</dt><dd class="text-on-surface">{a.running_jobs}/{a.max_concurrency}</dd>
						{#if (sshOf(a)?.route.length ?? 0) > 1}
							{@const hops = sshOf(a)!.route.slice(0, -1)}
							<dt>Via</dt><dd class="truncate font-mono text-[12px] text-on-surface" title="Jump hosts, from the SSH config">{hops.map((h) => h.replace(/:22$/, '')).join(' › ')}</dd>
						{/if}
						<dt>Seen</dt><dd class="text-on-surface" title={absoluteTime(a.last_seen_at)}>{a.connected ? 'now' : relativeTime(a.last_seen_at) || 'never'}</dd>
						<dt>Auth</dt>
						<dd class="flex min-w-0 items-center gap-1.5">
							{#if a.ssh_host_id}
								{@const h = sshOf(a)}
								<span class="flex min-w-0 items-center gap-1 text-on-surface" title="Castor connects to it over SSH{h?.host_key_fingerprint ? `; host key ${h.host_key_fingerprint}` : ''}">
									<Icon name="key" size={14} /><span class="truncate">SSH{h?.key_fingerprint ? ` · ${h.key_fingerprint.replace('SHA256:', '').slice(0, 12)}…` : ''}</span>
								</span>
							{:else if tokenOf(a)}
								{@const t = tokenOf(a)!}
								{#if t.revoked_at}
									<span class="flex items-center gap-1 text-error"><Icon name="key_off" size={14} />token revoked</span>
								{:else}
									<span class="flex min-w-0 items-center gap-1 text-on-surface" title="Last used {absoluteTime(t.last_used_at)}"><Icon name="key" size={14} /><span class="truncate font-mono">{t.prefix}…</span></span>
									{#if isAdmin()}<button type="button" class="label-small text-primary hover:underline" onclick={() => rotate(t, a.name)}>Rotate</button>
									<button type="button" class="label-small text-error hover:underline" onclick={() => revoke(t, `${a.name}'s token`)}>Revoke</button>{/if}
								{/if}
							{:else}
								<span class="flex items-center gap-1 {sharedSecretAllowed ? 'text-on-surface' : 'text-error'}" title="Connects with the shared CASTOR_AGENT_SECRET. Enroll it with a token: forget it, create a token, restart it with --token.">
									<Icon name="group" size={14} />shared secret{sharedSecretAllowed ? '' : ' (no longer accepted)'}
								</span>
							{/if}
						</dd>
					</dl>
					{#if sshOf(a) && !a.connected}
						{@const h = sshOf(a)!}
						{#if h.state === 'host_key_changed'}
							{@const jumps = Object.entries(h.jump_host_keys_pending)}
							<div class="flex flex-col gap-2 rounded-md bg-error-container px-3 py-2 body-small text-on-error-container">
								<span class="flex items-start gap-1.5"><Icon name="gpp_maybe" size={18} class="mt-px shrink-0" />{h.host_key_pending_fingerprint || !jumps.length ? 'The host key changed' : `The key of jump host ${jumps[0][0]} changed`}, so Castor stopped connecting.</span>
								<dl class="grid grid-cols-[auto_1fr] gap-x-2 font-mono text-[11.5px]">
									{#if h.host_key_pending_fingerprint || !jumps.length}
										<dt>now</dt><dd class="break-all">{h.host_key_pending_fingerprint ?? 'unknown'}</dd>
										<dt>was</dt><dd class="break-all">{h.host_key_fingerprint}</dd>
									{/if}
									{#each jumps as [label, fp] (label)}
										<dt>now</dt><dd class="break-all">{fp ?? 'unknown'}</dd>
										<dt>was</dt><dd class="break-all">{h.jump_host_keys[label] ?? '—'}</dd>
									{/each}
								</dl>
								<Button variant="text" class="self-end" onclick={() => acceptHostKey(h)}>Review and trust…</Button>
							</div>
						{:else if h.state === 'error' && h.error}
							<div class="flex items-start gap-1.5 rounded-md bg-surface-container-high px-3 py-2 body-small text-on-surface">
								<Icon name="error" size={18} class="mt-px shrink-0 text-error" />
								<span class="min-w-0 flex-1 break-words">{h.error}</span>
								<button type="button" class="label-small text-primary hover:underline" onclick={() => reconnect(h)}>Try now</button>
							</div>
						{:else if h.state === 'disabled'}
							<p class="body-small text-on-surface-variant">Turned off: Castor isn't connecting to it.</p>
						{:else}
							<p class="body-small text-on-surface-variant">Connecting…</p>
						{/if}
					{/if}
					<div class="flex flex-wrap items-center gap-1">
						{#each a.tags as t (t)}<Chip variant="suggestion">{t}</Chip>{/each}
						<span class="flex-1"></span>
						{#if isAdmin() && sshOf(a)}
							{@const h = sshOf(a)!}
							<IconButton icon="edit" label="Edit SSH host" size="sm" onclick={() => editSshHost(h)} />
							<IconButton icon="delete" label="Remove SSH host" size="sm" onclick={() => removeSshHost(a, h)} />
						{:else if isAdmin()}
							<IconButton icon="edit" label="Edit agent" size="sm" onclick={() => edit(a)} />
							<IconButton icon="delete" label="Forget agent" size="sm" disabled={a.connected} onclick={() => removeAgent(a)} />
						{/if}
					</div>
				</Card>
				</div>
			{/each}
		</div>
	{/if}

	{#if waiting.length}
		<h2 class="mt-8 mb-2 title-medium text-on-surface">Tokens not used yet</h2>
		<div class="overflow-hidden rounded-md bg-surface-container-low">
			<table class="w-full body-medium">
				<tbody>
					{#each waiting as t (t.id)}
						<tr class="border-b border-outline-variant/50 last:border-0">
							<td class="px-4 py-2"><span class="flex items-center gap-2"><Icon name={t.revoked_at ? 'key_off' : 'key'} size={18} class={t.revoked_at ? 'text-error' : 'text-on-surface-variant'} /><span class="font-mono text-[12.5px] text-on-surface">{t.prefix}…</span></span></td>
							<td class="px-4 py-2 text-on-surface">{t.label || '—'}</td>
							<td class="px-4 py-2">{#each t.tags as tag (tag)}<Chip variant="suggestion">{tag}</Chip>{/each}</td>
							<td class="px-4 py-2 text-on-surface-variant" title={absoluteTime(t.created_at)}>{t.revoked_at ? 'revoked' : `created ${relativeTime(t.created_at)}`}</td>
							<td class="px-4 py-2 text-right">
								{#if !t.revoked_at}
									<Button variant="text" onclick={() => revoke(t, `token ${t.prefix}…`)}>Revoke</Button>
								{:else}
									<IconButton icon="delete" label="Delete token" size="sm" onclick={() => removeToken(t)} />
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}

	<h2 class="mt-8 mb-2 title-medium text-on-surface">Recent jobs</h2>
	<div class="overflow-hidden rounded-md bg-surface-container-low">
		<table class="w-full body-medium">
			<thead class="label-medium text-on-surface-variant">
				<tr class="border-b border-outline-variant text-left">
					<th class="px-4 py-2 font-medium">Status</th><th class="px-4 py-2 font-medium">Command</th><th class="px-4 py-2 font-medium">Agent</th><th class="px-4 py-2 font-medium">Queued</th><th class="px-4 py-2 font-medium">Duration</th>
				</tr>
			</thead>
			<tbody>
				{#each jobs as j (j.id)}
					<tr class="border-b border-outline-variant/50 last:border-0">
						<td class="px-4 py-2"><StatusChip job={j} /></td>
						<td class="max-w-md truncate px-4 py-2 font-mono text-[12px] text-on-surface" title={j.command}>{j.command}</td>
						<td class="px-4 py-2 text-on-surface">{agentName(j.agent_id)}</td>
						<td class="px-4 py-2 text-on-surface-variant" title={absoluteTime(j.queued_at)}>{relativeTime(j.queued_at)}</td>
						<td class="px-4 py-2 text-on-surface-variant">{j.started_at ? duration(j.started_at, j.finished_at) : '—'}</td>
					</tr>
				{:else}
					<tr><td colspan="5" class="px-4 py-6 text-center text-on-surface-variant">No jobs yet.</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>

<Dialog open={editing !== null} title="Edit agent {editing?.name ?? ''}" onclose={() => (editing = null)}>
	<div class="flex flex-col gap-5 pt-2 pb-2">
		<TagInput label="Tags" bind:value={tags} suggestions={tagSuggestions} noun="agents" placeholder="Add a tag" supporting="Cards and playbooks can target agents by tag." />
		<TextField label="Max concurrent jobs" bind:value={concurrency} type="number" />
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (editing = null)}>Cancel</Button>
		<Button onclick={saveAgent}>Save</Button>
	{/snippet}
</Dialog>

<SshConfigDialog bind:open={sshConfigOpen} onsaved={() => void reloadSshHosts()} />
<TagManagerDialog bind:open={agentTagsOpen} kind="agents" onchanged={() => void reloadTokens()} />
<SshHostDialog bind:open={sshOpen} host={sshEditing} hosts={sshHosts} onsaved={sshSaved} />

<AgentTokenDialog bind:open={enrollOpen} {wsUrl} {revealed} {sharedSecretAllowed} oncreated={() => void reloadTokens()} />
