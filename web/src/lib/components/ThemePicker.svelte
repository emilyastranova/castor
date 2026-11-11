<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import type { Theme, User } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { applyTheme } from '$lib/m3/theme';
	import { themes } from '$lib/themes.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import ThemePreview from './ThemePreview.svelte';

	/** Your theme: Castor's own or one from the server (built-in or an admin's). It applies
	 *  straight away and is saved to your account, so it follows you. */
	let { user }: { user: User } = $props();
	// svelte-ignore state_referenced_locally
	let current = $state(user.theme ?? '');
	$effect(() => void themes.load());

	async function pick(t: Theme) {
		if (t.id === current) return;
		const before = themes.byId(current);
		current = t.id;
		applyTheme(t);
		try {
			await api().auth.updateMe({ theme: t.id });
			await invalidateAll();
		} catch (err) {
			current = before?.id ?? '';
			if (before) applyTheme(before);
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save your theme');
		}
	}
</script>

<div class="grid grid-cols-1 gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Theme">
	{#each themes.list as t (t.id)}
		<button
			type="button"
			role="radio"
			aria-checked={current === t.id}
			class="state-layer focus-ring flex flex-col overflow-hidden rounded-md border text-left {current === t.id ? 'border-primary outline outline-1 outline-primary' : 'border-outline-variant'}"
			onclick={() => pick(t)}
		>
			<ThemePreview theme={t} />
			<span class="flex items-start gap-2 px-3 py-2.5">
				<span class="min-w-0 flex-1">
					<span class="block label-large text-on-surface">{t.name}</span>
					{#if t.description}<span class="mt-0.5 block body-small text-on-surface-variant">{t.description}</span>{/if}
				</span>
				{#if current === t.id}<Icon name="check" size={18} class="mt-px shrink-0 text-primary" />{/if}
			</span>
		</button>
	{/each}
</div>
