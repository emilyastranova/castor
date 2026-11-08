<script lang="ts">
	import type { ApiToken } from '$lib/api';
	import { api } from '$lib/api';
	import { Button, TopAppBar } from '$lib/m3';
	import ApiTokenDialog from '$lib/components/ApiTokenDialog.svelte';
	import ApiTokenList from '$lib/components/ApiTokenList.svelte';
	import McpConnect from '$lib/components/McpConnect.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let tokens = $state<ApiToken[]>(data.tokens);
	let dialogOpen = $state(false);

	async function reload() {
		tokens = await api().apiTokens.list();
	}
</script>

<svelte:head><title>API tokens · Castor</title></svelte:head>

<TopAppBar title="API tokens" subtitle="Use Castor from scripts, CI and other tools, as you, with only the access you pick">
	{#snippet actions()}
		<Button variant="text" icon="description" href="/api/docs" external>API reference</Button>
		<Button icon="add" onclick={() => (dialogOpen = true)}>New token</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-4 pt-2">
		<p class="body-medium text-on-surface-variant">
			Send a token as <code class="rounded-sm bg-surface-container-highest px-1.5 py-0.5 font-mono text-[12.5px] text-on-surface">Authorization: Bearer castor_pat_…</code>.
			It acts as you, so your role and projects apply, narrowed to its scopes. Tokens can't make tokens or change your password.
		</p>
		<ApiTokenList {tokens} onchange={reload} oncreate={() => (dialogOpen = true)} />
		<McpConnect />
	</div>
</div>

{#if data.user}<ApiTokenDialog bind:open={dialogOpen} role={data.user.role} oncreated={reload} />{/if}
