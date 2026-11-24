<script lang="ts">
	import { brand } from '$lib/brand.svelte';
	import type { BrandLogo, Branding, Client } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon, IconButton, Switch, TextField, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let info = $state<Branding>(data.branding);
	// svelte-ignore state_referenced_locally
	let clients = $state<Client[]>(data.clients);
	// svelte-ignore state_referenced_locally
	let name = $state(data.settings.brand_name ?? '');
	// svelte-ignore state_referenced_locally
	let tagline = $state(data.settings.brand_tagline ?? '');
	// svelte-ignore state_referenced_locally
	let footer = $state(data.settings.report_footer ?? '');
	// svelte-ignore state_referenced_locally
	let poweredBy = $state(data.settings.powered_by ?? true);
	// svelte-ignore state_referenced_locally
	let saved = $state(JSON.stringify([data.settings.brand_name ?? '', data.settings.brand_tagline ?? '', data.settings.report_footer ?? '', data.settings.powered_by ?? true]));
	let saving = $state(false);
	let busy = $state<string | null>(null);
	const dirty = $derived(JSON.stringify([name.trim(), tagline.trim(), footer.trim(), poweredBy]) !== saved);
	const ACCEPT = 'image/svg+xml,image/png,image/webp,image/jpeg,image/gif';

	async function save() {
		saving = true;
		try {
			const s = await api().settings.update({ brand_name: name.trim(), brand_tagline: tagline.trim(), report_footer: footer.trim(), powered_by: poweredBy });
			name = s.brand_name ?? '';
			tagline = s.brand_tagline ?? '';
			footer = s.report_footer ?? '';
			saved = JSON.stringify([name, tagline, footer, s.powered_by ?? true]);
			info = await api().branding.get();
			brand.info = info;
			snackbar.show('Branding saved');
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Couldn’t save it');
		} finally {
			saving = false;
		}
	}

	async function upload(kind: BrandLogo, input: HTMLInputElement) {
		const file = input.files?.[0];
		input.value = '';
		if (!file) return;
		busy = kind;
		try {
			info = await api().branding.uploadLogo(kind, file);
			brand.info = info;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Couldn’t upload it');
		} finally {
			busy = null;
		}
	}
	async function removeLogo(kind: BrandLogo) {
		busy = kind;
		try {
			info = await api().branding.removeLogo(kind);
			brand.info = info;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Couldn’t remove it');
		} finally {
			busy = null;
		}
	}
	async function clientLogo(c: Client, variant: 'light' | 'dark', input: HTMLInputElement | null) {
		const file = input?.files?.[0];
		if (input) input.value = '';
		if (input && !file) return;
		busy = `${c.id}:${variant}`;
		try {
			const updated = file ? await api().clients.uploadLogo(c.id, file, variant) : await api().clients.removeLogo(c.id, variant);
			clients = clients.map((x) => (x.id === updated.id ? updated : x));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Couldn’t change the logo');
		} finally {
			busy = null;
		}
	}

	const SLOTS: { kind: BrandLogo; title: string; detail: string; dark: boolean }[] = [
		{ kind: 'mark', title: 'Square logo', detail: 'The navigation rail and the browser tab', dark: false },
		{ kind: 'mark_dark', title: 'Square logo, dark themes', detail: 'Optional: when the light one doesn’t show on dark', dark: true },
		{ kind: 'wordmark', title: 'Wordmark', detail: 'The sign-in page and exported reports', dark: false },
		{ kind: 'wordmark_dark', title: 'Wordmark, dark themes', detail: 'Optional: the sign-in page in a dark theme', dark: true }
	];
	const urlOf = (kind: BrandLogo) => info[`${kind}_url` as const];
</script>

<svelte:head><title>Branding · Settings · {brand.windowName}</title></svelte:head>

