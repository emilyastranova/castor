<script lang="ts">
	import type { AgentToken } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Enroll an agent (create a token), or show a token just made by rotating one. The
	 *  token is only ever shown here, once. */
	interface Props {
		open: boolean;
		/** WebSocket URL agents connect to. */
		wsUrl: string;
		/** Set to show a token that already exists (after Rotate) instead of creating one. */
		revealed?: (AgentToken & { token: string }) | null;
		sharedSecretAllowed?: boolean;
		oncreated?: (token: AgentToken) => void;
	}
	let { open = $bindable(false), wsUrl, revealed = null, sharedSecretAllowed = false, oncreated }: Props = $props();

	let label = $state('');
	let tagsText = $state('');
	let creating = $state(false);
	let created = $state<(AgentToken & { token: string }) | null>(null);
	let copied = $state<string | null>(null);
	let openedFor = false;

	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (open) {
			label = '';
			tagsText = '';
			created = revealed;
			copied = null;
		}
	});

	let shown = $derived(created ?? revealed);
	let name = $derived(shown?.agent_name || shown?.label || label || '<agent-name>');
	let command = $derived(
		shown
			? `castor-agent --server ${wsUrl} \\\n  --token ${shown.token} \\\n  --name ${name.includes(' ') ? `'${name}'` : name}${shown.tags.length && !shown.agent_id ? ` --tags ${shown.tags.join(',')}` : ''}`
			: ''
	);

	async function create() {
		creating = true;
		try {
			created = await api().agentTokens.create({
				label: label.trim(),
				tags: tagsText.split(',').map((t) => t.trim()).filter(Boolean)
			});
			oncreated?.(created);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not create the token');
		} finally {
			creating = false;
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
</script>

<Dialog bind:open title={revealed ? `New token for ${revealed.agent_name ?? revealed.label}` : 'Enroll an agent'} icon="key" size="md">
	<div class="flex flex-col gap-4 pb-2">
		{#if !shown}
			<p class="body-medium text-on-surface">
				Each agent gets its own token, so it can be revoked on its own. The first agent that connects with this token becomes its agent.
			</p>
			<TextField label="Agent name" bind:value={label} placeholder="e.g. kali-lab-01" supporting="Used in the command below and to recognise the token. The agent's --name decides its name." />
			<TextField label="Tags (optional)" bind:value={tagsText} supporting="Comma separated; given to the agent when it enrolls." />
		{:else}
			<div class="flex items-start gap-2 rounded-md bg-tertiary-container/50 px-3 py-2 body-small text-on-surface">
				<Icon name="warning" size={18} class="mt-px shrink-0 text-tertiary" />
				<span>Copy this token now: it's shown only once. Castor keeps only a hash of it.{revealed ? ' The old token no longer works; restart the agent with this one.' : ''}</span>
			</div>
			<div>
				<div class="mb-1 flex items-center justify-between">
					<span class="label-large text-on-surface">Token</span>
					<Button variant="text" icon={copied === 'token' ? 'check' : 'content_copy'} onclick={() => copy(shown!.token, 'token')}>{copied === 'token' ? 'Copied' : 'Copy'}</Button>
				</div>
				<code class="block rounded-sm bg-surface-container-highest px-3 py-2 font-mono text-[12.5px] break-all text-on-surface select-all">{shown.token}</code>
			</div>
			<div>
				<div class="mb-1 flex items-center justify-between">
					<span class="label-large text-on-surface">Run the agent</span>
					<Button variant="text" icon={copied === 'command' ? 'check' : 'content_copy'} onclick={() => copy(command, 'command')}>{copied === 'command' ? 'Copied' : 'Copy'}</Button>
				</div>
				<pre class="overflow-auto rounded-sm bg-surface-container-highest p-3 font-mono text-[12.5px] text-on-surface">{command}</pre>
				<p class="mt-1.5 body-small text-on-surface-variant">
					Install it with <code class="font-mono">pip install ./agent</code> from the Castor repository. Or set <code class="font-mono">CASTOR_AGENT_TOKEN</code> instead of
					<code class="font-mono">--token</code> (for the compose agent, in <code class="font-mono">.env</code>). Use <code class="font-mono">wss://</code> when the agent connects over a network.
				</p>
			</div>
		{/if}
		{#if !shown && sharedSecretAllowed}
			<p class="body-small text-on-surface-variant">
				Agents can also still connect with the shared <code class="font-mono">CASTOR_AGENT_SECRET</code>. Set
				<code class="font-mono">CASTOR_ALLOW_SHARED_AGENT_SECRET=false</code> on the server once every agent has a token.
			</p>
		{/if}
	</div>
	{#snippet actions()}
		{#if shown}
			<Button onclick={() => (open = false)}>Done</Button>
		{:else}
			<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
			<Button icon="key" loading={creating} onclick={create}>Create token</Button>
		{/if}
	{/snippet}
</Dialog>
