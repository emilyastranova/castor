<script lang="ts">
	import type { SamlCertificate, SamlConfig, SsoProtocol, SsoProvider, SsoProviderIn, UserRole } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Chip, Dialog, Icon, IconButton, Select, Switch, Tabs, TagInput, TextField } from '$lib/m3';
	import { absoluteTime, relativeTime } from '$lib/format';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import type { SsoBranding } from '$lib/api';
	import SsoButton from './SsoButton.svelte';

	/** Add or edit a sign-on provider: OpenID Connect (the redirect URI to register, the issuer
	 *  checked before saving) or SAML (the identity provider's metadata read from its URL or
	 *  pasted, and Castor's own to give it). */
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
	let copied = $state<string | null>(null);
	let openedFor = false;

	function blankSaml(): SamlConfig {
		return {
			sso_url: '', certificates: [], metadata_url: '', sp_entity_id: '', name_id_format: '', subject_attribute: '',
			email_attribute: '', name_attribute: '', username_attribute: '', trust_email: true
		};
	}
	function blank(protocol: SsoProtocol = 'oidc'): SsoProviderIn {
		return {
			id: '', name: '', protocol, issuer: '', client_id: '', scopes: 'openid email profile', enabled: true, default_role: 'viewer',
			allowed_domains: [], allowed_groups: [], groups_claim: 'groups', role_map: [], link_by_email: false
		};
	}
	let saml = $state<SamlConfig>(blankSaml());
	const blankBranding = (): SsoBranding => ({ label: '', style: 'theme', color: '', text_color: '', logo: 'symbol', symbol: 'key' });
	let branding = $state<SsoBranding>(blankBranding());
	/** The uploaded logos (saved at once, apart from the rest of the form). */
	let logos = $state<{ light: string | null; dark: string | null }>({ light: null, dark: null });
	let uploading = $state<'light' | 'dark' | null>(null);
	const STYLES = [
		{ value: 'theme', label: 'Castor’s look', icon: 'palette' },
		{ value: 'filled', label: 'Filled with a colour', icon: 'format_color_fill' },
		{ value: 'outlined', label: 'Outlined in a colour', icon: 'border_style' }
	] as const;
	const LOGOS = [
		{ value: 'symbol', label: 'An icon', icon: 'key' },
		{ value: 'icon', label: 'A square logo', icon: 'crop_square' },
		{ value: 'wordmark', label: 'A wordmark', icon: 'title' },
		{ value: 'none', label: 'Words only', icon: 'text_fields' }
	] as const;
	/** WCAG contrast between two #rrggbb colours. */
	function contrast(a: string, b: string): number {
		const lum = (hex: string) => {
			const [r, g, bl] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
			return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
		};
		const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m);
		return (x + 0.05) / (y + 0.05);
	}
	/** Where an outlined colour (or a filled button's words) would be hard to read. */
	const hardToRead = $derived.by(() => {
		const c = branding.color;
		if (!/^#[0-9a-f]{6}$/i.test(c)) return '';
		if (branding.style === 'outlined') {
			const onLight = contrast(c, '#ffffff'), onDark = contrast(c, '#1b1b1f');
			if (onLight < 3 && onDark < 3) return 'Hard to read on light and dark pages alike';
			if (onDark < 3) return 'Hard to read on dark themes: pick a lighter colour, or fill the button with it';
			if (onLight < 3) return 'Hard to read on light themes: pick a darker colour, or fill the button with it';
		}
		if (branding.style === 'filled' && /^#[0-9a-f]{6}$/i.test(branding.text_color) && contrast(c, branding.text_color) < 4.5)
			return 'The words are hard to read on that colour: leave their colour empty to have it worked out';
		return '';
	});
	async function uploadLogo(variant: 'light' | 'dark', input: HTMLInputElement) {
		const file = input.files?.[0];
		input.value = '';
		if (!file || !provider) return;
		uploading = variant;
		try {
			const saved = await api().sso.uploadLogo(provider.id, file, variant);
			logos = { light: saved.branding.logo_url, dark: saved.branding.logo_dark_url };
			onsaved?.(saved);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not upload the logo');
		} finally {
			uploading = null;
		}
	}
	async function removeLogo(variant: 'light' | 'dark') {
		if (!provider) return;
		try {
			const saved = await api().sso.removeLogo(provider.id, variant);
			logos = { light: saved.branding.logo_url, dark: saved.branding.logo_dark_url };
			onsaved?.(saved);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove the logo');
		}
	}
	/** What the certificates are, as the server read them (kept in step with `saml.certificates`). */
	let described = $state<SamlCertificate[]>([]);
	let metadataXml = $state('');
	let pasting = $state(false);
	let loading = $state(false);
	let loaded = $state<{ ok: boolean; text: string } | null>(null);
	let newCert = $state('');
	let addingCert = $state(false);
	let advanced = $state(false);
	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (!open) return;
		const p = provider;
		form = p
			? { id: p.id, name: p.name, protocol: p.protocol, issuer: p.issuer, client_id: p.client_id, scopes: p.scopes, enabled: p.enabled,
				default_role: p.default_role, allowed_domains: [...p.allowed_domains], allowed_groups: [...p.allowed_groups],
				groups_claim: p.groups_claim, role_map: p.role_map.map((r) => ({ ...r })), link_by_email: p.link_by_email }
			: blank();
		const cfg = blankSaml();
		for (const k of Object.keys(cfg) as (keyof SamlConfig)[]) if (p?.saml && k in p.saml) (cfg as unknown as Record<string, unknown>)[k] = p.saml[k];
		saml = { ...cfg, certificates: [...cfg.certificates] };
		described = [...(p?.saml?.described ?? [])];
		branding = p ? { label: p.branding.label, style: p.branding.style, color: p.branding.color, text_color: p.branding.text_color,
			logo: p.branding.logo, symbol: p.branding.symbol } : blankBranding();
		logos = { light: p?.branding.logo_url ?? null, dark: p?.branding.logo_dark_url ?? null };
		secret = metadataXml = newCert = '';
		pasting = addingCert = advanced = false;
		tested = loaded = null;
		copied = null;
	});
	let isSaml = $derived(form.protocol === 'saml');
	const PROTOCOLS = [
		{ id: 'oidc', label: 'OpenID Connect' },
		{ id: 'saml', label: 'SAML 2.0' }
	];
	const NAME_ID_FORMATS = [
		{ value: '', label: 'The provider’s choice' },
		{ value: 'urn:oasis:names:tc:SAML:2.0:nameid-format:persistent', label: 'Persistent', description: 'A stable, opaque ID per person' },
		{ value: 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress', label: 'Email address' },
		{ value: 'urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified', label: 'Unspecified' }
	];

	// A new provider's id follows its name until edited.
	let idTouched = $state(false);
	function nameChanged() {
		if (!provider && !idTouched) form.id = form.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32);
	}
	let redirect = $derived(provider?.redirect_uri ?? `${origin}/api/auth/sso/${form.id || '<id>'}/callback`);
	// What the SAML identity provider needs from Castor (the server's answer once saved).
	let spMetadata = $derived(provider?.saml?.metadata ?? `${origin}/api/auth/sso/${form.id || '<id>'}/saml/metadata`);
	let spAcs = $derived(provider?.saml?.acs_url ?? `${origin}/api/auth/sso/${form.id || '<id>'}/saml/acs`);
	let spEntity = $derived(saml.sp_entity_id.trim() || spMetadata);

	async function loadMetadata() {
		loading = true;
		try {
			const r = await api().sso.readMetadata(pasting ? { xml: metadataXml } : { url: saml.metadata_url.trim() });
			if (!r.ok) {
				loaded = { ok: false, text: r.error ?? 'Could not read it' };
				return;
			}
			form.issuer = r.entity_id;
			saml.sso_url = r.sso_url;
			saml.certificates = r.certificates;
			described = r.described;
			if (pasting) saml.metadata_url = '';
			loaded = { ok: true, text: `Read ${r.entity_id}: ${r.certificates.length} signing certificate${r.certificates.length === 1 ? '' : 's'}` };
			pasting = false;
		} catch (err) {
			loaded = { ok: false, text: err instanceof ApiError ? err.message : 'Could not read it' };
		} finally {
			loading = false;
		}
	}
	function addCert() {
		const pem = newCert.trim();
		if (!pem) return;
		saml.certificates = [...saml.certificates, pem];
		described = [...described, { subject: 'Added: shown once saved', not_after: '', sha256: '' }];
		newCert = '';
		addingCert = false;
	}
	function removeCert(i: number) {
		saml.certificates = saml.certificates.filter((_, j) => j !== i);
		described = described.filter((_, j) => j !== i);
	}
	const expiresSoon = (c: SamlCertificate) => !!c.not_after && new Date(c.not_after).getTime() - Date.now() < 30 * 86_400_000;

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
	async function copy(text: string = redirect) {
		try {
			await navigator.clipboard.writeText(text);
			copied = text;
			setTimeout(() => (copied = null), 2000);
		} catch {
			snackbar.error('Copying needs clipboard access; select the text instead');
		}
	}
	async function save() {
		saving = true;
		try {
			const body: SsoProviderIn = { ...form, role_map: form.role_map.filter((r) => r.group.trim()), branding: { ...branding } };
			if (isSaml) body.saml = { ...saml };
			else if (secret.trim()) body.client_secret = secret.trim();
			const saved = provider ? await api().sso.update(provider.id, body) : await api().sso.create(body);
			onsaved?.(saved);
			snackbar.show(
				provider ? `Saved ${saved.name}` : isSaml ? `Added ${saved.name}. Give the identity provider Castor’s metadata if you haven’t.` : `Added ${saved.name}. Register the redirect URI at the provider if you haven't.`
			);
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
	let canSave = $derived(
		!!form.name.trim() && !!form.id && !!form.issuer.trim() && !saving && (isSaml ? !!saml.sso_url.trim() && saml.certificates.length > 0 : !!form.client_id.trim())
	);
</script>

<Dialog bind:open title={provider ? `Edit ${provider.name}` : 'Add a sign-on provider'} icon="key" size="lg">
	<div class="flex flex-col gap-4 pt-1 pb-2">
		{#if !provider}
			<Tabs tabs={PROTOCOLS} value={form.protocol} onchange={(id) => { form.protocol = id as SsoProtocol; form.issuer = ''; tested = loaded = null; }} class="-mt-1" />
		{/if}
		<div class="grid gap-4 sm:grid-cols-[1fr_12rem]">
			<TextField label="Name" bind:value={form.name} placeholder="Okta, Entra ID, Keycloak…" required supporting="On the sign-in button: Continue with …" oninput={nameChanged} />
			<TextField label="Id" bind:value={form.id} mono required readonly={!!provider} supporting={isSaml ? "In Castor’s SAML addresses" : "In the redirect URI"} oninput={() => (idTouched = true)} />
		</div>

		{#if isSaml}
			{@render samlSettings()}
		{:else}
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
				<Button variant="text" icon={copied === redirect ? 'check' : 'content_copy'} onclick={() => copy(redirect)}>{copied === redirect ? 'Copied' : 'Copy'}</Button>
			</div>
			<code class="block font-mono text-[12.5px] break-all text-on-surface select-all">{redirect}</code>
			<p class="mt-1 body-small text-on-surface-variant">Register this at the provider as the app's redirect (callback) URI. It uses CASTOR_PUBLIC_URL when that's set.</p>
		</div>
		{/if}

		<h3 class="mt-1 title-small text-on-surface">Its button</h3>
		<div class="flex flex-col gap-2 rounded-lg bg-surface-container-low p-4">
			<span class="label-medium text-on-surface-variant">On the sign-in page</span>
			<div class="mx-auto w-full max-w-sm">
				<SsoButton name={form.name || 'your provider'} {branding} logoUrl={logos.light} logoDarkUrl={logos.dark} />
			</div>
		</div>
		<TextField label="Words" bind:value={branding.label} placeholder="Continue with {form.name || 'the provider'}"
			supporting="{'{name}'} is the provider’s name. With a wordmark, they’re what screen readers say." />
		<div class="flex flex-col gap-2">
			<span class="label-large text-on-surface">Style</span>
			<div class="flex flex-wrap gap-2" role="radiogroup" aria-label="Style">
				{#each STYLES as st (st.value)}
					<Chip variant="filter" icon={st.icon} selected={branding.style === st.value} onclick={() => (branding.style = st.value)}>{st.label}</Chip>
				{/each}
			</div>
		</div>
		{#if branding.style !== 'theme'}
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="flex items-start gap-2">
					<input type="color" class="mt-2 size-10 shrink-0 cursor-pointer rounded-md border border-outline-variant bg-transparent" aria-label="Pick the colour"
						value={branding.color || '#2f6feb'} oninput={(e) => (branding.color = (e.currentTarget as HTMLInputElement).value)} />
					<TextField label="Colour" bind:value={branding.color} mono placeholder="#2f6feb" class="flex-1" supporting={hardToRead || 'The brand’s colour, as #rrggbb'} />
				</div>
				{#if branding.style === 'filled'}
					<div class="flex items-start gap-2">
						<input type="color" class="mt-2 size-10 shrink-0 cursor-pointer rounded-md border border-outline-variant bg-transparent" aria-label="Pick the words’ colour"
							value={branding.text_color || '#ffffff'} oninput={(e) => (branding.text_color = (e.currentTarget as HTMLInputElement).value)} />
						<TextField label="Words’ colour (optional)" bind:value={branding.text_color} mono placeholder="worked out from the colour" class="flex-1"
							supporting="Empty: white or black, whichever reads" />
					</div>
				{/if}
			</div>
		{/if}
		<div class="flex flex-col gap-2">
			<span class="label-large text-on-surface">Logo</span>
			<div class="flex flex-wrap gap-2" role="radiogroup" aria-label="Logo">
				{#each LOGOS as lg (lg.value)}
					<Chip variant="filter" icon={lg.icon} selected={branding.logo === lg.value} onclick={() => (branding.logo = lg.value)}>{lg.label}</Chip>
				{/each}
			</div>
			{#if branding.logo === 'symbol'}
				<TextField label="Icon" bind:value={branding.symbol} mono placeholder="key"
					supporting="A Material Symbols name: key, shield_person, badge, lock, apartment…" />
			{:else if branding.logo === 'icon' || branding.logo === 'wordmark'}
				<p class="body-small text-on-surface-variant">
					{branding.logo === 'icon' ? 'A square logo before the words (it’s drawn 20 px tall).' : 'A horizontal logo instead of the words (24 px tall).'}
					SVG, PNG, WebP, JPEG or GIF, up to 512 KB. A second one for dark themes is optional.
				</p>
				{#if provider}
					{#each [{ v: 'light', label: 'Logo', url: logos.light }, { v: 'dark', label: 'For dark themes', url: logos.dark }] as slot (slot.v)}
						<div class="flex items-center gap-3 rounded-md border border-outline-variant px-3 py-2">
							<!-- On what it'll be on: the button's colour when it's filled, else a light or dark page. -->
							<span class="flex h-10 w-24 shrink-0 items-center justify-center rounded-sm border border-outline-variant {slot.v === 'dark' ? 'bg-[#1b1b1f]' : 'bg-white'}"
								style={branding.style === 'filled' && /^#[0-9a-f]{6}$/i.test(branding.color) ? `background:${branding.color}` : ''}>
								{#if slot.url}<img src={slot.url} alt="" class="max-h-8 max-w-20 object-contain" />{:else}<Icon name="image" size={20} class="text-[#888]" />{/if}
							</span>
							<span class="min-w-0 flex-1 label-large text-on-surface">{slot.label}</span>
							<label class="state-layer focus-ring inline-flex h-10 cursor-pointer items-center gap-2 rounded-full px-3 label-large text-primary">
								<Icon name={uploading === slot.v ? 'progress_activity' : 'upload'} size={18} />{slot.url ? 'Replace' : 'Upload'}
								<input type="file" class="sr-only" accept="image/svg+xml,image/png,image/webp,image/jpeg,image/gif"
									onchange={(e) => uploadLogo(slot.v as 'light' | 'dark', e.currentTarget)} />
							</label>
							{#if slot.url}<IconButton icon="delete" label="Remove the {slot.label.toLowerCase()}" onclick={() => removeLogo(slot.v as 'light' | 'dark')} />{/if}
						</div>
					{/each}
				{:else}
					<p class="body-small text-on-surface-variant">Add the provider first, then upload its logo here.</p>
				{/if}
			{/if}
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

		{#if isSaml}
			<TextField label="Groups attribute" bind:value={form.groups_claim} mono
				supporting="The attribute that lists the person’s groups: groups, memberOf, or Entra ID’s http://schemas.microsoft.com/ws/2008/06/identity/claims/groups." />
			<Switch bind:checked={saml.trust_email} label="The provider’s email addresses are verified (needed for allowed domains and linking by email)" />
		{:else}
			<div class="grid gap-4 sm:grid-cols-2">
				<TextField label="Groups claim" bind:value={form.groups_claim} mono supporting="Where the provider puts groups in the token (or UserInfo)." />
				<TextField label="Scopes" bind:value={form.scopes} mono supporting="Must include openid; add groups if your provider needs it." />
			</div>
		{/if}
		<Switch bind:checked={form.link_by_email} label={isSaml ? 'Link to an existing account with the same email' : "Link to an existing account with the same email, when the provider says it's verified"} />
		<Switch bind:checked={form.enabled} label="Offer it on the sign-in page" />
	</div>
	{#snippet actions()}
		{#if provider}<Button variant="text" class="mr-auto text-error!" onclick={remove}>Remove</Button>{/if}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon={provider ? 'save' : 'add'} loading={saving} disabled={!canSave} onclick={save}>{provider ? 'Save' : 'Add provider'}</Button>
	{/snippet}
</Dialog>

{#snippet copyRow(label: string, value: string)}
	<div class="flex items-center gap-2">
		<div class="min-w-0 flex-1">
			<span class="block label-medium text-on-surface-variant">{label}</span>
			<code class="block font-mono text-[12.5px] break-all text-on-surface select-all">{value}</code>
		</div>
		<IconButton icon={copied === value ? 'check' : 'content_copy'} label={copied === value ? 'Copied' : `Copy the ${label.toLowerCase()}`} onclick={() => copy(value)} />
	</div>
{/snippet}

{#snippet samlSettings()}
	<div class="rounded-md bg-surface-container px-3 py-2.5">
		<div class="mb-1.5 flex items-center gap-2">
			<span class="flex-1 label-large text-on-surface">Give these to the identity provider</span>
			<Button variant="text" icon="download" disabled={!provider} onclick={() => (window.location.href = spMetadata)}>Metadata</Button>
		</div>
		<div class="flex flex-col gap-1.5">
			{@render copyRow('Metadata URL', spMetadata)}
			{@render copyRow('Entity ID (audience)', spEntity)}
			{@render copyRow('Assertion consumer service (ACS) URL', spAcs)}
		</div>
		<p class="mt-1.5 body-small text-on-surface-variant">
			Most providers take the metadata URL{provider ? '' : ' (it works once the provider is added)'}; others want the entity ID and ACS URL. Castor wants the assertion signed and not encrypted. These use CASTOR_PUBLIC_URL when that's set.
		</p>
	</div>

	<h3 class="mt-1 title-small text-on-surface">The identity provider</h3>
	<div class="flex flex-col gap-2">
		{#if pasting}
			<TextField label="Metadata XML" bind:value={metadataXml} multiline rows={6} mono placeholder={'<md:EntityDescriptor …>'} oninput={() => (loaded = null)} />
			<div class="flex gap-2">
				<Button variant="tonal" icon="input" loading={loading} disabled={!metadataXml.trim()} onclick={loadMetadata}>Read it</Button>
				<Button variant="text" onclick={() => (pasting = false)}>Use a URL instead</Button>
			</div>
		{:else}
			<div class="flex items-start gap-2">
				<TextField label="Metadata URL" bind:value={saml.metadata_url} mono class="flex-1" placeholder="https://login.example.com/app/…/sso/saml/metadata"
					supporting="Castor reads the entity ID, sign-on URL and certificates from it. Load it again when the provider changes its certificate." oninput={() => (loaded = null)} />
				<Button variant="tonal" class="mt-2" loading={loading} disabled={!saml.metadata_url.trim()} onclick={loadMetadata}>Load</Button>
			</div>
			<Button variant="text" icon="content_paste" class="self-start" onclick={() => (pasting = true)}>Paste the metadata XML instead</Button>
		{/if}
		{#if loaded}
			<p class="flex items-start gap-1.5 body-small {loaded.ok ? 'text-on-surface-variant' : 'text-error'}">
				<Icon name={loaded.ok ? 'check_circle' : 'error'} size={16} class="mt-px shrink-0 {loaded.ok ? 'text-primary' : ''}" />{loaded.text}
			</p>
		{/if}
	</div>
	<div class="grid gap-4 sm:grid-cols-2">
		<TextField label="Entity ID" bind:value={form.issuer} mono required supporting="The identity provider’s; responses must be issued by it." />
		<TextField label="Single sign-on URL" bind:value={saml.sso_url} mono required supporting="Its HTTP-Redirect binding." />
	</div>

	<div class="flex flex-col gap-2">
		<div class="flex items-center gap-2">
			<span class="flex-1 label-large text-on-surface">Signing certificates</span>
			<Button variant="text" icon="add" onclick={() => (addingCert = true)}>Add</Button>
		</div>
		{#each saml.certificates as _, i (i)}
			{@const c = described[i]}
			<div class="flex items-start gap-3 rounded-md border border-outline-variant/70 px-3 py-2">
				<Icon name="verified" size={20} class="mt-0.5 shrink-0 text-on-surface-variant" />
				<div class="min-w-0 flex-1">
					<span class="block truncate body-medium text-on-surface" title={c?.subject}>{c?.subject ?? 'A certificate'}</span>
					{#if c?.not_after}
						<span class="block body-small {expiresSoon(c) ? 'text-error' : 'text-on-surface-variant'}" title={absoluteTime(c.not_after)}>
							{new Date(c.not_after).getTime() < Date.now() ? 'Expired' : 'Expires'} {relativeTime(c.not_after)}
						</span>
					{/if}
					{#if c?.sha256}<code class="block truncate font-mono text-[11.5px] text-on-surface-variant" title="SHA-256 fingerprint">{c.sha256}</code>{/if}
				</div>
				<IconButton icon="delete" label="Remove this certificate" onclick={() => removeCert(i)} />
			</div>
		{:else}
			<p class="body-small text-on-surface-variant">None yet: load the metadata, or add the certificate the provider signs with.</p>
		{/each}
		{#if addingCert}
			<TextField label="Certificate" bind:value={newCert} multiline rows={4} mono placeholder="-----BEGIN CERTIFICATE-----" supporting="PEM, or the base64 from the provider’s console. With two, either may sign (for a rotation)." />
			<div class="flex gap-2">
				<Button variant="tonal" disabled={!newCert.trim()} onclick={addCert}>Add certificate</Button>
				<Button variant="text" onclick={() => { addingCert = false; newCert = ''; }}>Cancel</Button>
			</div>
		{/if}
	</div>

	<button type="button" class="state-layer -mx-2 flex items-center gap-1 self-start rounded-full px-2 py-1 label-large text-primary" aria-expanded={advanced} onclick={() => (advanced = !advanced)}>
		<Icon name={advanced ? 'expand_less' : 'expand_more'} size={20} />Who the person is, and Castor’s entity ID
	</button>
	{#if advanced}
		<div class="grid gap-4 sm:grid-cols-2">
			<Select label="NameID format to ask for" bind:value={saml.name_id_format} options={NAME_ID_FORMATS} supporting="A transient NameID can’t be used unless a subject attribute is set." />
			<TextField label="Subject attribute" bind:value={saml.subject_attribute} mono supporting="Who the person is at the provider. Empty: the NameID." />
			<TextField label="Email attribute" bind:value={saml.email_attribute} mono supporting="Empty: email, mail and the usual URIs." />
			<TextField label="Name attribute" bind:value={saml.name_attribute} mono supporting="Empty: displayName, or given name and surname." />
			<TextField label="Username attribute" bind:value={saml.username_attribute} mono supporting="For new accounts. Empty: uid, username, or the email’s first part." />
			<TextField label="Castor’s entity ID" bind:value={saml.sp_entity_id} mono supporting="Empty: the metadata URL. Set it if the provider already knows Castor by another." />
		</div>
	{/if}
{/snippet}
