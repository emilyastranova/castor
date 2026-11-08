<script lang="ts">
	import type { ApiToken } from '$lib/api';
	import { api } from '$lib/api';
	import { Button, TopAppBar } from '$lib/m3';
	import ApiTokenList from '$lib/components/ApiTokenList.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let tokens = $state<ApiToken[]>(data.tokens);
	let live = $derived(tokens.filter((t) => !t.revoked_at && !(t.expires_at && new Date(t.expires_at).getTime() <= Date.now())).length);

	async function reload() {
		tokens = await api().apiTokens.list(true);
	}
</script>

<svelte:head><title>API tokens · Settings · Castor</title></svelte:head>

<TopAppBar title="API tokens" subtitle={`Everyone's personal tokens · ${live} working`}>
	{#snippet actions()}
		<Button variant="text" icon="person" href="/tokens">Your tokens</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-4 pt-2">
		<p class="body-medium text-on-surface-variant">
			People make their own tokens from their account menu. You can revoke anyone's here; disabling an account stops all of its tokens too.
		</p>
		<ApiTokenList {tokens} everyone onchange={reload} />
	</div>
</div>
