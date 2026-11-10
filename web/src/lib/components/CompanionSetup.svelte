<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import type { User } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Choosing the Companion: the built-in one or your own picture, and the seat line (where
	 *  the figure sits on a comment), with a preview that sits exactly as the timeline does. */
	let { user }: { user: User } = $props();

	const DEFAULT_SEAT = 0.605;
	// svelte-ignore state_referenced_locally
	let seat = $state(user.companion_seat ?? DEFAULT_SEAT);
	// svelte-ignore state_referenced_locally
	let url = $state<string | null>(user.companion_url ?? null);
	let busy = $state(false);
	let fileInput = $state<HTMLInputElement>();
	let src = $derived(url ?? '/companion/sitting.webp');

	async function upload(file: File | undefined) {
		if (!file) return;
		busy = true;
		try {
			const saved = await api().auth.uploadCompanion(file);
			url = saved.companion_url ?? null;
			snackbar.show('Your companion is in. Move the seat line until they sit on the comment.');
			await invalidateAll();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not upload the picture');
		} finally {
			busy = false;
			if (fileInput) fileInput.value = '';
		}
	}
	async function reset() {
		busy = true;
		try {
			const saved = await api().auth.resetCompanion();
			url = null;
			seat = saved.companion_seat ?? DEFAULT_SEAT;
			await invalidateAll();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not change it back');
		} finally {
			busy = false;
		}
	}
	let timer: ReturnType<typeof setTimeout> | undefined;
	function seatChanged() {
		clearTimeout(timer);
		timer = setTimeout(async () => {
			try {
				await api().auth.updateMe({ companion_seat: seat });
				await invalidateAll();
			} catch {
				snackbar.error('Could not save the seat line');
			}
		}, 350);
	}
</script>

<div class="flex flex-col gap-3">
	<!-- The preview: a comment like the timeline's, with the companion at the same height and place. -->
	<div class="relative h-[212px] overflow-hidden rounded-md bg-surface" aria-label="Preview" role="img">
		<div class="absolute right-3 bottom-3 left-3 top-[118px] rounded-md bg-surface-container px-3 py-2.5">
			<span class="block h-2.5 w-3/5 rounded-full bg-on-surface/12"></span>
			<span class="mt-2 block h-2.5 w-4/5 rounded-full bg-on-surface/12"></span>
			<span class="mt-2 block h-2.5 w-2/5 rounded-full bg-on-surface/12"></span>
		</div>
		<img
			{src}
			alt=""
			class="pointer-events-none absolute right-12 h-[150px] w-auto drop-shadow-[0_2px_2px_rgb(0_0_0/0.28)]"
			style:top="calc(118px + 1px - 150px * {seat})"
		/>
	</div>

	<label class="flex flex-col gap-1">
		<span class="flex items-baseline justify-between body-small text-on-surface-variant">
			<span>Seat line: where they sit on the comment</span>
			<span class="font-mono">{Math.round(seat * 1000) / 10}% down</span>
		</span>
		<input type="range" min="0.05" max="0.95" step="0.005" bind:value={seat} oninput={seatChanged} class="w-full accent-primary" aria-label="Seat line, from the top of the picture" />
	</label>

	<div class="flex flex-wrap items-center gap-2">
		<Button variant="tonal" icon="upload" loading={busy} onclick={() => fileInput?.click()}>{url ? 'Change picture' : 'Use your own picture'}</Button>
		{#if url}<Button variant="text" icon="undo" disabled={busy} onclick={reset}>Back to the built-in one</Button>{/if}
		<a href="/companion/template.png" download="castor-companion-template.png" class="ml-auto label-medium text-primary hover:underline">Template</a>
	</div>
	<p class="body-small text-on-surface-variant">
		A PNG or WebP with a transparent background, up to 3 MB, works best. Draw them sitting, legs hanging down, on the template's seat line (60.5% from the top), or move the line above until they sit on the comment. Only you see your companion.
	</p>
	<input bind:this={fileInput} type="file" accept="image/png,image/webp,image/gif,image/jpeg" class="hidden" onchange={(e) => upload(e.currentTarget.files?.[0])} />
</div>
