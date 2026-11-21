<script lang="ts">
	import { brand } from '$lib/brand.svelte';
	import { page } from '$app/state';
	import type { SsoProvider } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon, IconButton, Select, Switch, TextField, TopAppBar } from '$lib/m3';
	import SsoButton from '$lib/components/SsoButton.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import SsoProviderDialog from '$lib/components/SsoProviderDialog.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let providers = $state<SsoProvider[]>(data.providers);
	// svelte-ignore state_referenced_locally
	let passwordLogin = $state(data.settings.password_login);
	// svelte-ignore state_referenced_locally
	let redirect = $state(data.settings.sso_redirect ?? '');
	// svelte-ignore state_referenced_locally
	let message = $state(data.settings.sign_in_message ?? '');
	// svelte-ignore state_referenced_locally
	let collapsed = $state(data.settings.password_form === 'collapsed');
	// svelte-ignore state_referenced_locally
	providers.sort((a, b) => a.position - b.position || a.name.localeCompare(b.name));

	async function setting(body: Record<string, unknown>, done: string) {
		try {
			const s = await api().settings.update(body);
			redirect = s.sso_redirect;
			message = s.sign_in_message;
			collapsed = s.password_form === 'collapsed';
			snackbar.show(done);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not change it');
		}
	}
	async function move(i: number, delta: number) {
		const j = i + delta;
		if (j < 0 || j >= providers.length) return;
		const next = [...providers];
		[next[i], next[j]] = [next[j], next[i]];
		providers = next.map((p, k) => ({ ...p, position: k }));
		try {
			await api().sso.order(providers.map((p) => p.id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not change the order');
		}
	}
	let dialogOpen = $state(false);
	let editing = $state<SsoProvider | null>(null);

	function saved(p: SsoProvider) {
		const at = providers.findIndex((x) => x.id === p.id);
		providers = at >= 0 ? providers.map((x) => (x.id === p.id ? p : x)) : [...providers, p];
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

<svelte:head><title>Sign-on · Settings · {brand.name}</title></svelte:head>

<TopAppBar title="Sign-on" subtitle="Single sign-on with OpenID Connect or SAML, and password sign-in">
	{#snippet actions()}
		<Button icon="add" onclick={() => { editing = null; dialogOpen = true; }}>Add provider</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-6 pt-2">
		<section class="flex flex-col gap-2">
			<h2 class="title-medium text-on-surface">Providers</h2>
			{#if providers.length > 1}<p class="-mt-1 body-small text-on-surface-variant">In the order the sign-in page shows them.</p>{/if}
			{#each providers as p, i (p.id)}
				<Card variant="outlined" class="flex items-center gap-3 p-3 {p.enabled ? '' : 'opacity-60'}">
					<div class="hidden w-56 shrink-0 sm:block"><SsoButton name={p.name} branding={p.branding} logoUrl={p.branding.logo_url} logoDarkUrl={p.branding.logo_dark_url} compact /></div>
					<button type="button" class="min-w-0 flex-1 text-left" onclick={() => { editing = p; dialogOpen = true; }}>
						<span class="block truncate title-small text-on-surface">{p.name}</span>
						<span class="block truncate body-small text-on-surface-variant">
							{p.protocol === 'saml' ? 'SAML · ' : ''}{p.issuer} · new accounts are {roleLabel(p.default_role)}{p.role_map.length ? `, roles from ${p.role_map.length} group${p.role_map.length === 1 ? '' : 's'}` : ''} · {p.users} signed in{p.enabled ? '' : ' · off'}
						</span>
					</button>
					{#if providers.length > 1}
						<IconButton icon="arrow_upward" label="Move {p.name} up" disabled={i === 0} onclick={() => move(i, -1)} />
						<IconButton icon="arrow_downward" label="Move {p.name} down" disabled={i === providers.length - 1} onclick={() => move(i, 1)} />
					{/if}
					<IconButton icon="edit" label="Edit {p.name}" onclick={() => { editing = p; dialogOpen = true; }} />
				</Card>
			{:else}
				<p class="body-medium text-on-surface-variant">
					No providers yet. Add one to let people sign in with Okta, Entra ID, Google, Keycloak, Authentik, GitLab, ADFS or any OpenID Connect or SAML 2.0 provider.
				</p>
			{/each}
		</section>

		<section class="flex flex-col gap-2">
			<h2 class="title-medium text-on-surface">The sign-in page</h2>
			<Card variant="outlined" class="flex flex-col gap-4 p-4">
				<TextField label="Message (optional)" bind:value={message} multiline rows={2} placeholder="Sign in with your Acme account. Trouble? Ask the service desk."
					supporting="Over the buttons, for everyone who signs in"
					onblur={() => message !== (data.settings.sign_in_message ?? '') && setting({ sign_in_message: message.trim() }, 'Saved the message')} />
				<div class="flex flex-col gap-1">
					<Select label="Go straight to" value={redirect}
						options={[{ value: '', label: 'Nowhere: show the sign-in page' }, ...providers.filter((p) => p.enabled).map((p) => ({ value: p.id, label: p.name }))]}
						onchange={(v) => setting({ sso_redirect: v }, v ? `Signing in goes straight to ${providers.find((p) => p.id === v)?.name}` : 'The sign-in page shows again')} />
					<p class="px-1 body-small text-on-surface-variant">
						People are sent to the provider without seeing the sign-in page. It still shows after someone signs out, when sign-on fails, and at
						<code class="font-mono">/login?local</code>, so there’s always a way back to other providers and passwords.
					</p>
				</div>
				{#if passwordLogin && providers.some((p) => p.enabled)}
					<div class="flex flex-col gap-1">
						<Switch checked={collapsed} label="Tuck the password form behind “Sign in with a password”"
							onchange={(on) => setting({ password_form: on ? 'collapsed' : 'shown' }, on ? 'The password form is behind a link' : 'The password form shows')} />
						<p class="body-small text-on-surface-variant">The buttons come first; people who need a password can still open the form.</p>
					</div>
				{/if}
			</Card>
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
