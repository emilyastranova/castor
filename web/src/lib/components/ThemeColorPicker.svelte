<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import type { User } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { applySeed } from '$lib/m3/theme';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Your Material You colour: every colour role is generated from it, in light and dark.
	 *  It applies straight away and is saved to your account, so it follows you. */
	let { user, defaultColour = '#4f5bd5' }: { user: User; defaultColour?: string } = $props();

	const PRESETS = [
		{ seed: '#1a73e8', name: 'Blue' },
		{ seed: '#00897b', name: 'Teal' },
		{ seed: '#0b8043', name: 'Green' },
		{ seed: '#f9a825', name: 'Amber' },
		{ seed: '#ef6c00', name: 'Orange' },
		{ seed: '#d93025', name: 'Red' },
		{ seed: '#d81b60', name: 'Pink' },
		{ seed: '#8e24aa', name: 'Purple' },
		{ seed: '#795548', name: 'Brown' },
		{ seed: '#546e7a', name: 'Slate' }
	];

	// svelte-ignore state_referenced_locally
	let current = $state(user.theme_seed);
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function pick(seed: string, saveNow = true) {
		const before = current;
		current = seed;
		await applySeed(seed || null);
		clearTimeout(timer);
		const save = async () => {
			try {
				await api().auth.updateMe({ theme_seed: seed });
				void invalidateAll();
			} catch (err) {
				current = before;
				await applySeed(before || null);
				snackbar.error(err instanceof ApiError ? err.message : 'Could not save your colour');
			}
		};
		// Dragging through the colour wheel saves once it settles.
		if (saveNow) void save();
		else timer = setTimeout(save, 600);
	}
	let custom = $derived(!!current && !PRESETS.some((p) => p.seed === current));
</script>

<div class="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Theme colour">
	<button
		type="button"
		role="radio"
		aria-checked={!current}
		title="Default"
		class="swatch {!current ? 'chosen' : ''}"
		style="--c: {defaultColour}"
		onclick={() => pick('')}
	>
		{#if !current}<Icon name="check" size={18} />{/if}
	</button>
	{#each PRESETS as p (p.seed)}
		<button type="button" role="radio" aria-checked={current === p.seed} title={p.name} class="swatch {current === p.seed ? 'chosen' : ''}" style="--c: {p.seed}" onclick={() => pick(p.seed)}>
			{#if current === p.seed}<Icon name="check" size={18} />{/if}
		</button>
	{/each}
	<label class="swatch custom {custom ? 'chosen' : ''}" title="Your own colour" style="--c: {custom ? current : 'transparent'}">
		<Icon name={custom ? 'check' : 'palette'} size={18} />
		<input type="color" class="sr-only" value={current || defaultColour} oninput={(e) => pick((e.currentTarget as HTMLInputElement).value, false)} aria-label="Your own colour" />
	</label>
</div>

<style>
	.swatch {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2rem;
		height: 2rem;
		border-radius: 9999px;
		background: var(--c);
		color: white;
		cursor: pointer;
		outline-offset: 2px;
		transition: transform 120ms;
	}
	.swatch:hover {
		transform: scale(1.08);
	}
	.swatch.chosen {
		outline: 2px solid var(--md-sys-color-on-surface);
	}
	.swatch.custom {
		border: 1px dashed var(--md-sys-color-outline);
		color: var(--md-sys-color-on-surface-variant);
	}
	.swatch.custom.chosen {
		border: none;
		color: white;
	}
	.swatch:focus-within,
	.swatch:focus-visible {
		outline: 2px solid var(--md-sys-color-primary);
	}
	@media (prefers-reduced-motion: reduce) {
		.swatch {
			transition: none;
		}
	}
</style>
