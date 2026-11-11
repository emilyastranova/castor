<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import type { User } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { applyTheme } from '$lib/m3/theme';
	import { THEMES, type ThemeDef } from '$lib/m3/themes';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Your theme: applies straight away and is saved to your account, so it follows you. Each
	 *  tile is drawn in the theme's own colours (a picture of it, so not themed itself). */
	let { user }: { user: User } = $props();
	// svelte-ignore state_referenced_locally
	let current = $state(user.theme ?? '');

	async function pick(t: ThemeDef) {
		if (t.id === current) return;
		const before = current;
		current = t.id;
		applyTheme(t.id);
		try {
			await api().auth.updateMe({ theme: t.id });
			await invalidateAll();
		} catch (err) {
			current = before;
			applyTheme(before);
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save your theme');
		}
	}
</script>

<div class="grid grid-cols-1 gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Theme">
	{#each THEMES as t (t.id)}
		{@const [bg, surface, ink, accent] = t.preview}
		<button
			type="button"
			role="radio"
			aria-checked={current === t.id}
			class="state-layer focus-ring flex flex-col overflow-hidden rounded-md border text-left {current === t.id ? 'border-primary outline outline-1 outline-primary' : 'border-outline-variant'}"
			onclick={() => pick(t)}
		>
			<span class="flex h-16 items-end gap-2 p-2.5" style:background={bg} aria-hidden="true">
				<span class="flex flex-1 flex-col gap-1.5 rounded-xs p-2" style:background={surface}>
					<span class="block h-1.5 w-3/4 rounded-full" style:background={ink}></span>
					<span class="block h-1.5 w-1/2 rounded-full opacity-50" style:background={ink}></span>
				</span>
				<span class="block h-5 w-10 shrink-0 {t.id === 'astralabs' ? 'cut' : 'rounded-full'}" style:background={accent}></span>
			</span>
			<span class="flex items-start gap-2 px-3 py-2.5">
				<span class="min-w-0 flex-1">
					<span class="block label-large text-on-surface">{t.name}</span>
					<span class="mt-0.5 block body-small text-on-surface-variant">{t.description}</span>
				</span>
				{#if current === t.id}<Icon name="check" size={18} class="mt-px shrink-0 text-primary" />{/if}
			</span>
		</button>
	{/each}
</div>

<style>
	/* The AstraLabs primary's 60° top-right cut. */
	.cut {
		clip-path: polygon(0 0, calc(100% - 6px) 0, 100% 10.4px, 100% 100%, 0 100%);
	}
</style>
