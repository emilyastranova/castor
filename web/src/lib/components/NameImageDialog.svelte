<script lang="ts">
	import { onMount } from 'svelte';
	import { Button, Dialog, TextField } from '$lib/m3';

	interface Props {
		file: File;
		/** How many more pasted images are waiting after this one. */
		remaining?: number;
		onconfirm: (name: string) => void;
		oncancel: () => void;
	}
	let { file, remaining = 0, onconfirm, oncancel }: Props = $props();

	// One instance per pasted image (the parent keys it), so each instance resolves exactly once.
	let open = $state(true);
	let done = false;
	const inputId = `img-name-${Math.random().toString(36).slice(2, 8)}`;

	/** Keep a real filename from the clipboard; browsers name raw screenshots "image.png". */
	function suggestedName(f: File): string {
		if (f.name && !/^image\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name)) {
			return f.name.replace(/\.[^.]+$/, '');
		}
		const d = new Date();
		const p = (n: number) => String(n).padStart(2, '0');
		return `screenshot-${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
	}

	// svelte-ignore state_referenced_locally
	let name = $state(suggestedName(file));
	// svelte-ignore state_referenced_locally
	const preview = URL.createObjectURL(file);

	onMount(() => {
		// Select the suggested name so typing replaces it straight away.
		requestAnimationFrame(() => {
			const input = document.getElementById(inputId) as HTMLInputElement | null;
			input?.focus();
			input?.select();
		});
		return () => URL.revokeObjectURL(preview);
	});

	function submit(e: SubmitEvent) {
		e.preventDefault();
		const trimmed = name.trim();
		if (!trimmed || done) return;
		done = true;
		open = false;
		onconfirm(trimmed);
	}

	function dismissed() {
		if (done) return;
		done = true;
		oncancel();
	}
</script>

<Dialog bind:open title="Name this image" icon="image" onclose={dismissed}>
	<form id={inputId + '-form'} class="flex flex-col gap-4 pt-2 pb-2" onsubmit={submit}>
		<img
			src={preview}
			alt="Preview of what you pasted"
			class="max-h-56 w-full rounded-md bg-surface-container-highest object-contain"
		/>
		<TextField
			id={inputId}
			label="Name"
			bind:value={name}
			required
			supporting={remaining > 0
				? `${remaining} more pasted image${remaining === 1 ? '' : 's'} after this one`
				: 'Shown as the image caption and its attachment name'}
		/>
	</form>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Discard</Button>
		<Button type="submit" form={inputId + '-form'} icon="add_photo_alternate" disabled={!name.trim()}
			>Insert</Button
		>
	{/snippet}
</Dialog>
