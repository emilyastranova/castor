<script lang="ts">
	import type { AiDraft } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, TextField } from '$lib/m3';

	/** Describe a command, tool, playbook or variable kind; the AI drafts it, Castor checks it
	 *  like saving would (the AI fixes what fails), and "Use this" hands it to the editor. */
	interface Props {
		open: boolean;
		kind: 'command' | 'tool' | 'playbook' | 'variable_kind';
		/** The definition being edited: the AI changes it instead of starting over. */
		current?: string;
		onuse: (draft: AiDraft) => void;
	}
	let { open = $bindable(false), kind, current = '', onuse }: Props = $props();

	const NOUN = { command: 'command', tool: 'tool', playbook: 'playbook', variable_kind: 'variable kind' };
	const EXAMPLES = {
		command: 'Run testssl.sh against a host:port and save the JSON report as an output',
		tool: 'testssl.sh from its official container image',
		playbook: 'Ping-sweep a range, scan open web ports on live hosts, then run a directory brute force on each web server',
		variable_kind: 'An AWS access key: access key id, secret key, optional session token and region'
	};
	let prompt = $state('');
	let edit = $state(true);
	let busy = $state(false);
	let draft = $state<AiDraft | null>(null);
	let error = $state<string | null>(null);
	$effect(() => {
		if (!open) return;
		draft = null;
		error = null;
		edit = !!current.trim();
	});

	async function run() {
		busy = true;
		error = null;
		try {
			draft = await api().ai.generate(kind, prompt, edit ? current : '');
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'The AI could not answer';
		} finally {
			busy = false;
		}
	}
</script>

<Dialog bind:open title="Generate a {NOUN[kind]} with AI" icon="auto_awesome" size="md">
	<div class="flex flex-col gap-3 pt-2 pb-2">
		<TextField label="What should it do?" bind:value={prompt} multiline rows={3} placeholder={EXAMPLES[kind]} onkeydown={(e) => (e.ctrlKey || e.metaKey) && e.key === 'Enter' && prompt.trim().length > 2 && run()} />
		{#if current.trim()}
			<label class="flex items-center gap-2 body-small text-on-surface-variant"><input type="checkbox" bind:checked={edit} class="accent-[var(--md-sys-color-primary)]" />Change the one in the editor rather than starting over</label>
		{/if}
		{#if draft}
			{#if draft.problems.length}
				<div class="rounded-md bg-error-container/50 px-3 py-2 body-small text-on-error-container">
					<p class="font-medium">It still doesn't validate; fix it in the editor:</p>
					<ul class="list-disc pl-5">{#each draft.problems as p, i (i)}<li class="break-words">{p}</li>{/each}</ul>
				</div>
			{:else}
				<p class="flex items-center gap-1.5 body-small text-primary"><Icon name="check_circle" size={16} />Checks out, like saving it would.</p>
			{/if}
			<pre class="max-h-80 overflow-auto rounded-md bg-surface-container-highest p-3 font-mono text-[12px] text-on-surface">{draft.yaml}</pre>
			{#if draft.notes}<p class="body-small text-on-surface-variant">{draft.notes}</p>{/if}
			<p class="body-small text-on-surface-variant">By {draft.model}. Nothing is saved until you save it.</p>
		{/if}
		{#if error}<p class="body-small text-error">{error}</p>{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button variant={draft ? 'tonal' : 'filled'} icon="auto_awesome" loading={busy} disabled={prompt.trim().length < 3} onclick={run}>{draft ? 'Try again' : 'Generate'}</Button>
		{#if draft}<Button icon="check" onclick={() => { onuse(draft!); open = false; }}>Use this</Button>{/if}
	{/snippet}
</Dialog>
