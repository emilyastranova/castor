<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import { Button, TextField, Icon } from '$lib/m3';

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
</script>

<svelte:head><title>Sign in · Castor</title></svelte:head>

<div class="flex min-h-dvh items-center justify-center bg-surface-container p-4">
	<form class="flex w-full max-w-sm flex-col gap-6 rounded-xl bg-surface-container-lowest p-8 shadow-e1" onsubmit={submit}>
		<div class="flex items-center gap-3">
			<span class="flex size-12 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
				<Icon name="account_tree" filled />
			</span>
			<div>
				<h1 class="headline-small text-on-surface">Castor</h1>
				<p class="body-medium text-on-surface-variant">Workflow orchestration task board</p>
			</div>
		</div>

		{#if ssoError}
			<p role="alert" class="flex items-start gap-2 rounded-md bg-error-container px-3 py-2 body-small text-on-error-container">
				<Icon name="error" size={18} class="mt-px shrink-0" />{ssoError}
			</p>
		{/if}

		{#if data.providers.length}
			<div class="flex flex-col gap-2">
				{#each data.providers as p (p.id)}
					<!-- A full page load: the server sends the browser on to the provider. -->
					<a
						href={startUrl(p.id)}
						data-sveltekit-reload
						class="state-layer flex h-12 items-center justify-center gap-2 rounded-full border border-outline label-large text-on-surface"
					>
						<Icon name="key" size={20} class="text-primary" />Continue with {p.name}
					</a>
				{/each}
			</div>
		{/if}

		{#if data.password_login}
			{#if data.providers.length}
				<div class="flex items-center gap-3 label-medium text-on-surface-variant" aria-hidden="true">
					<span class="h-px flex-1 bg-outline-variant"></span>or with a password<span class="h-px flex-1 bg-outline-variant"></span>
				</div>
			{/if}
			<TextField label="Username" bind:value={username} autocomplete="username" required autofocus={!data.providers.length} />
			<TextField label="Password" type="password" bind:value={password} autocomplete="current-password" required {error} />
			<Button type="submit" loading={busy} class="w-full" variant={data.providers.length ? 'tonal' : 'filled'}>Sign in</Button>
		{:else if !data.providers.length}
			<p class="body-medium text-on-surface-variant">No way to sign in is set up. An administrator can turn password sign-in back on with <code class="font-mono">castor-admin password-login on</code>.</p>
		{/if}
	</form>
</div>
