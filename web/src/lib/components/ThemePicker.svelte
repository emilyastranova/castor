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
	 *  straight away and is saved to your account, so it follows you. Until you pick one you
	 *  follow the instance default (an admin's choice), and you can go back to following it. */
	let { user }: { user: User } = $props();
	// svelte-ignore state_referenced_locally
	let current = $state<string | null>(user.theme ?? null);
	$effect(() => void themes.load());
	let shown = $derived(themes.effectiveId(current));
	let dflt = $derived(themes.byId(themes.defaultId));

	/** `null`: follow the default. */
	async function pick(id: string | null) {
		if (id === current) return;
		const before = current;
		current = id;
		const t = themes.effective(id);
		if (t) applyTheme(t);
		try {
			await api().auth.updateMe({ theme: id });
			await invalidateAll();
		} catch (err) {
			current = before;
			const back = themes.effective(before);
			if (back) applyTheme(back);
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save your theme');
		}
	}
</script>

{#if themes.loaded && dflt}
	<p class="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 body-small text-on-surface-variant">
		{#if current === null}
			<Icon name="check_circle" size={16} class="text-primary" />You follow the default for everyone, {dflt.name}; pick one to keep it whatever the default becomes.
		{:else}
			The default for everyone is {dflt.name}.
			<button type="button" class="text-primary hover:underline" onclick={() => pick(null)}>Follow the default</button>
		{/if}
	</p>
{/if}

<div class="grid grid-cols-1 gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Theme">
	{#each themes.list as t (t.id)}
		<button
			type="button"
			role="radio"
			aria-checked={shown === t.id}
			class="state-layer focus-ring flex flex-col overflow-hidden rounded-md border text-left {shown === t.id ? 'border-primary outline outline-1 outline-primary' : 'border-outline-variant'}"
			onclick={() => pick(t.id)}
		>
			<ThemePreview theme={t} />
			<span class="flex items-start gap-2 px-3 py-2.5">
				<span class="min-w-0 flex-1">
					<span class="flex flex-wrap items-center gap-x-1.5 label-large text-on-surface">
						{t.name}
						{#if t.id === themes.defaultId}<span class="rounded-xs border border-outline-variant px-1.5 label-small text-on-surface-variant">Default</span>{/if}
					</span>
					{#if t.description}<span class="mt-0.5 block body-small text-on-surface-variant">{t.description}</span>{/if}
				</span>
				{#if shown === t.id}<Icon name="check" size={18} class="mt-px shrink-0 text-primary" />{/if}
			</span>
		</button>
	{/each}
</div>
