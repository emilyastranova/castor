<script lang="ts">
	import { brand } from '$lib/brand.svelte';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import { Button, TextField, Icon } from '$lib/m3';
	import SsoButton from '$lib/components/SsoButton.svelte';
	import BrandLogo from '$lib/components/BrandLogo.svelte';

	let { data } = $props();

	let username = $state('');
	let password = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);
	let next = $derived.by(() => {
		const n = page.url.searchParams.get('next');
		return n && n.startsWith('/') && !n.startsWith('//') ? n : '/';
	});
	/** Why a sign-on round trip came back without signing in (set by the server). */
	let ssoError = $derived(page.url.searchParams.get('sso_error'));

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = null;
		try {
			await api().auth.login(username, password);
			await invalidateAll();
			await goto(next);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not reach the server';
		} finally {
			busy = false;
		}
	}
	const startUrl = (id: string) => `/api/auth/sso/${encodeURIComponent(id)}/start?next=${encodeURIComponent(next)}`;

	// Straight to the provider an admin chose, unless someone just signed out (or it would loop),
	// sign-on came back with an error, or the address says ?local (the way back to this page).
	const stay = $derived(!!ssoError || page.url.searchParams.has('local') || page.url.searchParams.has('signed_out'));
	const going = $derived(!stay && data.redirect ? data.providers.find((p) => p.id === data.redirect) ?? null : null);
	$effect(() => {
		if (going) window.location.replace(startUrl(going.id));
	});
	// The password form tucked behind a link, if an admin asked (always open when it's the only way).
	let showPassword = $state(false);
	const passwordOpen = $derived(data.password_form !== 'collapsed' || !data.providers.length || showPassword || !!error);
</script>

<svelte:head><title>Sign in · {brand.name}</title></svelte:head>

<div class="flex min-h-dvh items-center justify-center bg-surface-container p-4">
	<form class="flex w-full max-w-sm flex-col gap-6 rounded-xl bg-surface-container-lowest p-8 shadow-e1" onsubmit={submit}>
		{#if brand.info?.wordmark_url || brand.info?.wordmark_dark_url}
			<!-- The company's wordmark (Settings → Branding), with its tagline. -->
			<div class="flex flex-col gap-2">
				<h1 class="sr-only">{brand.name}</h1>
				<BrandLogo kind="wordmark" alt={brand.name} class="h-12 max-w-full self-start" />
				{#if brand.info?.tagline}<p class="body-medium text-on-surface-variant">{brand.info.tagline}</p>{/if}
			</div>
		{:else}
			<div class="flex items-center gap-3">
				{#if brand.info?.mark_url || brand.info?.mark_dark_url}
					<BrandLogo kind="mark" class="size-12 rounded-lg" />
				{:else}
					<span class="flex size-12 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
						<Icon name="account_tree" filled />
					</span>
				{/if}
				<div>
					<h1 class="headline-small text-on-surface">{brand.name}</h1>
					<p class="body-medium text-on-surface-variant">{brand.info?.tagline || (brand.branded ? '' : 'Workflow orchestration task board')}</p>
				</div>
			</div>
		{/if}

		{#if going}
			<div class="flex flex-col items-center gap-3 py-2 text-center" role="status">
				<span class="size-6 animate-spin rounded-full border-2 border-primary border-t-transparent"></span>
				<p class="body-large text-on-surface">Taking you to {going.name}…</p>
				<a class="label-large text-primary hover:underline" href="/login?local{next !== '/' ? `&next=${encodeURIComponent(next)}` : ''}">Use another way to sign in</a>
			</div>
		{:else}
		{#if page.url.searchParams.has('signed_out')}
			<p role="status" class="flex items-center gap-2 rounded-md bg-secondary-container px-3 py-2 body-small text-on-secondary-container">
				<Icon name="logout" size={18} class="shrink-0" />You’re signed out.
			</p>
		{/if}
		{#if data.message}
			<p class="body-medium whitespace-pre-line text-on-surface">{data.message}</p>
		{/if}
		{#if ssoError}
			<p role="alert" class="flex items-start gap-2 rounded-md bg-error-container px-3 py-2 body-small text-on-error-container">
				<Icon name="error" size={18} class="mt-px shrink-0" />{ssoError}
			</p>
		{/if}

		{#if data.providers.length}
			<div class="flex flex-col gap-2">
				{#each data.providers as p (p.id)}
					<SsoButton name={p.name} branding={p} logoUrl={p.logo_url} logoDarkUrl={p.logo_dark_url} href={startUrl(p.id)} />
				{/each}
			</div>
		{/if}

		{#if data.password_login && !passwordOpen}
			<button type="button" class="self-center label-large text-primary hover:underline" onclick={() => (showPassword = true)}>Sign in with a password</button>
		{:else if data.password_login}
			{#if data.providers.length}
				<div class="flex items-center gap-3 label-medium text-on-surface-variant" aria-hidden="true">
					<span class="h-px flex-1 bg-outline-variant"></span>or with a password<span class="h-px flex-1 bg-outline-variant"></span>
				</div>
			{/if}
			<TextField label="Username" bind:value={username} autocomplete="username" required autofocus={!data.providers.length || showPassword} />
			<TextField label="Password" type="password" bind:value={password} autocomplete="current-password" required {error} />
			<Button type="submit" loading={busy} class="w-full" variant={data.providers.length ? 'tonal' : 'filled'}>Sign in</Button>
		{:else if !data.providers.length}
			<p class="body-medium text-on-surface-variant">No way to sign in is set up. An administrator can turn password sign-in back on with <code class="font-mono">castor-admin password-login on</code>.</p>
		{/if}
		{/if}
		{#if brand.branded && brand.info?.powered_by}
			<p class="-mb-2 text-center label-small text-on-surface-variant">Powered by Castor</p>
		{/if}
	</form>
</div>
