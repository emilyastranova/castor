<script lang="ts">
	import type { SsoProvider, SsoProviderIn, UserRole } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, IconButton, Select, Switch, TagInput, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';

	/** Add or edit an OpenID Connect provider. Shows the redirect URI to register at the
	 *  provider, and checks the issuer before saving. */
	interface Props {
		open: boolean;
		/** The provider to edit; null to add one. */
		provider?: SsoProvider | null;
		/** Where callbacks land (for a new provider's redirect URI). */
		origin: string;
		onsaved?: (p: SsoProvider) => void;
		onremoved?: (id: string) => void;
	}
	let { open = $bindable(false), provider = null, origin, onsaved, onremoved }: Props = $props();

	const ROLES: { value: UserRole; label: string }[] = [
		{ value: 'viewer', label: 'Viewer: read-only' },
		{ value: 'operator', label: 'Operator: works in their projects' },
		{ value: 'admin', label: 'Admin: everything' }
	];

	let form = $state<SsoProviderIn>(blank());
	let secret = $state('');
	let saving = $state(false);
	let testing = $state(false);
	let tested = $state<{ ok: boolean; text: string } | null>(null);
	let copied = $state(false);
	let openedFor = false;

	function blank(): SsoProviderIn {
		return {
			id: '', name: '', issuer: '', client_id: '', scopes: 'openid email profile', enabled: true, default_role: 'viewer',
			allowed_domains: [], allowed_groups: [], groups_claim: 'groups', role_map: [], link_by_email: false
		};
	}
	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (!open) return;
		const p = provider;
		form = p
			? { id: p.id, name: p.name, issuer: p.issuer, client_id: p.client_id, scopes: p.scopes, enabled: p.enabled, default_role: p.default_role,
				allowed_domains: [...p.allowed_domains], allowed_groups: [...p.allowed_groups], groups_claim: p.groups_claim,
				role_map: p.role_map.map((r) => ({ ...r })), link_by_email: p.link_by_email }
			: blank();
		secret = '';
		tested = null;
		copied = false;
	});

	// A new provider's id follows its name until edited.
	let idTouched = $state(false);
	function nameChanged() {
		if (!provider && !idTouched) form.id = form.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32);
	}
	let redirect = $derived(provider?.redirect_uri ?? `${origin}/api/auth/sso/${form.id || '<id>'}/callback`);

	async function test() {
		testing = true;
		try {
			const r = await api().sso.test(form.issuer);
			tested = r.ok ? { ok: true, text: `Found ${r.issuer}; sign-in goes to ${r.authorization_endpoint}` } : { ok: false, text: r.error ?? 'Not found' };
		} catch (err) {
			tested = { ok: false, text: err instanceof ApiError ? err.message : 'Could not check' };
		} finally {
			testing = false;
		}
	}
	async function copy() {
		try {
			await navigator.clipboard.writeText(redirect);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			snackbar.error('Copying needs clipboard access; select the text instead');
		}
	}
	async function save() {
		saving = true;
		try {
			const body = { ...form, role_map: form.role_map.filter((r) => r.group.trim()), ...(secret.trim() ? { client_secret: secret.trim() } : {}) };
			const saved = provider ? await api().sso.update(provider.id, body) : await api().sso.create(body);
			onsaved?.(saved);
			snackbar.show(provider ? `Saved ${saved.name}` : `Added ${saved.name}. Register the redirect URI at the provider if you haven't.`);
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save the provider');
		} finally {
			saving = false;
		}
	}
	async function remove() {
		if (!provider) return;
		const ok = await confirmAction({
			title: `Remove ${provider.name}?`,
			message: `Nobody can sign in with it any more. The ${provider.users} account${provider.users === 1 ? '' : 's'} made or linked through it stay; give them a password (Settings → Users) or another way to sign in.`,
			confirmLabel: 'Remove provider'
		});
		if (!ok) return;
		try {
			await api().sso.remove(provider.id);
			onremoved?.(provider.id);
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove the provider');
		}
	}
	let canSave = $derived(!!form.name.trim() && !!form.id && !!form.issuer.trim() && !!form.client_id.trim() && !saving);
</script>

