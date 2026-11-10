<script lang="ts">
	import type { AssetDetail, AssetSource, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, IconButton, TextField } from '$lib/m3';
	import { assetTypes, show } from '$lib/assetTypes.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { canEdit } from '$lib/session';

	/** One asset: every field with where its value came from (and other values tools
	 *  reported), what it's linked to, triggered runs and its history. */
	interface Props {
		open: boolean;
		assetId: UUID | null;
		onopenasset: (id: UUID) => void;
		onchanged?: () => void;
	}
	let { open = $bindable(false), assetId, onopenasset, onchanged }: Props = $props();

	let asset = $state<AssetDetail | null>(null);
	let secret = $state<string | null>(null);
	async function load(id: UUID) {
		try {
			asset = await api().assets.get(id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load the asset');
			open = false;
		}
	}
	$effect(() => {
		secret = null;
		if (open && assetId) void load(assetId);
	});
	$effect(() => void assetTypes.load());

	let type = $derived(asset ? assetTypes.get(asset.type) : undefined);
	/** Core fields in their order, then anything extra a tool reported. */
	let rows = $derived.by(() => {
		if (!asset) return [];
		const core = type?.fields ?? [];
		const names = [...core.map((f) => f.name), ...Object.keys(asset.fields).filter((k) => !core.some((f) => f.name === k))];
		return names
			.filter((n) => asset!.fields[n] !== undefined || n === 'fingerprint')
			.map((n) => ({ name: n, label: core.find((f) => f.name === n)?.label ?? n, value: asset!.fields[n], seen: asset!.provenance[n] ?? [] }));
	});
	let linkGroups = $derived.by(() => {
		const out: Record<string, AssetDetail['links']> = {};
		for (const l of asset?.links ?? []) (out[l.label] ??= []).push(l);
		return Object.entries(out);
	});
	const sourceText = (s: AssetSource) =>
		s.kind === 'parser' ? (s.parser ?? 'a tool') : s.kind === 'import' ? `imported${s.by ? ` by ${s.by}` : ''}` : `set by ${s.by ?? 'someone'}`;

	// ---- editing extra fields ----
	let newKey = $state('');
	let newValue = $state('');
	async function setField() {
		if (!asset || !newKey.trim()) return;
		try {
			await api().assets.update(asset.id, { [newKey.trim()]: newValue });
			newKey = newValue = '';
			await load(asset.id);
			onchanged?.();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save');
		}
	}
	async function remove() {
		if (!asset) return;
		const ok = await confirmAction({ title: `Remove ${asset.label}?`, message: 'The asset, its history and its links are removed from the project. Tools that find it again add it back.', confirmLabel: 'Remove' });
		if (!ok) return;
		await api().assets.remove(asset.id);
		open = false;
		onchanged?.();
	}
	async function reveal() {
		if (!asset) return;
		try {
			secret = (await api().assets.reveal(asset.id)).secret;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not reveal');
		}
	}
	const FIRE: Record<string, string> = { started: 'text-primary', waiting: 'text-tertiary', failed: 'text-error', limited: 'text-on-surface-variant', skipped: 'text-on-surface-variant' };
</script>

<Dialog bind:open title={asset?.label ?? 'Asset'} icon={type?.icon ?? 'radar'} size="md">
	{#if asset}
		<div class="flex flex-col gap-5 pt-1 pb-2">
			<div class="flex flex-wrap items-center gap-2 body-small text-on-surface-variant">
				<span class="rounded-full bg-secondary-container px-2.5 py-0.5 label-medium text-on-secondary-container">{type?.label ?? asset.type}</span>
				{#if asset.in_scope}
					<span class="flex items-center gap-1 rounded-full bg-primary-container px-2.5 py-0.5 label-medium text-on-primary-container"><Icon name="verified" size={14} />In scope</span>
				{:else}
					<span class="flex items-center gap-1 rounded-full border border-outline-variant px-2.5 py-0.5 label-medium"><Icon name="block" size={14} />Outside scope</span>
				{/if}
				<span title={absoluteTime(asset.first_seen)}>first seen {relativeTime(asset.first_seen)}</span>·
				<span title={absoluteTime(asset.last_seen)}>last seen {relativeTime(asset.last_seen)}</span>
			</div>

			<!-- fields, each with where it came from -->
			<section>
				<h3 class="mb-1.5 label-large text-on-surface-variant">What's known</h3>
				<dl class="divide-y divide-outline-variant/50 rounded-md bg-surface-container">
					{#each rows as r (r.name)}
						<div class="grid grid-cols-[9rem_minmax(0,1fr)] gap-3 px-3 py-2">
							<dt class="body-small text-on-surface-variant">{r.label}</dt>
							<dd class="min-w-0">
								<span class="body-medium break-words text-on-surface {typeof r.value === 'boolean' && r.name === 'smb_signing' && !r.value ? 'font-medium text-error' : ''}">{show(r.value)}</span>
								{#if r.seen[0]}
									<span class="block body-small text-on-surface-variant">
										from {sourceText(r.seen[0].source)}{#if r.seen[0].source.task}{' '}on <a href={r.seen[0].source.task.link} class="text-primary hover:underline">{r.seen[0].source.task.title}</a>{/if}
										· <span title={absoluteTime(r.seen[0].source.at)}>{relativeTime(r.seen[0].source.at)}</span>
									</span>
								{/if}
								{#each r.seen.slice(1, 4) as other, i (i)}
									<span class="block body-small text-tertiary" title="Another value a tool reported">
										also seen: <span class="font-medium">{show(other.value)}</span> from {sourceText(other.source)} · {relativeTime(other.source.at)}
									</span>
								{/each}
							</dd>
						</div>
					{/each}
					{#if asset.has_secret}
						<div class="grid grid-cols-[9rem_minmax(0,1fr)] items-center gap-3 px-3 py-2">
							<dt class="body-small text-on-surface-variant">Secret</dt>
							<dd class="flex items-center gap-2">
								<span class="font-mono body-medium text-on-surface">{secret ?? '••••••••'}</span>
								{#if canEdit() && !secret}<Button variant="text" icon="visibility" onclick={reveal}>Reveal</Button>{/if}
								{#if secret}<IconButton icon="content_copy" label="Copy" size="sm" onclick={() => navigator.clipboard.writeText(secret ?? '')} />{/if}
							</dd>
						</div>
					{/if}
				</dl>
				{#if canEdit()}
					<form class="mt-2 flex items-end gap-2" onsubmit={(e) => { e.preventDefault(); void setField(); }}>
						<TextField label="Field" bind:value={newKey} class="w-40 [&_div]:h-10" placeholder="e.g. owner" />
						<TextField label="Value" bind:value={newValue} class="flex-1 [&_div]:h-10" />
						<Button variant="tonal" type="submit" icon="add" disabled={!newKey.trim()}>Set</Button>
					</form>
				{/if}
			</section>

			{#if linkGroups.length}
				<section>
					<h3 class="mb-1.5 label-large text-on-surface-variant">Linked</h3>
					<div class="flex flex-col gap-2">
						{#each linkGroups as [label, links] (label)}
							<div>
								<p class="mb-1 body-small text-on-surface-variant">{label}</p>
								<div class="flex flex-wrap gap-1.5">
									{#each links as l (l.asset.id)}
										<button type="button" class="state-layer flex items-center gap-1.5 rounded-full border border-outline-variant px-2.5 py-1 body-small text-on-surface" onclick={() => onopenasset(l.asset.id)}>
											<Icon name={assetTypes.get(l.asset.type)?.icon ?? 'radar'} size={16} />{l.asset.label}
										</button>
									{/each}
								</div>
							</div>
						{/each}
					</div>
				</section>
			{/if}

			{#if asset.triggers.length}
				<section>
					<h3 class="mb-1.5 label-large text-on-surface-variant">Triggered runs</h3>
					<ul class="flex flex-col gap-1">
						{#each asset.triggers as f (f.id)}
							<li class="flex items-center gap-2 body-small">
								<span class="font-medium {FIRE[f.status] ?? ''}">{f.status}</span>
								<span class="text-on-surface">{f.trigger}</span>
								{#if f.reason}<span class="truncate text-on-surface-variant">· {f.reason}</span>{/if}
								<span class="ml-auto shrink-0 text-on-surface-variant">{relativeTime(f.at)}</span>
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			<details class="group">
				<summary class="cursor-pointer label-large text-on-surface-variant">History ({asset.history.length})</summary>
				<ol class="mt-2 flex flex-col gap-2">
					{#each asset.history as h, i (i)}
						<li class="rounded-md bg-surface-container px-3 py-2 body-small">
							<p class="text-on-surface-variant">
								{sourceText(h.source)}{#if h.source.task}{' '}on <a href={h.source.task.link} class="text-primary hover:underline">{h.source.task.title}</a>{/if} · {relativeTime(h.source.at)}
							</p>
							<p class="font-mono text-[12px] break-words text-on-surface">{Object.entries(h.fields).map(([k, v]) => `${k}=${show(v)}`).join('  ')}</p>
						</li>
					{/each}
				</ol>
			</details>
		</div>
	{:else}
		<p class="py-6 body-medium text-on-surface-variant">Loading…</p>
	{/if}
	{#snippet actions()}
		{#if asset && canEdit()}<Button variant="text" icon="delete" class="mr-auto text-error!" onclick={remove}>Remove</Button>{/if}
		<Button variant="text" onclick={() => (open = false)}>Close</Button>
	{/snippet}
</Dialog>
