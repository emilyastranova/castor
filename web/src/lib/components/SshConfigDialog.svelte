<script lang="ts">
	import type { SshConfig } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	/** The SSH config SSH hosts are reached with (ssh_config format): checked as you type, with
	 *  each host's route under it. */
	interface Props {
		open: boolean;
		onsaved?: () => void;
	}
	let { open = $bindable(false), onsaved }: Props = $props();

	const EXAMPLE = `Host bastion
    HostName bastion.example.net
    User jump

Host lab-*
    ProxyJump bastion
    User kali`;

	let text = $state('');
	let saved = $state('');
	let result = $state<SshConfig | null>(null);
	let loading = $state(false);
	let saving = $state(false);
	let openedFor = false;
	let timer: ReturnType<typeof setTimeout> | undefined;

	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (!open) return;
		loading = true;
		result = null;
		api()
			.sshHosts.config()
			.then((c) => {
				text = saved = c.text;
				result = c;
			})
			.catch((err) => snackbar.error(err instanceof ApiError ? err.message : 'Could not load the SSH config'))
			.finally(() => (loading = false));
	});

	function changed() {
		clearTimeout(timer);
		const draft = text;
		timer = setTimeout(async () => {
			try {
				const checked = await api().sshHosts.checkConfig(draft);
				if (draft === text) result = checked;
			} catch {
				/* the next keystroke checks again */
			}
		}, 350);
	}

	async function save() {
		saving = true;
		try {
			result = await api().sshHosts.saveConfig(text);
			saved = text;
			onsaved?.();
			open = false;
			snackbar.show('SSH config saved; SSH hosts reconnect with it');
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save the SSH config');
		} finally {
			saving = false;
		}
	}

	let routes = $derived(result ? Object.entries(result.routes).sort(([a], [b]) => a.localeCompare(b)) : []);
	let routeErrors = $derived(result ? Object.entries(result.route_errors) : []);
</script>

<Dialog bind:open title="SSH config" icon="route" size="lg">
	<div class="flex flex-col gap-4 pb-2">
		<p class="body-medium text-on-surface">
			How Castor reaches SSH hosts, in the usual <code class="font-mono">ssh_config</code> format: aliases, users, ports and jump hosts
			(<code class="font-mono">ProxyJump</code>). A host's own port and username win over it.
		</p>
		<TextField label="Config" bind:value={text} multiline rows={12} mono placeholder={EXAMPLE} disabled={loading} oninput={changed} />
		<p class="-mt-2 body-small text-on-surface-variant">
			Every hop signs in with the SSH host's own key, so add that key on the jump hosts too. Each jump host's key is trusted on first use,
			like the host's. Castor uses Host, HostName, Port, User, ProxyJump, ConnectTimeout and ServerAlive* and ignores the rest;
			ProxyCommand, Match and Include are refused, because they would run commands or read files on the server.
		</p>

		{#if result?.errors.length}
			<ul class="flex flex-col gap-1 rounded-md bg-error-container px-3 py-2 body-small text-on-error-container">
				{#each result.errors as e (e)}<li class="flex items-start gap-1.5"><Icon name="error" size={16} class="mt-px shrink-0" />{e}</li>{/each}
			</ul>
		{/if}
		{#if result?.warnings.length}
			<ul class="flex flex-col gap-1 rounded-md bg-surface-container-high px-3 py-2 body-small text-on-surface-variant">
				{#each result.warnings as w (w)}<li class="flex items-start gap-1.5"><Icon name="info" size={16} class="mt-px shrink-0" />{w}</li>{/each}
			</ul>
		{/if}
		{#if !result?.errors.length && (routes.length || routeErrors.length)}
			<div>
				<h3 class="mb-1.5 label-large text-on-surface">Routes</h3>
				<ul class="flex flex-col gap-1 body-small">
					{#each routes as [name, hops] (name)}
						<li class="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5">
							<span class="text-on-surface">{name}</span>
							<Icon name="arrow_forward" size={14} class="text-on-surface-variant" />
							{#each hops as hop, i (i)}
								{#if i > 0}<Icon name="chevron_right" size={14} class="text-on-surface-variant" />{/if}
								<span class="font-mono {i === hops.length - 1 ? 'text-on-surface' : 'text-on-surface-variant'}">{hop}</span>
							{/each}
						</li>
					{/each}
					{#each routeErrors as [name, error] (name)}
						<li class="flex items-start gap-1.5 text-error"><Icon name="error" size={14} class="mt-0.5 shrink-0" />{name}: {error}</li>
					{/each}
				</ul>
			</div>
		{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="save" loading={saving} disabled={loading || text === saved || !!result?.errors.length} onclick={save}>Save</Button>
	{/snippet}
</Dialog>
