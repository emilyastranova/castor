<script lang="ts">
	import type { SshHost, SshHostIn } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, Switch, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Add or edit an SSH host: a machine Castor connects to and runs jobs on. After adding
	 *  one with a generated key, it shows the public key to put in authorized_keys and
	 *  follows the connection until it's up. */
	interface Props {
		open: boolean;
		/** The host to edit; null to add one. */
		host?: SshHost | null;
		/** The page's live list, to follow the connection after saving. */
		hosts: SshHost[];
		onsaved?: (host: SshHost) => void;
	}
	let { open = $bindable(false), host = null, hosts, onsaved }: Props = $props();

	let name = $state('');
	let address = $state('');
	let port = $state('22');
	let username = $state('');
	let workdir = $state('castor-jobs');
	let network = $state('host');
	let tagsText = $state('');
	let concurrency = $state('1');
	let allowHost = $state(true);
	let allowInstall = $state(false);
	let enabled = $state(true);
	let keyMode = $state<'keep' | 'generate' | 'paste'>('generate');
	let privateKey = $state('');
	let passphrase = $state('');
	let advanced = $state(false);
	let saving = $state(false);
	let savedId = $state<string | null>(null);
	let copied = $state<string | null>(null);
	let openedFor = false;

	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (!open) return;
		const h = host;
		name = h?.name ?? '';
		address = h?.host ?? '';
		port = h?.port ? String(h.port) : '';
		username = h?.username ?? '';
		workdir = h?.workdir ?? 'castor-jobs';
		network = h?.network ?? 'host';
		tagsText = h?.tags.join(', ') ?? '';
		concurrency = String(h?.max_concurrency ?? 1);
		allowHost = h?.allow_host ?? true;
		allowInstall = h?.allow_host_install ?? false;
		enabled = h?.enabled ?? true;
		keyMode = h ? 'keep' : 'generate';
		privateKey = '';
		passphrase = '';
		advanced = false;
		savedId = null;
		copied = null;
	});

	/** The host as it is now (live), once saved. */
	let live = $derived(savedId ? (hosts.find((h) => h.id === savedId) ?? null) : null);
	/** Before the key is on the host, "permission denied" is the expected answer. */
	let waitingForKey = $derived(live?.state === 'error' && !!live.error?.startsWith('permission denied'));
	let failed = $derived((live?.state === 'error' && !waitingForKey) || live?.state === 'host_key_changed');
	let authorizedLine = $derived(live ? `echo '${live.public_key}' >> ~/.ssh/authorized_keys` : '');

	async function save() {
		const body: SshHostIn = {
			name: name.trim(),
			host: address.trim(),
			port: Number(port) || null,
			username: username.trim(),
			workdir: workdir.trim() || 'castor-jobs',
			network: network.trim(),
			allow_host: allowHost,
			allow_host_install: allowInstall,
			enabled,
			tags: tagsText.split(',').map((t) => t.trim()).filter(Boolean),
			max_concurrency: Number(concurrency) || 1,
			generate_key: keyMode === 'generate',
			...(keyMode === 'paste' ? { private_key: privateKey, passphrase: passphrase || undefined } : {})
		};
		saving = true;
		try {
			const saved = host ? await api().sshHosts.update(host.id, body) : await api().sshHosts.create(body);
			onsaved?.(saved);
			// A new key has to go on the host before anything works: show it.
			if (keyMode === 'generate') savedId = saved.id;
			else open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save the SSH host');
		} finally {
			saving = false;
		}
	}

	async function copy(text: string, what: string) {
		try {
			await navigator.clipboard.writeText(text);
			copied = what;
			setTimeout(() => (copied = copied === what ? null : copied), 2000);
		} catch {
			snackbar.error('Copying needs clipboard access; select the text instead');
		}
	}

	async function retry() {
		if (!live) return;
		try {
			await api().sshHosts.reconnect(live.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not reconnect');
		}
	}

	let canSave = $derived(
		!!name.trim() && !!address.trim() && (keyMode !== 'paste' || !!privateKey.trim())
	);
	type KeyMode = 'keep' | 'generate' | 'paste';
	let keyModes = $derived<{ id: KeyMode; label: string }[]>([
		...(host ? [{ id: 'keep' as const, label: 'Keep current key' }] : []),
		{ id: 'generate', label: host ? 'New key' : 'Generate a key' },
		{ id: 'paste', label: 'Paste a private key' }
	]);
</script>

<Dialog bind:open title={live ? `Add the key to ${live.name}` : host ? `Edit ${host.name}` : 'Add an SSH host'} icon="terminal" size="md">
	{#if live}
		<div class="flex flex-col gap-4 pb-2">
			<p class="body-medium text-on-surface">
				Castor signs in as <span class="font-mono">{live.route.at(-1) ?? `${live.username}@${live.host}`}</span> with this key. Add it to that account's
				<code class="font-mono">~/.ssh/authorized_keys</code>, on the host{live.route.length > 1 ? ' and on each jump host on the way' : ''}:
			</p>
			<div>
				<div class="mb-1 flex items-center justify-between">
					<span class="label-large text-on-surface">Run on the host</span>
					<Button variant="text" icon={copied === 'line' ? 'check' : 'content_copy'} onclick={() => copy(authorizedLine, 'line')}>{copied === 'line' ? 'Copied' : 'Copy'}</Button>
				</div>
				<pre class="overflow-auto rounded-sm bg-surface-container-highest p-3 font-mono text-[12.5px] break-all whitespace-pre-wrap text-on-surface select-all">{authorizedLine}</pre>
				<p class="mt-1.5 body-small text-on-surface-variant">Key {live.key_fingerprint}. Castor keeps the private key in its vault; it never leaves the server.</p>
			</div>
			<div class="flex items-start gap-3 rounded-md px-3 py-2.5 {live.state === 'connected' ? 'bg-primary-container text-on-primary-container' : failed ? 'bg-error-container text-on-error-container' : 'bg-surface-container-high text-on-surface'}">
				<Icon name={live.state === 'connected' ? 'check_circle' : failed ? 'error' : waitingForKey ? 'key' : 'sync'} size={20} class="mt-px shrink-0" />
				<div class="min-w-0 flex-1 body-medium">
					{#if live.state === 'connected'}
						Connected. It's in the agent list and takes jobs now.
					{:else if waitingForKey}
						Waiting for the key. Once it's on the host, Castor connects within a minute, or press Try now.
					{:else if failed}
						Not connected yet: {live.error}
					{:else}
						Connecting…
					{/if}
				</div>
				{#if live.state === 'error'}<Button variant="text" onclick={retry}>Try now</Button>{/if}
			</div>
		</div>
	{:else}
		<div class="flex flex-col gap-4 pt-1 pb-2">
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_6rem]">
				<TextField label="Host" bind:value={address} placeholder="10.0.0.5 or lab-box.internal" required supporting="An address, or a Host from the SSH config (for jump hosts)." />
				<TextField label="Port" bind:value={port} type="number" placeholder="22" />
			</div>
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
				<TextField label="Username" bind:value={username} placeholder="castor" autocomplete="off" supporting="Empty: the SSH config's User." />
				<TextField label="Name" bind:value={name} placeholder="lab-box-01" required supporting="How it's listed with the agents." />
			</div>

			<fieldset class="flex flex-col gap-3">
				<legend class="mb-2 label-large text-on-surface">Key</legend>
				<div class="flex flex-wrap gap-2" role="radiogroup" aria-label="Key">
					{#each keyModes as mode (mode.id)}
						<button
							type="button"
							role="radio"
							aria-checked={keyMode === mode.id}
							class="state-layer flex h-8 items-center gap-1.5 rounded-sm border px-3 label-large {keyMode === mode.id ? 'border-transparent bg-secondary-container text-on-secondary-container' : 'border-outline text-on-surface-variant'}"
							onclick={() => (keyMode = mode.id)}
						>
							{#if keyMode === mode.id}<Icon name="check" size={18} />{/if}{mode.label}
						</button>
					{/each}
				</div>
				{#if keyMode === 'keep' && host}
					<p class="body-small text-on-surface-variant">Signs in with key <span class="font-mono">{host.key_fingerprint}</span>.</p>
				{:else if keyMode === 'generate'}
					<p class="body-small text-on-surface-variant">Castor makes an ed25519 key pair and shows you the public key to add to the host{host ? ' (the old key stops being used)' : ''}.</p>
				{:else if keyMode === 'paste'}
					<TextField label="Private key" bind:value={privateKey} multiline rows={4} mono placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
					<TextField label="Passphrase (if it has one)" bind:value={passphrase} type="password" autocomplete="off" supporting="Castor decrypts the key once and keeps it in its vault." />
				{/if}
			</fieldset>

			<div class="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_10rem]">
				<TextField label="Tags" bind:value={tagsText} supporting="Comma separated. Cards can target hosts by tag." />
				<TextField label="Max concurrent jobs" bind:value={concurrency} type="number" />
			</div>

			<button type="button" class="flex items-center gap-1 self-start label-large text-primary" aria-expanded={advanced} onclick={() => (advanced = !advanced)}>
				<Icon name={advanced ? 'expand_less' : 'expand_more'} size={20} />More settings
			</button>
			{#if advanced}
				<div class="flex flex-col gap-4">
					<TextField label="Work directory" bind:value={workdir} mono supporting="Each job gets a folder in it. A relative path is under the account's home." />
					<TextField label="Container network" bind:value={network} mono supporting="For podman or docker on the host: host (the default) or bridge. A command's own --network wins." />
					<Switch bind:checked={allowHost} label="Run commands in the host's shell (not only in containers)" />
					<Switch bind:checked={allowInstall} label="Let tools install themselves on the host" disabled={!allowHost} />
					<Switch bind:checked={enabled} label="Use this host (off: Castor disconnects and leaves it alone)" />
				</div>
			{/if}
		</div>
	{/if}
	{#snippet actions()}
		{#if live}
			<Button onclick={() => (open = false)}>Done</Button>
		{:else}
			<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
			<Button icon={host ? 'save' : 'add'} loading={saving} disabled={!canSave} onclick={save}>{host ? 'Save' : 'Add host'}</Button>
		{/if}
	{/snippet}
</Dialog>
