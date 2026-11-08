<script lang="ts">
	import { Button, Card, Chip, Icon } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Ready-to-paste setup for AI assistants that speak MCP, pointed at this server. */
	let origin = $derived(typeof location === 'undefined' ? '' : location.origin);
	let url = $derived(`${origin}/api/mcp`);
	let client = $state('claude-code');
	let copied = $state(false);

	const TOKEN = '<your token>';
	let clients = $derived([
		{ id: 'claude-code', label: 'Claude Code', where: 'Run in a terminal:', text: `claude mcp add --transport http castor ${url} \\\n  --header "Authorization: Bearer ${TOKEN}"` },
		{
			id: 'claude-desktop',
			label: 'Claude Desktop',
			where: 'In claude_desktop_config.json, after pip install ./agent from the Castor repository (use the full path to castor-mcp if Claude can\'t find it):',
			text: JSON.stringify(
				{ mcpServers: { castor: { command: 'castor-mcp', args: ['--server', origin], env: { CASTOR_TOKEN: TOKEN } } } },
				null,
				2
			)
		},
		{
			id: 'vscode',
			label: 'VS Code',
			where: 'In .vscode/mcp.json (it asks for the token once):',
			text: JSON.stringify(
				{
					inputs: [{ type: 'promptString', id: 'castor-token', description: 'Castor API token', password: true }],
					servers: { castor: { type: 'http', url, headers: { Authorization: 'Bearer ${input:castor-token}' } } }
				},
				null,
				2
			)
		},
		{
			id: 'gemini',
			label: 'Gemini CLI',
			where: 'In ~/.gemini/settings.json:',
			text: JSON.stringify({ mcpServers: { castor: { httpUrl: url, headers: { Authorization: `Bearer ${TOKEN}` } } } }, null, 2)
		}
	]);
	let shown = $derived(clients.find((c) => c.id === client) ?? clients[0]);

	async function copy() {
		try {
			await navigator.clipboard.writeText(shown.text);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			snackbar.error('Copying needs clipboard access; select the text instead');
		}
	}
</script>

<Card variant="outlined" class="flex flex-col gap-3 p-4">
	<div class="flex items-center gap-3">
		<span class="flex size-10 shrink-0 items-center justify-center rounded-md bg-tertiary-container text-on-tertiary-container"><Icon name="smart_toy" /></span>
		<div class="min-w-0 flex-1">
			<h2 class="title-small text-on-surface">Connect an AI assistant</h2>
			<p class="body-small text-on-surface-variant">
				Castor is an MCP server at <code class="font-mono">{url}</code>. The assistant gets the tools your token's scopes allow: read-only tokens can only look.
			</p>
		</div>
	</div>
	<div class="flex flex-wrap gap-1.5">
		{#each clients as c (c.id)}
			<Chip variant="filter" selected={client === c.id} onclick={() => (client = c.id)}>{c.label}</Chip>
		{/each}
	</div>
	<div>
		<div class="mb-1 flex items-center justify-between gap-2">
			<span class="body-small text-on-surface-variant">{shown.where}</span>
			<Button variant="text" icon={copied ? 'check' : 'content_copy'} onclick={copy}>{copied ? 'Copied' : 'Copy'}</Button>
		</div>
		<pre class="overflow-auto rounded-sm bg-surface-container-highest p-3 font-mono text-[12.5px] text-on-surface">{shown.text}</pre>
		<p class="mt-1.5 body-small text-on-surface-variant">Replace <code class="font-mono">{TOKEN}</code> with a token from this page. Job output reaches the assistant marked as untrusted data.</p>
	</div>
</Card>
