<script lang="ts">
	import type { InventoryHost, SshHost, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, IconButton, Switch, TagInput, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { copyText } from '$lib/copyCode';
	import AgentProjectsPicker from './AgentProjectsPicker.svelte';

	/** Agents → Add from an Ansible inventory: paste an inventory (INI, YAML or `ansible-inventory
	 *  --list` JSON), or take one a run read (`job`), pick its hosts and add them as SSH hosts with
	 *  one key for them all and their groups as tags. */
	interface Props {
		open: boolean;
		/** A run's output that read an inventory (the Ansible plugin's inventory command, AWX). */
		job?: { id: UUID; output?: string } | null;
		onadded?: (hosts: SshHost[]) => void;
	}
	let { open = $bindable(false), job = null, onadded }: Props = $props();

	let text = $state('');
	let hosts = $state<InventoryHost[] | null>(null);
	let error = $state<string | null>(null);
	let reading = $state(false);
	let picked = $state<Set<string>>(new Set());
	let filter = $state('');
	let prefix = $state('');
	let username = $state('');
	let tags = $state<string[]>([]);
	let groupsAsTags = $state(true);
	let concurrency = $state(1);
	let projectIds = $state<UUID[] | null>(null);
	let keyMode = $state<'generate' | 'paste'>('generate');
	let privateKey = $state('');
	let passphrase = $state('');
	let busy = $state(false);
	let publicKey = $state<string | null>(null);

	$effect(() => {
		if (!open) return;
		hosts = null;
		error = null;
		publicKey = null;
		picked = new Set();
		if (job) void read();
	});

	function source() {
		return job ? { job_id: job.id, output: job.output } : { inventory: text };
	}
	async function read() {
		reading = true;
		error = null;
		try {
			hosts = await api().sshHosts.readInventory(source(), prefix);
			picked = new Set(hosts.filter((h) => !h.skip && !h.exists).map((h) => h.name));
		} catch (err) {
			hosts = null;
			error = err instanceof ApiError ? err.message : 'Could not read it';
		} finally {
			reading = false;
		}
	}
	// A new prefix changes the names: say again which are taken.
	let prefixTimer: ReturnType<typeof setTimeout> | undefined;
	function prefixChanged() {
		clearTimeout(prefixTimer);
		if (hosts) prefixTimer = setTimeout(read, 300);
	}

	let shown = $derived(
		(hosts ?? []).filter((h) => {
			const q = filter.trim().toLowerCase();
			return !q || [h.name, h.host, h.user, ...h.groups].some((v) => v.toLowerCase().includes(q));
		})
	);
	let available = $derived(shown.filter((h) => !h.skip && !h.exists));
	function toggle(name: string) {
		const next = new Set(picked);
		if (next.has(name)) next.delete(name);
		else next.add(name);
		picked = next;
	}
	function all() {
		const every = available.every((h) => picked.has(h.name));
		picked = every ? new Set([...picked].filter((n) => !available.some((h) => h.name === n))) : new Set([...picked, ...available.map((h) => h.name)]);
	}

	async function add() {
		busy = true;
		try {
			const r = await api().sshHosts.fromInventory({
				...source(), names: [...picked], prefix, username, tags, groups_as_tags: groupsAsTags, max_concurrency: Number(concurrency) || 1,
				project_ids: projectIds, generate_key: keyMode === 'generate',
				...(keyMode === 'paste' ? { private_key: privateKey, passphrase: passphrase || undefined } : {})
			});
			onadded?.(r.added);
			const skipped = Object.keys(r.skipped).length;
			snackbar.show(`Added ${r.added.length} SSH host${r.added.length === 1 ? '' : 's'}` + (skipped ? `; ${skipped} skipped` : ''));
			if (keyMode === 'generate' && r.added.length) publicKey = r.public_key;
			else open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not add them');
		} finally {
			busy = false;
		}
	}
	let authorizeLine = $derived(publicKey ? `echo '${publicKey}' >> ~/.ssh/authorized_keys` : '');
	let ansibleLine = $derived(publicKey ? `ansible all -i inventory -m ansible.posix.authorized_key -a "user={{ ansible_user }} key='${publicKey}'"` : '');
</script>

<Dialog bind:open title="Add hosts from an Ansible inventory" icon="lan" size="lg">
	<div class="flex flex-col gap-4 pt-1 pb-2">
		{#if publicKey}
			<p class="body-medium text-on-surface">They share one key. Add its public key to each host's <span class="font-mono">~/.ssh/authorized_keys</span>; they connect once it's there.</p>
			<div class="flex items-start gap-2 rounded-md bg-surface-container px-3 py-2">
				<code class="min-w-0 flex-1 font-mono text-[12.5px] break-all text-on-surface">{authorizeLine}</code>
				<IconButton icon="content_copy" label="Copy" size="sm" onclick={() => copyText(authorizeLine)} />
			</div>
			<p class="body-small text-on-surface-variant">Or with Ansible itself, from where you run it:</p>
			<div class="flex items-start gap-2 rounded-md bg-surface-container px-3 py-2">
				<code class="min-w-0 flex-1 font-mono text-[12.5px] break-all text-on-surface">{ansibleLine}</code>
				<IconButton icon="content_copy" label="Copy" size="sm" onclick={() => copyText(ansibleLine)} />
			</div>
		{:else}
			{#if !job}
				<TextField label="Inventory" bind:value={text} multiline rows={hosts ? 3 : 10} mono
					placeholder={'[web]\nweb[01:03].lab ansible_user=deploy\n\n[db]\ndb01.lab ansible_port=2222'}
					supporting="INI or YAML, or what ansible-inventory --list prints. Only how to reach each host is read; nothing in it runs. For a repository's inventory or a dynamic one, run the Ansible plugin's “Ansible inventory” command and add its hosts from the run." />
				<div><Button variant="tonal" icon="checklist" loading={reading} disabled={!text.trim()} onclick={read}>Read its hosts</Button></div>
			{:else if reading && !hosts}
				<p class="py-6 text-center body-medium text-on-surface-variant">Reading the run's hosts…</p>
			{/if}
			{#if error}
				<p class="rounded-md bg-error-container px-3 py-2 body-medium text-on-error-container">{error}</p>
			{/if}
			{#if hosts}
				{#if !hosts.length}
					<p class="py-4 text-center body-medium text-on-surface-variant">It has no hosts.</p>
				{:else}
					<div class="flex flex-wrap items-center gap-2">
						<label class="flex min-w-[14rem] flex-1 items-center gap-2 rounded-full bg-surface-container-high px-4">
							<Icon name="filter_list" size={20} class="text-on-surface-variant" />
							<input class="h-10 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter: name, address, group" bind:value={filter} autocomplete="off" spellcheck="false" />
							{#if filter}<IconButton icon="close" label="Clear" size="sm" onclick={() => (filter = '')} />{/if}
						</label>
						<Button variant="text" icon="select_all" disabled={!available.length} onclick={all}>{available.length && available.every((h) => picked.has(h.name)) ? 'None' : `All ${available.length}`}</Button>
					</div>
					<ul class="flex max-h-72 flex-col divide-y divide-outline-variant/50 overflow-y-auto rounded-md border border-outline-variant/60">
						{#each shown as h (h.name)}
							{@const off = !!h.skip || h.exists}
							<li>
								<label class="flex items-start gap-3 px-3 py-2 {off ? 'opacity-60' : 'cursor-pointer'}">
									<input type="checkbox" class="mt-1 accent-primary" checked={!off && picked.has(h.name)} disabled={off} onchange={() => toggle(h.name)} />
									<span class="min-w-0 flex-1">
										<span class="block body-medium text-on-surface">{h.agent_name}
											{#if h.exists}<span class="ml-2 body-small text-on-surface-variant">already an agent</span>{/if}
										</span>
										<span class="block font-mono text-[12px] text-on-surface-variant">{h.user || username || '(SSH config user)'}@{h.host}{h.port ? `:${h.port}` : ''}</span>
										{#if h.skip}<span class="block body-small text-error">Skipped: {h.skip}</span>{/if}
										{#if h.groups.length}
											<span class="mt-0.5 flex flex-wrap gap-1">{#each h.groups.slice(0, 8) as g (g)}<span class="rounded-xs bg-surface-container-highest px-1.5 py-px label-small text-on-surface-variant">{g}</span>{/each}{#if h.groups.length > 8}<span class="label-small text-on-surface-variant">+{h.groups.length - 8}</span>{/if}</span>
										{/if}
									</span>
								</label>
							</li>
						{:else}
							<li class="px-3 py-4 text-center body-medium text-on-surface-variant">Nothing matches.</li>
						{/each}
					</ul>
					<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
						<TextField label="Name prefix" bind:value={prefix} placeholder="lab-" oninput={prefixChanged} supporting="Before each inventory name, as its agent name." />
						<TextField label="Username" bind:value={username} placeholder="deploy" autocomplete="off" supporting="For hosts the inventory gives no ansible_user." />
					</div>
					<fieldset class="flex flex-col gap-3">
						<legend class="mb-2 label-large text-on-surface">One key for them all</legend>
						<div class="flex flex-wrap gap-2" role="radiogroup" aria-label="Key">
							{#each [{ id: 'generate', label: 'Generate a key' }, { id: 'paste', label: 'Paste a private key' }] as mode (mode.id)}
								<button type="button" role="radio" aria-checked={keyMode === mode.id}
									class="state-layer flex h-8 items-center gap-1.5 rounded-sm border px-3 label-large {keyMode === mode.id ? 'border-transparent bg-secondary-container text-on-secondary-container' : 'border-outline text-on-surface-variant'}"
									onclick={() => (keyMode = mode.id as 'generate' | 'paste')}>
									{#if keyMode === mode.id}<Icon name="check" size={18} />{/if}{mode.label}
								</button>
							{/each}
						</div>
						{#if keyMode === 'paste'}
							<TextField label="Private key" bind:value={privateKey} multiline rows={4} mono placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" supporting="The key Ansible already signs in with, say." />
							<TextField label="Passphrase (if it has one)" bind:value={passphrase} type="password" autocomplete="off" />
						{:else}
							<p class="body-small text-on-surface-variant">Castor makes an ed25519 key pair and shows you its public key to put on the hosts.</p>
						{/if}
					</fieldset>
					<div class="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_10rem]">
						<TagInput label="Tags for them" bind:value={tags} noun="agents" placeholder="Add a tag" supporting="On top of their groups, if those become tags." />
						<TextField label="Max concurrent jobs" bind:value={concurrency} type="number" />
					</div>
					<Switch bind:checked={groupsAsTags} label="Their inventory groups as tags" />
					<AgentProjectsPicker bind:value={projectIds} supporting="Which projects' jobs they may run." />
				{/if}
			{/if}
		{/if}
	</div>
	{#snippet actions()}
		{#if publicKey}
			<Button onclick={() => (open = false)}>Done</Button>
		{:else}
			<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
			{#if hosts?.length}<Button icon="add" loading={busy} disabled={!picked.size || (keyMode === 'paste' && !privateKey.trim())} onclick={add}>Add {picked.size || ''} host{picked.size === 1 ? '' : 's'}</Button>{/if}
		{/if}
	{/snippet}
</Dialog>
