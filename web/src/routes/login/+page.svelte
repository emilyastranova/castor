<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import { Button, TextField, Icon } from '$lib/m3';

	let username = $state('');
	let password = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = null;
		try {
			await api().auth.login(username, password);
			await invalidateAll();
			const next = page.url.searchParams.get('next');
			await goto(next && next.startsWith('/') ? next : '/');
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not reach the server';
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Sign in · Castor</title></svelte:head>

<div class="flex min-h-dvh items-center justify-center bg-surface-container p-4">
	<form
		class="flex w-full max-w-sm flex-col gap-6 rounded-xl bg-surface-container-lowest p-8 shadow-e1"
		onsubmit={submit}
	>
		<div class="flex items-center gap-3">
			<span class="flex size-12 items-center justify-center rounded-lg bg-primary-container text-on-primary-container">
				<Icon name="account_tree" filled />
			</span>
			<div>
				<h1 class="headline-small text-on-surface">Castor</h1>
				<p class="body-medium text-on-surface-variant">Workflow orchestration task board</p>
			</div>
		</div>
		<TextField label="Username" bind:value={username} autocomplete="username" required autofocus />
		<TextField label="Password" type="password" bind:value={password} autocomplete="current-password" required error={error} />
		<Button type="submit" loading={busy} class="w-full">Sign in</Button>
	</form>
</div>
