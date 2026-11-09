<script lang="ts">
	import { page } from '$app/state';
	import type { SsoProvider } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon, IconButton, Switch, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import SsoProviderDialog from '$lib/components/SsoProviderDialog.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let providers = $state<SsoProvider[]>(data.providers);
	// svelte-ignore state_referenced_locally
	let passwordLogin = $state(data.settings.password_login);
	let dialogOpen = $state(false);
	let editing = $state<SsoProvider | null>(null);

	function saved(p: SsoProvider) {
		providers = [...providers.filter((x) => x.id !== p.id), p].sort((a, b) => a.name.localeCompare(b.name));
	}
	async function setPasswordLogin(on: boolean) {
		try {
			passwordLogin = (await api().settings.update({ password_login: on })).password_login;
			snackbar.show(on ? 'Password sign-in is on' : 'Password sign-in is off: single sign-on only');
		} catch (err) {
			passwordLogin = !on;
			snackbar.error(err instanceof ApiError ? err.message.replace(/^password_login: /, '') : 'Could not change it');
		}
	}
	const roleLabel = (r: string) => ({ viewer: 'viewers', operator: 'operators', admin: 'admins' })[r] ?? r;
</script>

<svelte:head><title>Sign-on · Settings · Castor</title></svelte:head>

<TopAppBar title="Sign-on" subtitle="Single sign-on with OpenID Connect, and password sign-in">
	{#snippet actions()}
		<Button icon="add" onclick={() => { editing = null; dialogOpen = true; }}>Add provider</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-6 pt-2">
		<section class="flex flex-col gap-2">
			<h2 class="title-medium text-on-surface">Providers</h2>
			{#each providers as p (p.id)}
				<Card variant="outlined" class="flex items-center gap-3 p-3 {p.enabled ? '' : 'opacity-60'}">
					<span class="flex size-9 shrink-0 items-center justify-center rounded-md bg-secondary-container text-on-secondary-container"><Icon name="key" size={20} /></span>
					<button type="button" class="min-w-0 flex-1 text-left" onclick={() => { editing = p; dialogOpen = true; }}>
						<span class="block truncate title-small text-on-surface">{p.name}</span>
						<span class="block truncate body-small text-on-surface-variant">
							{p.issuer} · new accounts are {roleLabel(p.default_role)}{p.role_map.length ? `, roles from ${p.role_map.length} group${p.role_map.length === 1 ? '' : 's'}` : ''} · {p.users} signed in{p.enabled ? '' : ' · off'}
						</span>
					</button>
					<IconButton icon="edit" label="Edit {p.name}" onclick={() => { editing = p; dialogOpen = true; }} />
				</Card>
			{:else}
				<p class="body-medium text-on-surface-variant">
					No providers yet. Add one to let people sign in with Okta, Entra ID, Google, Keycloak, Authentik, GitLab or any OpenID Connect provider.
				</p>
			{/each}
		</section>

		<section class="flex flex-col gap-2">
			<h2 class="title-medium text-on-surface">Password sign-in</h2>
			<Card variant="outlined" class="flex flex-col gap-2 p-4">
				<Switch checked={passwordLogin} label="People can sign in with a username and password" onchange={(on) => void setPasswordLogin(on)} />
				<p class="body-small text-on-surface-variant">
					Turn it off to allow single sign-on only. You can do that once a provider is on and you have signed in through it yourself, so nobody gets locked out. From the
					server's shell, <code class="font-mono">castor-admin password-login on</code> turns it back on.
				</p>
			</Card>
		</section>
	</div>
</div>

<SsoProviderDialog bind:open={dialogOpen} provider={editing} origin={page.url.origin} onsaved={saved} onremoved={(id) => (providers = providers.filter((p) => p.id !== id))} />
