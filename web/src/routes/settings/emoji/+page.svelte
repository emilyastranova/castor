<script lang="ts">
	import { brand } from '$lib/brand.svelte';
	import type { CustomEmoji } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon, IconButton, TextField, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { customEmojis } from '$lib/emojis.svelte';
	import { pop } from '$lib/motion';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let emojis = $state<CustomEmoji[]>(data.emojis);
	let name = $state('');
	let file = $state<File | null>(null);
	let preview = $state<string | null>(null);
	let saving = $state(false);
	let dragging = $state(false);
	let filter = $state('');
	let fileInput: HTMLInputElement | undefined = $state();

	const NAME = /^[a-z0-9_]{2,32}$/;
	const cleanName = (raw: string) => raw.toLowerCase().replace(/\.[a-z0-9]+$/, '').replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 32);
	const taken = $derived(emojis.some((e) => e.name === name));
	const nameError = $derived(name && !NAME.test(name) ? '2–32 lowercase letters, digits and underscores' : '');

	function choose(f: File | undefined | null) {
		if (!f) return;
		if (!/^image\/(png|gif|webp|jpeg)$/.test(f.type)) {
			snackbar.error('Pick a PNG, GIF, WebP or JPEG image');
			return;
		}
		if (f.size > 256 * 1024) {
			snackbar.error('That image is larger than 256 KB');
			return;
		}
		file = f;
		if (preview) URL.revokeObjectURL(preview);
		preview = URL.createObjectURL(f);
		if (!name) name = cleanName(f.name);
	}
	async function add() {
		if (!file || !NAME.test(name)) return;
		if (taken && !(await confirmAction({ title: `Replace :${name}:?`, message: 'Reactions made with it show the new picture.', confirmLabel: 'Replace it' }))) return;
		saving = true;
		try {
			const e = await api().emojis.add(name, file, taken);
			emojis = [...emojis.filter((x) => x.name !== e.name), e].sort((a, b) => a.name.localeCompare(b.name));
			void customEmojis.refresh();
			snackbar.show(`Added :${e.name}:`);
			name = '';
			file = null;
			if (preview) URL.revokeObjectURL(preview);
			preview = null;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message.replace(/^(name|file): /, '') : 'Couldn’t add it');
		} finally {
			saving = false;
		}
	}
	async function remove(e: CustomEmoji) {
		const ok = await confirmAction({ title: `Remove :${e.name}:?`, message: 'Every reaction made with it goes too. This can’t be undone.', confirmLabel: 'Remove it', danger: true, icon: 'delete' });
		if (!ok) return;
		try {
			await api().emojis.remove(e.name);
			emojis = emojis.filter((x) => x.name !== e.name);
			void customEmojis.refresh();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Couldn’t remove it');
		}
	}
	const shown = $derived(emojis.filter((e) => !filter.trim() || e.name.includes(filter.trim().toLowerCase())));
</script>

<svelte:head><title>Emoji · Settings · {brand.name}</title></svelte:head>

<TopAppBar title="Emoji" subtitle="Your own emoji, for everyone to react with" />

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-6 pt-2">
		<Card variant="outlined" class="flex flex-col gap-4 p-4">
			<h2 class="title-medium text-on-surface">Add an emoji</h2>
			<div class="flex flex-wrap items-start gap-4">
				<button
					type="button"
					class="focus-ring flex size-28 shrink-0 flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed text-on-surface-variant transition-colors
						{dragging ? 'border-primary bg-primary-container/40' : 'border-outline-variant hover:border-outline'}"
					aria-label="Pick an image"
					onclick={() => fileInput?.click()}
					ondragover={(e) => { e.preventDefault(); dragging = true; }}
					ondragleave={() => (dragging = false)}
					ondrop={(e) => { e.preventDefault(); dragging = false; choose(e.dataTransfer?.files[0]); }}
				>
					{#if preview}
						<img src={preview} alt="" class="size-16 object-contain" />
						<span class="label-small">Change</span>
					{:else}
						<Icon name="add_photo_alternate" size={28} />
						<span class="px-2 text-center label-small">Drop an image or click</span>
					{/if}
				</button>
				<input bind:this={fileInput} type="file" class="sr-only" accept="image/png,image/gif,image/webp,image/jpeg" onchange={(e) => choose(e.currentTarget.files?.[0])} />
				<div class="flex min-w-60 flex-1 flex-col gap-3">
					<TextField label="Name" bind:value={name} mono placeholder="party_parrot" error={nameError}
						supporting={nameError ? '' : name ? (taken ? `:${name}: exists: adding replaces its picture` : `People react with :${name}:`) : 'Taken from the file’s name'} />
					{#if preview}
						<div class="flex items-center gap-3 body-small text-on-surface-variant">
							<span>How it looks:</span>
							<span class="inline-flex h-7 items-center gap-1 rounded-full border border-outline-variant px-2 label-medium"><img src={preview} alt="" class="size-4 object-contain" />3</span>
							<span class="body-medium text-on-surface">in text <img src={preview} alt="" class="inline size-5 object-contain align-[-0.25em]" /></span>
						</div>
					{/if}
					<div><Button icon="add_reaction" loading={saving} disabled={!file || !NAME.test(name)} onclick={add}>{taken ? 'Replace it' : 'Add it'}</Button></div>
				</div>
			</div>
			<p class="body-small text-on-surface-variant">PNG, GIF (animated is fine), WebP or JPEG, up to 256 KB. Square images with a transparent background look best; they’re drawn about 20 px tall.</p>
		</Card>

		<section class="flex flex-col gap-3">
			<div class="flex items-center gap-3">
				<h2 class="min-w-0 flex-1 title-medium text-on-surface">{emojis.length ? `${emojis.length} emoji` : 'No emoji yet'}</h2>
				{#if emojis.length > 12}
					<label class="flex items-center gap-2 rounded-full bg-surface-container-high px-4">
						<Icon name="search" size={20} class="text-on-surface-variant" />
						<input class="h-10 w-40 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter" bind:value={filter} />
					</label>
				{/if}
			</div>
			{#if emojis.length}
				<ul class="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-2">
					{#each shown as e (e.name)}
						<li class="flex items-center gap-2 rounded-md border border-outline-variant py-1.5 pr-1 pl-3" in:pop>
							<img src={e.url} alt="" class="size-7 shrink-0 object-contain" />
							<span class="min-w-0 flex-1 truncate font-mono body-small text-on-surface" title=":{e.name}:">:{e.name}:</span>
							<IconButton icon="delete" label="Remove :{e.name}:" size="sm" onclick={() => remove(e)} />
						</li>
					{/each}
				</ul>
			{:else}
				<p class="body-medium text-on-surface-variant">Add your team’s own: a logo, an in-joke, a “pwned”. They show in the reaction picker under “This instance’s”, next to the usual ones.</p>
			{/if}
		</section>
	</div>
</div>
