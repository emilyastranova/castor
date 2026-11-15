<script lang="ts">
	import type { McpConnection } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon, IconButton, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { relativeTime } from '$lib/format';
	import McpConnectionDialog from '$lib/components/McpConnectionDialog.svelte';
	import LibraryTransfer from '$lib/components/LibraryTransfer.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let connections = $state<McpConnection[]>(data.connections);
	let dialogOpen = $state(false);
	let editing = $state<McpConnection | null>(null);
	let checking = $state<string | null>(null);
	let shown = $state<string | null>(null); // whose tools are unfolded

	function open(c: McpConnection | null) {
		editing = c;
		dialogOpen = true;
	}
	function saved(c: McpConnection) {
		connections = [...connections.filter((x) => x.id !== c.id), c].sort((a, b) => a.name.localeCompare(b.name));
		shown = c.id;
		if (c.last_error) snackbar.error(`${c.name}: ${c.last_error}`);
	}
	async function check(c: McpConnection) {
		checking = c.id;
		try {
			saved(await api().mcp.check(c.id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not check it');
		} finally {
			checking = null;
		}
	}
	async function remove(c: McpConnection) {
		const used = c.used_by.length ? ` ${c.used_by.length} definition${c.used_by.length === 1 ? '' : 's'} call it (${c.used_by.join(', ')}); their runs will fail until it's back.` : '';
		if (!(await confirmAction({ title: `Remove ${c.name}?`, message: `Castor stops calling its tools.${used}`, confirmLabel: 'Remove', danger: true }))) return;
		try {
			await api().mcp.remove(c.id);
			connections = connections.filter((x) => x.id !== c.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove it');
		}
	}
	const where = (c: McpConnection) => (c.transport !== 'stdio' ? c.url : `${c.command.join(' ') || c.image || c.tool} · on an agent`);
	const argNames = (t: McpConnection['tools'][number]) => Object.keys(t.inputSchema?.properties ?? {});
</script>

<svelte:head><title>MCP servers · Settings · Castor</title></svelte:head>

<TopAppBar title="MCP servers" subtitle="Other MCP servers whose tools cards and playbooks can call">
	{#snippet actions()}
		<LibraryTransfer noun="MCP servers" request={{ mcp_servers: connections.map((c) => c.id), label: 'castor-mcp-servers' }} count={connections.length} onimported={async () => (connections = await api().mcp.list())} />
		<Button icon="add" onclick={() => open(null)}>New MCP server</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-3 pt-2">
		{#each connections as c (c.id)}
			<Card variant="outlined" class="flex flex-col gap-3 p-4 {c.enabled ? '' : 'opacity-60'}">
				<div class="flex items-center gap-4">
					<span class="flex size-10 shrink-0 items-center justify-center rounded-md bg-secondary-container text-on-secondary-container">
						<Icon name={c.transport !== 'stdio' ? 'cloud' : 'terminal'} />
					</span>
					<button type="button" class="min-w-0 flex-1 text-left" onclick={() => open(c)}>
						<span class="flex items-center gap-2">
							<span class="truncate title-small text-on-surface">{c.name}</span>
							<code class="rounded-full bg-surface-container-highest px-2 py-0.5 font-mono label-small text-on-surface-variant">{c.id}</code>
							{#if c.server_info?.name}<span class="truncate label-small text-on-surface-variant">{c.server_info.name} {c.server_info.version ?? ''}</span>{/if}
						</span>
						<span class="block truncate font-mono body-small text-on-surface-variant">{where(c)}</span>
						{#if c.last_error}
							<span class="mt-0.5 flex items-start gap-1 body-small text-error"><Icon name="error" size={16} class="mt-0.5 shrink-0" />{c.last_error}</span>
						{:else if c.checked_at}
							<span class="block body-small text-on-surface-variant">{c.tools.length} tool{c.tools.length === 1 ? '' : 's'} · checked {relativeTime(c.checked_at)}{c.used_by.length ? ` · ${c.used_by.length} action${c.used_by.length === 1 ? '' : 's'} call it` : ''}</span>
						{:else}
							<span class="block body-small text-on-surface-variant">Not checked yet</span>
						{/if}
					</button>
					<Button variant="tonal" icon="sync" loading={checking === c.id} onclick={() => check(c)}>Check</Button>
					<IconButton icon="delete" label="Remove {c.name}" onclick={() => remove(c)} />
				</div>
				{#if c.tools.length}
					<button type="button" class="flex items-center gap-1 self-start label-large text-primary" aria-expanded={shown === c.id} onclick={() => (shown = shown === c.id ? null : c.id)}>
						<Icon name={shown === c.id ? 'expand_less' : 'expand_more'} size={18} />{shown === c.id ? 'Hide its tools' : `Show its ${c.tools.length} tools`}
					</button>
					{#if shown === c.id}
						<ul class="flex flex-col divide-y divide-outline-variant/60 rounded-md border border-outline-variant">
							{#each c.tools as t (t.name)}
								<li class="flex items-start gap-3 px-3 py-2">
									<div class="min-w-0 flex-1">
										<p class="font-mono label-large text-on-surface">{t.name}{#if t.title && t.title !== t.name}<span class="ml-2 font-sans body-small text-on-surface-variant">{t.title}</span>{/if}</p>
										{#if t.description}<p class="line-clamp-2 body-small text-on-surface-variant">{t.description}</p>{/if}
										{#if argNames(t).length}<p class="truncate font-mono text-[11.5px] text-on-surface-variant">{argNames(t).join(', ')}</p>{/if}
									</div>
									<Button variant="text" icon="add" href="/library?new=mcp&server={encodeURIComponent(c.id)}&tool={encodeURIComponent(t.name)}">Make an action</Button>
								</li>
							{/each}
						</ul>
					{/if}
				{/if}
			</Card>
		{:else}
			<Card variant="outlined" class="flex flex-col items-start gap-2 p-6">
				<p class="title-small text-on-surface">No MCP servers yet</p>
				<p class="max-w-[65ch] body-medium text-on-surface-variant">
					Add one to call its tools from cards and playbooks: a remote server Castor reaches over HTTP, or a program that speaks MCP on stdio, which an agent runs for Castor. Its token comes from a variable set, so it stays in the vault.
				</p>
				<Button icon="add" onclick={() => open(null)}>New MCP server</Button>
			</Card>
		{/each}
	</div>
</div>

<McpConnectionDialog bind:open={dialogOpen} connection={editing} onsaved={saved} />