<TopAppBar title="Branding" subtitle="Your company’s name and logos, where people see Castor" />

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-6 pt-2">
		<Card variant="outlined" class="flex flex-col gap-4 p-4">
			<h2 class="title-medium text-on-surface">Name</h2>
			<div class="grid gap-4 sm:grid-cols-2">
				<TextField label="Company or team name" bind:value={name} placeholder="Castor"
					supporting="In browser tabs and exports, instead of Castor" />
				<TextField label="Tagline (optional)" bind:value={tagline} placeholder="Offensive security" supporting="Under the name on the sign-in page" />
			</div>
			<Switch bind:checked={poweredBy} label="Show “Powered by Castor”" />
			<p class="-mt-2 body-small text-on-surface-variant">A small line on the sign-in page, and “(Castor)” after the name on exports, once the name or a logo is yours.</p>
		</Card>

		<section class="flex flex-col gap-3" aria-labelledby="logos">
			<h2 id="logos" class="title-medium text-on-surface">Logos</h2>
			<p class="-mt-1 body-medium text-on-surface-variant">SVG, PNG, WebP, JPEG or GIF, up to 512 KB, with a transparent background. Each one is shown on the background it’s for.</p>
			<div class="grid gap-3 sm:grid-cols-2">
				{#each SLOTS as slot (slot.kind)}
					{@const url = urlOf(slot.kind)}
					<Card variant="outlined" class="flex flex-col gap-3 p-3">
						<!-- A fixed light or dark backdrop (Castor's own surfaces), whatever the theme: how the logo looks there. -->
						<div class="flex h-24 items-center justify-center rounded-md border border-outline-variant p-3" style:background={slot.dark ? '#131318' : '#fbf8ff'}>
							{#if url}
								<img src={url} alt="" class="max-h-full max-w-full object-contain {slot.kind.startsWith('mark') ? 'size-16' : ''}" />
							{:else}
								<span class="body-small" style:color={slot.dark ? '#c7c5d0' : '#46464f'}>None</span>
							{/if}
						</div>
						<div class="flex items-start gap-2">
							<div class="min-w-0 flex-1">
								<p class="title-small text-on-surface">{slot.title}</p>
								<p class="body-small text-on-surface-variant">{slot.detail}</p>
							</div>
							<label class="shrink-0">
								<span class="state-layer focus-ring inline-flex h-10 cursor-pointer items-center gap-1.5 rounded-full px-3 label-large text-primary {busy === slot.kind ? 'opacity-60' : ''}">
									<Icon name="upload" size={18} />{url ? 'Replace' : 'Upload'}
								</span>
								<input type="file" class="sr-only" accept={ACCEPT} disabled={busy === slot.kind} onchange={(e) => upload(slot.kind, e.currentTarget)} />
							</label>
							{#if url}<IconButton icon="delete" label="Remove the {slot.title.toLowerCase()}" onclick={() => removeLogo(slot.kind)} />{/if}
						</div>
					</Card>
				{/each}
			</div>
		</section>

		<Card variant="outlined" class="flex flex-col gap-4 p-4">
			<h2 class="title-medium text-on-surface">Reports</h2>
			<p class="-mt-2 body-medium text-on-surface-variant">
				Timeline exports (a project’s Home → Export) carry the wordmark (or the square logo and name) at the top left and the project’s client’s logo at the top right.
			</p>
			<TextField label="Footer line (optional)" bind:value={footer} placeholder="Confidential: prepared for the client only"
				supporting="At the foot of every page of a PDF; empty: the project and “Timeline”" />
			<div class="flex flex-col gap-2">
				<span class="label-large text-on-surface">Client logos</span>
				{#if clients.length}
					<ul class="flex flex-col divide-y divide-outline-variant/60 rounded-md border border-outline-variant">
						{#each clients as c (c.id)}
							<li class="flex flex-wrap items-center gap-3 px-3 py-2">
								<span class="min-w-40 flex-1 truncate body-medium text-on-surface">{c.name}</span>
								{#each [{ v: 'light' as const, url: c.logo_url, label: 'Logo' }, { v: 'dark' as const, url: c.logo_dark_url, label: 'Dark' }] as slot (slot.v)}
									<span class="flex items-center gap-1">
										<span class="flex h-9 w-20 items-center justify-center rounded-sm border border-outline-variant p-1" style:background={slot.v === 'dark' ? '#131318' : '#fbf8ff'} title="{slot.label} logo">
											{#if slot.url}<img src={slot.url} alt="" class="max-h-full max-w-full object-contain" />{:else}<span class="label-small" style:color={slot.v === 'dark' ? '#c7c5d0' : '#46464f'}>{slot.label}</span>{/if}
										</span>
										<label title="Upload the {slot.v === 'dark' ? 'dark-theme ' : ''}logo of {c.name}">
											<span class="state-layer focus-ring inline-flex size-9 cursor-pointer items-center justify-center rounded-full text-on-surface-variant {busy === `${c.id}:${slot.v}` ? 'opacity-60' : ''}"><Icon name="upload" size={18} /></span>
											<input type="file" class="sr-only" accept={ACCEPT} onchange={(e) => clientLogo(c, slot.v, e.currentTarget)} />
										</label>
										{#if slot.url}<IconButton icon="close" size="sm" label="Remove the {slot.v === 'dark' ? 'dark-theme ' : ''}logo of {c.name}" onclick={() => clientLogo(c, slot.v, null)} />{/if}
									</span>
								{/each}
							</li>
						{/each}
					</ul>
				{:else}
					<p class="body-small text-on-surface-variant">No clients yet: a project’s settings make one.</p>
				{/if}
			</div>
		</Card>

		<div class="flex justify-end">
			<Button icon="check" loading={saving} disabled={!dirty} onclick={save}>Save</Button>
		</div>
	</div>
</div>
