<script lang="ts">
	import type { Agent, McpConnection, McpConnectionIn, ToolDefinition, VariableSet } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Select, Switch, TextField } from '$lib/m3';
	import { selectorFrom, selectorOptions, selectorValue } from '$lib/agentSelector';
	import AgentProjectsPicker from './AgentProjectsPicker.svelte';

	/** Add or change an MCP server Castor calls tools on (docs/ACTIONS.md phase 3). */
	interface Props {
		open: boolean;
		connection: McpConnection | null;
		onsaved: (c: McpConnection) => void;
	}
	let { open = $bindable(false), connection, onsaved }: Props = $props();

	let id = $state('');
	let name = $state('');
	let description = $state('');
	let transport = $state<'http' | 'sse' | 'stdio'>('http');
	let remote = $derived(transport !== 'stdio');
	let url = $state('');
	let headers = $state('');
	let env = $state('');
	let setId = $state('');
	let command = $state('');
	let runWith = $state(''); // 'tool:<id>' | 'image' | '' (host)
	let image = $state('');
	let agentValue = $state('');
	let idle = $state('300');
	let projectIds = $state<string[] | null>(null);
	let enabled = $state(true);
	let error = $state<string | null>(null);
	let saving = $state(false);

	let sets = $state<VariableSet[]>([]);
	let tools = $state<ToolDefinition[]>([]);
	let agents = $state<Agent[]>([]);
	let loadedFor: string | null | undefined = undefined;

	/** "Name: value" per line (a value can hold {{ field }} from the variable set). */
	const toLines = (o: Record<string, string>) => Object.entries(o ?? {}).map(([k, v]) => `${k}: ${v}`).join('\n');
	function fromLines(text: string, sep: ':' | '='): Record<string, string> {
		const out: Record<string, string> = {};
		for (const line of text.split('\n')) {
			const i = line.indexOf(sep);
			if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
		}
		return out;
	}
	/** Words, with "quoted parts" kept together. */
	function split(text: string): string[] {
		return [...text.matchAll(/"([^"]*)"|'([^']*)'|(\S+)/g)].map((m) => m[1] ?? m[2] ?? m[3]);
	}
	const join = (argv: string[]) => argv.map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(' ');

	$effect(() => {
		if (!open) return;
		if (loadedFor === (connection?.id ?? null)) return;
		loadedFor = connection?.id ?? null;
		const c = connection;
		id = c?.id ?? '';
		name = c?.name ?? '';
		description = c?.description ?? '';
		transport = c?.transport ?? 'http';
		url = c?.url ?? '';
		headers = toLines(c?.headers ?? { Authorization: 'Bearer {{ token }}' });
		env = Object.entries(c?.env ?? {}).map(([k, v]) => `${k}=${v}`).join('\n');
		setId = c?.variable_set_id ?? '';
		command = join(c?.command ?? []);
		runWith = c?.tool ? `tool:${c.tool}` : c?.image ? 'image' : '';
		image = c?.image ?? '';
		agentValue = selectorValue(c?.agent_selector);
		idle = String(c?.idle_s ?? 300);
		projectIds = c?.project_ids ?? null;
		enabled = c?.enabled ?? true;
		error = null;
		void Promise.all([api().variables.sets(), api().tools.list(), api().agents.list()]).then(([s, t, a]) => {
			sets = s.filter((x) => !x.ephemeral);
			tools = t;
			agents = a;
		});
	});
	$effect(() => {
		if (!open) loadedFor = undefined;
	});

	let mcpAgents = $derived(agents.filter((a) => a.capabilities?.mcp_stdio));

	async function save() {
		saving = true;
		error = null;
		const body: McpConnectionIn = {
			id: id.trim(),
			name: name.trim(),
			description,
			transport,
			url: remote ? url.trim() : '',
			headers: remote ? fromLines(headers, ':') : {},
			env: transport === 'stdio' ? fromLines(env, '=') : {},
			variable_set_id: setId || null,
			command: transport === 'stdio' ? split(command) : [],
			tool: transport === 'stdio' && runWith.startsWith('tool:') ? runWith.slice(5) : null,
			image: transport === 'stdio' && runWith === 'image' ? image.trim() || null : null,
			agent_selector: transport === 'stdio' ? selectorFrom(agentValue) : {},
			idle_s: Number(idle) || 300,
			project_ids: projectIds,
			enabled
		};
		try {
			const saved = connection ? await api().mcp.update(connection.id, body) : await api().mcp.create(body);
			// Read its tools straight away, so it's ready to make actions from.
			onsaved(await api().mcp.check(saved.id).catch(() => saved));
			open = false;
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}
</script>

<Dialog bind:open title={connection ? connection.name : 'New MCP server'} icon="hub" size="md">
	<div class="flex flex-col gap-4">
		<div class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,12rem)]">
			<TextField label="Name" bind:value={name} required placeholder="Shodan" />
			<TextField label="Id" bind:value={id} required mono readonly={!!connection} placeholder="shodan" supporting={connection ? 'Definitions name it' : 'Definitions name it in mcp.server'} />
		</div>
		<TextField label="Description" bind:value={description} placeholder="What it's for" />
		<Select
			label="How Castor reaches it"
			bind:value={transport}
			options={[
				{ value: 'http', label: 'Over HTTP, from the Castor server', description: 'A remote MCP server (Streamable HTTP)' },
				{ value: 'sse', label: 'Over HTTP with SSE (older servers)', description: 'A remote MCP server that still uses the 2024-11-05 HTTP+SSE transport' },
				{ value: 'stdio', label: 'A program an agent runs', description: 'An MCP server that speaks stdio (npx, uvx, a container image)' }
			]}
		/>
		<Select
			label="Variable set (optional)"
			bind:value={setId}
			options={[{ value: '', label: 'None' }, ...sets.map((s) => ({ value: s.id, label: s.name, description: s.kind_id }))]}
			supporting={"Where its token or keys come from: {{ field }} below is filled from this set, at the moment it's used"}
		/>
		{#if remote}
			<TextField label="Address" bind:value={url} required mono placeholder="https://mcp.example.com/mcp" />
			<TextField label="Headers" bind:value={headers} multiline rows={2} mono placeholder="Authorization: Bearer {'{{ token }}'}" supporting="One “Name: value” per line" />
		{:else}
			<TextField label="Command" bind:value={command} mono placeholder="npx -y @modelcontextprotocol/server-github" supporting="With a tool or image: the arguments after its entrypoint (empty: its own default)" />
			<div class="grid gap-3 sm:grid-cols-2">
				<Select
					label="Runs in"
					bind:value={runWith}
					options={[
						{ value: '', label: 'The agent’s host', description: 'Needs an agent started with --allow-host' },
						{ value: 'image', label: 'A container image' },
						...tools.map((t) => ({ value: `tool:${t.id}`, label: `The ${t.name} tool`, group: 'Tool library' }))
					]}
				/>
				{#if runWith === 'image'}
					<TextField label="Image" bind:value={image} mono placeholder="docker.io/mcp/github:latest" />
				{:else}
					<TextField label="Stops after idle (s)" bind:value={idle} type="number" />
				{/if}
			</div>
			<TextField label="Environment" bind:value={env} multiline rows={2} mono placeholder="GITHUB_TOKEN={'{{ token }}'}" supporting="One NAME=value per line; containers get them by name, never on the command line" />
			<Select
				label="Agent"
				bind:value={agentValue}
				options={selectorOptions(mcpAgents, selectorFrom(agentValue), false)}
				supporting={mcpAgents.length ? 'Which agent runs the program (one that’s connected and up to date)' : 'No agent can run MCP servers yet: they need Castor 1.3’s agent'}
			/>
		{/if}
		<AgentProjectsPicker
			bind:value={projectIds}
			limited={transport === 'stdio'}
			supporting={transport === 'stdio'
				? 'Only these projects\' cards and playbooks can call its tools, and each project gets its own running program, so one never sees another\'s calls.'
				: 'Limit it to some projects: only their cards and playbooks can call its tools.'}
		/>
		<Switch bind:checked={enabled} label="On" />
		{#if error}<p class="body-small text-error">{error}</p>{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="check" loading={saving} onclick={save} disabled={!id.trim() || !name.trim()}>Save and connect</Button>
	{/snippet}
</Dialog>
