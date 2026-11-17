<script lang="ts">
	import { Button, Chip, Icon, IconButton, TextField } from '$lib/m3';
	import { HOP_KINDS, hopKind } from '$lib/routes';
	import type { HopKind, RouteHop } from '$lib/api';
	import { snackbar } from '$lib/snackbar.svelte';

	/** A hop being edited. Secrets are write-only: `secret.mode` says what Save sends. */
	export interface DraftHop {
		key: string;
		id?: string;
		kind: HopKind;
		host: string;
		port: string;
		username: string;
		label: string;
		auth: 'none' | 'password' | 'key';
		secret: { mode: 'keep' | 'password' | 'paste' | 'generate' | 'none'; password: string; private_key: string; passphrase: string };
		pin: string;
		stored?: RouteHop;
	}
	interface Props {
		hop: DraftHop;
		index: number;
		count: number;
		readonly?: boolean;
		onremove?: () => void;
		onmove?: (delta: number) => void;
		/** Accept the host key it shows now (the route must be saved). */
		onaccept?: () => void;
	}
	let { hop = $bindable(), index, count, readonly = false, onremove, onmove, onaccept }: Props = $props();

	const k = $derived(hopKind(hop.kind));
	const stored = $derived(hop.stored && hop.stored.kind === hop.kind ? hop.stored : undefined);
	const movedHost = $derived(!!stored && (stored.host !== hop.host.trim() || String(stored.port) !== (hop.port.trim() || String(k.port))));

	function setKind(kind: HopKind) {
		if (kind === hop.kind) return;
		hop.kind = kind;
		const keepsSecret = hop.stored?.kind === kind;
		if (kind === 'ssh') {
			hop.auth = keepsSecret ? hop.stored!.auth : 'key';
			hop.secret.mode = keepsSecret ? 'keep' : 'generate';
		} else {
			hop.auth = keepsSecret ? hop.stored!.auth : 'none';
			hop.secret.mode = keepsSecret ? 'keep' : 'none';
		}
	}
	function setAuth(auth: 'none' | 'password' | 'key') {
		hop.auth = auth;
		if (stored && stored.auth === auth && stored.has_secret) hop.secret.mode = 'keep';
		else hop.secret.mode = auth === 'none' ? 'none' : auth === 'password' ? 'password' : 'generate';
	}
	async function copy(text: string) {
		try {
			await navigator.clipboard.writeText(text);
			snackbar.show('Copied the public key');
		} catch {
			snackbar.show('Couldn’t copy; select the key and copy it');
		}
	}
</script>