<Dialog bind:open title={provider ? `Edit ${provider.name}` : 'Add a sign-on provider'} icon="key" size="lg">
	<div class="flex flex-col gap-4 pt-1 pb-2">
		<div class="grid gap-4 sm:grid-cols-[1fr_12rem]">
			<TextField label="Name" bind:value={form.name} placeholder="Okta, Entra ID, Keycloak…" required supporting="On the sign-in button: Continue with …" oninput={nameChanged} />
			<TextField label="Id" bind:value={form.id} mono required readonly={!!provider} supporting="In the redirect URI" oninput={() => (idTouched = true)} />
		</div>

		<div class="flex flex-col gap-2">
			<div class="flex items-start gap-2">
				<TextField label="Issuer" bind:value={form.issuer} mono required placeholder="https://login.example.com/realms/main" class="flex-1"
					supporting="The provider's issuer URL; Castor reads its /.well-known/openid-configuration." oninput={() => (tested = null)} />
				<Button variant="tonal" class="mt-2" loading={testing} disabled={!form.issuer.trim()} onclick={test}>Test</Button>
			</div>
			{#if tested}
				<p class="flex items-start gap-1.5 body-small {tested.ok ? 'text-on-surface-variant' : 'text-error'}">
					<Icon name={tested.ok ? 'check_circle' : 'error'} size={16} class="mt-px shrink-0 {tested.ok ? 'text-primary' : ''}" />{tested.text}
				</p>
			{/if}
		</div>

		<div class="grid gap-4 sm:grid-cols-2">
			<TextField label="Client ID" bind:value={form.client_id} mono required autocomplete="off" />
			<TextField label="Client secret" type="password" bind:value={secret} autocomplete="new-password"
				placeholder={provider?.has_secret ? '•••••••• (kept)' : ''}
				supporting={provider?.has_secret ? 'Leave empty to keep the stored one.' : 'Empty for a public client (PKCE only). Kept in the vault.'} />
		</div>

		<div class="rounded-md bg-surface-container px-3 py-2.5">
			<div class="flex items-center gap-2">
				<span class="label-large text-on-surface">Redirect URI</span>
				<span class="flex-1"></span>
				<Button variant="text" icon={copied ? 'check' : 'content_copy'} onclick={copy}>{copied ? 'Copied' : 'Copy'}</Button>
			</div>
			<code class="block font-mono text-[12.5px] break-all text-on-surface select-all">{redirect}</code>
			<p class="mt-1 body-small text-on-surface-variant">Register this at the provider as the app's redirect (callback) URI. It uses CASTOR_PUBLIC_URL when that's set.</p>
		</div>

		<h3 class="mt-1 title-small text-on-surface">Who can sign in, and as what</h3>
		<Select label="Role for new accounts" bind:value={form.default_role} options={ROLES} supporting="Accounts are made the first time someone signs in." />
		<div class="grid gap-4 sm:grid-cols-2">
			<TagInput label="Only these email domains" bind:value={form.allowed_domains} placeholder="corp.example" supporting="Empty: any. The provider must vouch for the email." />
			<TagInput label="Only these groups" bind:value={form.allowed_groups} placeholder="castor-users" supporting="Empty: anyone the provider signs in." />
		</div>

		<div class="flex flex-col gap-2">
			<div class="flex items-center gap-2">
				<span class="flex-1 label-large text-on-surface">Groups to roles</span>
				<Button variant="text" icon="add" onclick={() => (form.role_map = [...form.role_map, { group: '', role: 'operator' }])}>Add</Button>
			</div>
			<p class="-mt-1 body-small text-on-surface-variant">
				With any, the role follows the groups at every sign-in: the highest matching role, or the role for new accounts when none match. The last active admin is never demoted.
			</p>
			{#each form.role_map as rule, i (i)}
				<div class="flex items-start gap-2">
					<TextField label="Group" bind:value={rule.group} mono class="flex-1" />
					<Select label="Role" bind:value={rule.role} options={ROLES.map((r) => ({ value: r.value, label: r.value }))} class="w-40" />
					<IconButton icon="delete" label="Remove" class="mt-3" onclick={() => (form.role_map = form.role_map.filter((_, j) => j !== i))} />
				</div>
			{/each}
		</div>

		<div class="grid gap-4 sm:grid-cols-2">
			<TextField label="Groups claim" bind:value={form.groups_claim} mono supporting="Where the provider puts groups in the token (or UserInfo)." />
			<TextField label="Scopes" bind:value={form.scopes} mono supporting="Must include openid; add groups if your provider needs it." />
		</div>
		<Switch bind:checked={form.link_by_email} label="Link to an existing account with the same email, when the provider says it's verified" />
		<Switch bind:checked={form.enabled} label="Offer it on the sign-in page" />
	</div>
	{#snippet actions()}
		{#if provider}<Button variant="text" class="mr-auto text-error" onclick={remove}>Remove</Button>{/if}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon={provider ? 'save' : 'add'} loading={saving} disabled={!canSave} onclick={save}>{provider ? 'Save' : 'Add provider'}</Button>
	{/snippet}
</Dialog>