<section class="flex flex-col gap-4 rounded-lg border border-outline-variant bg-surface-container-low p-4" aria-label={`Hop ${index + 1}`}>
	<header class="flex items-center gap-2">
		<span class="flex size-8 items-center justify-center rounded-full bg-primary-container text-on-primary-container"><Icon name={k.icon} size={18} /></span>
		<h3 class="min-w-0 flex-1 title-small text-on-surface">Hop {index + 1} <span class="text-on-surface-variant">of {count}</span></h3>
		{#if !readonly}
			<IconButton icon="arrow_back" label="Move it earlier" disabled={index === 0} onclick={() => onmove?.(-1)} class="max-md:rotate-90" />
			<IconButton icon="arrow_forward" label="Move it later" disabled={index === count - 1} onclick={() => onmove?.(1)} class="max-md:rotate-90" />
			<IconButton icon="delete" label="Remove this hop" disabled={count === 1} onclick={() => onremove?.()} />
		{/if}
	</header>

	<div class="flex flex-wrap gap-2" role="radiogroup" aria-label="What this hop is">
		{#each HOP_KINDS as option (option.value)}
			<Chip variant="filter" selected={hop.kind === option.value} icon={option.icon} title={option.hint} disabled={readonly}
				onclick={() => setKind(option.value)}>{option.label}</Chip>
		{/each}
	</div>
	<p class="-mt-2 body-small text-on-surface-variant">{k.hint}{index === 0 ? '. The agent connects to it directly.' : `, reached through hop ${index}.`}</p>

	<div class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem]">
		<TextField label="Host" bind:value={hop.host} mono placeholder={index === 0 ? 'jump01.example.com' : '10.0.0.5'} disabled={readonly}
			supporting={index > 0 ? 'As the hop before sees it' : ''} />
		<TextField label="Port" bind:value={hop.port} type="number" mono placeholder={String(k.port)} disabled={readonly} />
	</div>
	<div class="grid gap-3 sm:grid-cols-2">
		<TextField label={hop.kind === 'ssh' ? 'User' : hop.kind === 'socks4' ? 'User id (optional)' : 'User (optional)'} bind:value={hop.username} mono
			disabled={readonly} />
		<TextField label="Name (optional)" bind:value={hop.label} placeholder="DMZ jumpbox" disabled={readonly} />
	</div>

	{#if hop.kind !== 'socks4'}
		<div class="flex flex-col gap-2">
			<span class="label-large text-on-surface">Signs in with</span>
			<div class="flex flex-wrap gap-2" role="radiogroup" aria-label="How it signs in">
				{#if hop.kind === 'ssh'}
					<Chip variant="filter" icon="key" selected={hop.auth === 'key'} disabled={readonly} onclick={() => setAuth('key')}>A key</Chip>
				{:else}
					<Chip variant="filter" icon="lock_open" selected={hop.auth === 'none'} disabled={readonly} onclick={() => setAuth('none')}>Nothing</Chip>
				{/if}
				<Chip variant="filter" icon="password" selected={hop.auth === 'password'} disabled={readonly} onclick={() => setAuth('password')}>A password</Chip>
			</div>

			{#if hop.auth === 'key'}
				{#if hop.secret.mode === 'keep' && stored?.public_key}
					<div class="flex flex-col gap-2 rounded-md bg-surface-container p-3">
						<p class="body-small text-on-surface-variant">
							Castor signs in with its own key. Put this line in <span class="font-mono">~/.ssh/authorized_keys</span>
							for <span class="font-mono">{hop.username || 'the user'}</span> on that host:
						</p>
						<div class="flex items-start gap-2">
							<code class="min-w-0 flex-1 break-all rounded-sm bg-surface-container-highest p-2 font-mono text-[12px] text-on-surface">{stored.public_key}</code>
							<IconButton icon="content_copy" label="Copy the public key" onclick={() => copy(stored.public_key)} />
						</div>
						{#if !readonly}
							<div class="flex flex-wrap gap-2">
								<Button variant="text" icon="autorenew" onclick={() => (hop.secret.mode = 'generate')}>Make a new key</Button>
								<Button variant="text" icon="content_paste" onclick={() => (hop.secret.mode = 'paste')}>Use a key of mine</Button>
							</div>
						{/if}
					</div>
				{:else if hop.secret.mode === 'keep'}
					<p class="body-small text-on-surface-variant">A key is stored for it (a pasted one; Castor shows no public key for it).
						{#if !readonly}<button type="button" class="text-primary underline" onclick={() => (hop.secret.mode = 'paste')}>Replace it</button>{/if}</p>
				{:else if hop.secret.mode === 'generate'}
					<div class="flex flex-wrap items-center gap-2 rounded-md bg-surface-container p-3 body-small text-on-surface-variant">
						<Icon name="auto_awesome" size={18} class="text-primary" />
						<span class="min-w-0 flex-1">Castor makes an ed25519 key when you save, and shows its public half to authorize on the host.</span>
						<Button variant="text" icon="content_paste" onclick={() => (hop.secret.mode = 'paste')}>Paste a key instead</Button>
					</div>
				{:else}
					<TextField label="Private key" bind:value={hop.secret.private_key} multiline rows={4} mono placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
						supporting="Kept in Castor’s vault; nobody can read it back" />
					<TextField label="Its passphrase (if it has one)" bind:value={hop.secret.passphrase} type="password" />
					<div><Button variant="text" icon="auto_awesome" onclick={() => (hop.secret.mode = stored?.has_secret ? 'keep' : 'generate')}>
						{stored?.has_secret ? 'Keep the stored key' : 'Make a key instead'}</Button></div>
				{/if}
			{:else if hop.auth === 'password'}
				<TextField label="Password" type="password" bind:value={hop.secret.password} disabled={readonly}
					placeholder={stored?.auth === 'password' && stored.has_secret ? 'Stored; type to replace it' : ''}
					oninput={() => (hop.secret.mode = hop.secret.password ? 'password' : stored?.auth === 'password' ? 'keep' : 'password')}
					supporting="Kept in Castor’s vault; masked in job output" />
			{/if}
		</div>
	{/if}

	{#if hop.kind === 'ssh'}
		<div class="flex flex-col gap-2">
			<span class="label-large text-on-surface">Host key</span>
			{#if stored?.host_key_pending && !movedHost}
				<div class="flex flex-col gap-2 rounded-md bg-error-container p-3 text-on-error-container" role="alert">
					<span class="flex items-center gap-2 title-small"><Icon name="gpp_maybe" size={20} /> It shows a different host key</span>
					<p class="body-small">Jobs on this route stop at this hop until someone checks the new key with whoever runs the host. It can be a
						reinstalled machine, or someone in the middle.</p>
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 font-mono text-[12px]">
						<dt>Trusted</dt><dd class="break-all">{stored.host_key_fingerprint}</dd>
						<dt>Now</dt><dd class="break-all">{stored.host_key_pending_fingerprint}</dd>
					</dl>
					{#if !readonly && onaccept}<div><Button variant="tonal" icon="verified_user" onclick={onaccept}>It’s the right machine: trust the new key</Button></div>{/if}
				</div>
			{:else if stored?.host_key_fingerprint && !movedHost}
				<p class="flex items-center gap-2 body-small text-on-surface-variant"><Icon name="verified_user" size={18} class="text-primary" />
					Trusted: <span class="break-all font-mono">{stored.host_key_fingerprint}</span></p>
			{:else}
				<p class="body-small text-on-surface-variant">
					{movedHost ? 'It points somewhere new, so its old key is forgotten. ' : ''}The key it shows the first time an agent reaches it is trusted; a
					different one later stops the route until someone accepts it. To be sure from the start, pin it:
				</p>
				<TextField label="Pin its key (optional)" bind:value={hop.pin} mono placeholder="ssh-ed25519 AAAA…" disabled={readonly}
					supporting="A line from ssh-keyscan, or the host’s /etc/ssh/ssh_host_ed25519_key.pub" />
			{/if}
		</div>
	{/if}
</section>
