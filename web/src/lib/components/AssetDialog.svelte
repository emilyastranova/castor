<script lang="ts">
	import type { Asset, AssetDetail, AssetRelation, AssetSource, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, IconButton, Select, TextField } from '$lib/m3';
	import { assetTypes, linkDetails as details, show } from '$lib/assetTypes.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { canEdit } from '$lib/session';
	import MarkdownEditor from './MarkdownEditor.svelte';
	import { people } from '$lib/people.svelte';
	import { PastedImages } from '$lib/pastedImages.svelte';
	import RichText from './RichText.svelte';
	import PluginActions from './PluginActions.svelte';

	/** One asset: every field with where its value came from (and other values tools
	 *  reported), what it's linked to, triggered runs and its history. */
	interface Props {
		open: boolean;
		assetId: UUID | null;
		projectId: UUID;
		onopenasset: (id: UUID) => void;
		/** Show this asset in the relationships graph. */
		onshowgraph?: (id: UUID) => void;
		onchanged?: () => void;
	}
	let { open = $bindable(false), assetId, projectId, onopenasset, onshowgraph, onchanged }: Props = $props();

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
	/** A relationship's details in a few words: "smb · port 445 · admin". */

	const linkSource = (l: AssetRelation) =>
		l.source === 'manual' ? `noted by ${l.by ?? 'someone'}` : l.source === 'parser' ? 'from a tool’s results' : `from ${l.where}`;

	// ---- relationships: add one by hand, remove one ----
	let relating = $state(false);
	let relation = $state('');
	let relateTo = $state('');
	let relateNote = $state('');
	let candidates = $state<Asset[]>([]);
	let relations = $derived(asset ? assetTypes.relationsOf(asset.type) : []);
	let chosen = $derived(relations.find((r) => `${r.direction}:${r.kind}` === relation));
	let targets = $derived(
		candidates
			.filter((a) => a.id !== asset?.id && (!chosen?.others.length || chosen.others.includes(a.type)))
			.map((a) => ({ value: a.id, label: a.label, group: assetTypes.get(a.type)?.plural ?? a.type }))
	);
	async function startRelating() {
		relating = !relating;
		if (!relating) return;
		relation = relations[0] ? `${relations[0].direction}:${relations[0].kind}` : '';
		relateTo = relateNote = '';
		try {
			candidates = (await api().assets.list(projectId, { limit: 1000 })).items;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load the assets');
		}
	}
	async function relate() {
		if (!asset || !chosen || !relateTo) return;
		try {
			await api().assets.link(asset.id, { kind: chosen.kind, direction: chosen.direction, to_id: relateTo, note: relateNote.trim() });
			relating = false;
			await load(asset.id);
			onchanged?.();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not add the relationship');
		}
	}
	async function unrelate(l: AssetRelation) {
		if (!asset) return;
		const ok = await confirmAction({
			title: 'Remove this relationship?',
			message: `${asset.label} ${l.label} ${l.asset.label}. A tool that reports it again adds it back.`,
			confirmLabel: 'Remove'
		});
		if (!ok) return;
		try {
			await api().assets.unlink(l.id);
			await load(asset.id);
			onchanged?.();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove it');
		}
	}
	const sourceText = (s: AssetSource) =>
		s.kind === 'parser'
			? (s.parser?.startsWith('lines:') ? 'a tool’s output' : (s.parser ?? 'a tool'))
			: s.kind === 'import'
				? `imported${s.by ? ` by ${s.by}` : ''}`
				: s.kind === 'manual'
					? `set by ${s.by ?? 'someone'}`
					: `${s.where}${s.by ? ` by ${s.by}` : ''}`;
	/** How sure Castor is, in words. */
	const sureness = (c: number) => (c >= 100 ? 'Confirmed by a person' : c >= 80 ? 'From a tool’s results' : c >= 45 ? 'Mentioned' : 'Mentioned in output');

	// ---- suggested: confirm or dismiss ----
	async function decide(confirm: boolean) {
		if (!asset) return;
		try {
			if (confirm) await api().assets.confirm(asset.id);
			else await api().assets.dismiss(asset.id);
			await load(asset.id);
			onchanged?.();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not do that');
		}
	}

	// ---- its description (markdown) ----
	// Pictures pasted into it are stored on the project, like a pinned note's.
	const images = new PastedImages(async (file) => api().attachments.url((await api().notes.uploadImage(projectId, file)).id, true));
	let describing = $state(false);
	let descriptionDraft = $state('');
	async function saveDescription(text: string) {
		if (!asset) return;
		await api().assets.describe(asset.id, text);
		describing = false;
		await load(asset.id);
		onchanged?.();
	}

	// ---- outside the scope: add it (the address or its network, the name or its domain) ----
	const isIp = (v: string) => /^\d{1,3}(\.\d{1,3}){3}$/.test(v);
	let scopeChoices = $derived.by(() => {
		if (!asset) return [] as { entry: string; label: string }[];
		const f = asset.fields as Record<string, unknown>;
		const names = new Set<string>();
		for (const v of [f.ip, f.hostname, f.host, asset.type === 'domain' ? f.name : null]) if (typeof v === 'string' && v) names.add(v.toLowerCase());
		if (asset.type === 'url' && typeof f.url === 'string') {
			try {
				names.add(new URL(f.url).hostname.toLowerCase());
			} catch {
				/* not a URL */
			}
		}
		const out: { entry: string; label: string }[] = [];
		for (const n of names) {
			if (isIp(n)) {
				out.push({ entry: n, label: `${n} (just this address)` });
				out.push({ entry: `${n.split('.').slice(0, 3).join('.')}.0/24`, label: `${n.split('.').slice(0, 3).join('.')}.0/24 (its network)` });
			} else if (n.includes('.')) {
				out.push({ entry: n, label: `${n} and its subdomains` });
				const parent = n.split('.').slice(1).join('.');
				if (parent.includes('.')) out.push({ entry: parent, label: `${parent} (the whole domain)` });
			} else {
				out.push({ entry: n, label: n });
			}
		}
		return out.filter((c, i) => out.findIndex((x) => x.entry === c.entry) === i);
	});
	let scopeOpen = $state(false);
	let scopeEntry = $state('');
	let scopeGroup = $state('');
	let newGroup = $state('');
	let groupNames = $state<string[]>([]);
	async function openScope() {
		scopeOpen = !scopeOpen;
		if (!scopeOpen) return;
		scopeEntry = scopeChoices[0]?.entry ?? '';
		newGroup = '';
		try {
			groupNames = (await api().assets.scope(projectId)).groups.map((g) => g.name);
		} catch {
			groupNames = [];
		}
		scopeGroup = groupNames[0] ?? '__new';
		if (!groupNames.length) newGroup = 'Scope';
	}
	async function addToScope() {
		if (!asset || !scopeEntry) return;
		const name = (scopeGroup === '__new' ? newGroup : scopeGroup).trim() || 'Scope';
		try {
			await api().assets.addToScope(projectId, scopeEntry, name);
			await load(asset.id);
			scopeOpen = false;
			onchanged?.();
			if (asset.in_scope) snackbar.show(`Added ${scopeEntry} to ${name}`);
			else snackbar.error(`Added ${scopeEntry} to ${name}, but ${asset.label} is still outside: an exclusion covers it (Edit scope)`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not change the scope');
		}
	}

	// ---- the same thing seen twice: merge ----
	let mergeOpen = $state(false);
	let mergeOptions = $state<Asset[]>([]);
	let mergeInto = $state('');
	async function startMerge() {
		if (!asset) return;
		try {
			const res = await api().assets.list(projectId, { type: asset.type, limit: 1000 });
			mergeOptions = res.items.filter((a) => a.id !== asset!.id);
			mergeInto = '';
			mergeOpen = true;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load the assets');
		}
	}
	async function merge() {
		if (!asset || !mergeInto) return;
		try {
			const into = await api().assets.merge(asset.id, mergeInto);
			mergeOpen = false;
			snackbar.show(`Merged into ${into.label}`);
			onchanged?.();
			onopenasset(into.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not merge');
		}
	}

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
					<span class="flex items-center gap-1 rounded-full bg-primary-container px-2.5 py-0.5 label-medium text-on-primary-container" title="In the project's scope">
						<Icon name="verified" size={14} />In scope{asset.scope_groups.length ? `: ${asset.scope_groups.join(', ')}` : ''}
					</span>
				{:else if canEdit() && scopeChoices.length}
					<button type="button" class="state-layer focus-ring flex items-center gap-1 rounded-full border border-outline-variant px-2.5 py-0.5 label-medium" aria-expanded={scopeOpen} onclick={openScope}>
						<Icon name="block" size={14} />Outside scope<span class="text-primary">· Add to scope</span><Icon name={scopeOpen ? 'arrow_drop_up' : 'arrow_drop_down'} size={16} />
					</button>
				{:else}
					<span class="flex items-center gap-1 rounded-full border border-outline-variant px-2.5 py-0.5 label-medium"><Icon name="block" size={14} />Outside scope</span>
				{/if}
				<span class="flex items-center gap-1 rounded-full border border-outline-variant px-2.5 py-0.5 label-medium" title="How sure Castor is: {asset.confidence}/100">
					<Icon name={asset.confidence >= 80 ? 'task_alt' : 'help'} size={14} />{sureness(asset.confidence)}
				</span>
				<span title={absoluteTime(asset.first_seen)}>first seen {relativeTime(asset.first_seen)}</span>·
				<span title={absoluteTime(asset.last_seen)}>last seen {relativeTime(asset.last_seen)}</span>
			</div>

			{#if scopeOpen && !asset.in_scope}
				<section class="flex flex-col gap-3 rounded-md border border-outline-variant bg-surface-container px-3 py-3" aria-label="Add to the scope">
					<fieldset class="flex flex-col gap-1">
						<legend class="mb-1 label-large text-on-surface">Add</legend>
						{#each scopeChoices as c (c.entry)}
							<label class="flex cursor-pointer items-center gap-2 body-medium text-on-surface">
								<input type="radio" name="scope-entry" value={c.entry} bind:group={scopeEntry} class="accent-primary" />{c.label}
							</label>
						{/each}
					</fieldset>
					<div class="flex flex-wrap items-end gap-2">
						<Select label="To the group" bind:value={scopeGroup} class="min-w-40 flex-1" options={[...groupNames.map((n) => ({ value: n, label: n })), { value: '__new', label: 'A new group…' }]} />
						{#if scopeGroup === '__new'}<TextField label="New group" bind:value={newGroup} class="min-w-40 flex-1" placeholder="e.g. External, OT" />{/if}
						<Button icon="add" disabled={!scopeEntry || (scopeGroup === '__new' && !newGroup.trim())} onclick={addToScope}>Add to scope</Button>
					</div>
				</section>
			{/if}

			{#if asset.status !== 'confirmed'}
				<div class="flex flex-wrap items-center gap-2 rounded-md bg-tertiary-container/40 px-3 py-2.5 body-medium text-on-surface">
					<Icon name={asset.status === 'dismissed' ? 'block' : 'lightbulb'} size={18} class="text-tertiary" />
					<span class="min-w-0 flex-1">
						{asset.status === 'dismissed'
							? 'Dismissed: it isn’t in the inventory, and mentions don’t bring it back.'
							: 'Suggested: only mentioned so far. It joins the inventory, arguments and triggers once confirmed.'}
					</span>
					{#if canEdit()}
						{#if asset.status === 'suggested'}<Button variant="text" onclick={() => decide(false)}>Dismiss</Button>{/if}
						<Button variant="tonal" icon="check" onclick={() => decide(true)}>Confirm</Button>
					{/if}
				</div>
			{/if}

			<section>
				<div class="mb-1.5 flex items-center gap-2">
					<h3 class="flex-1 label-large text-on-surface-variant">Description</h3>
					{#if canEdit() && !describing}
						<Button variant="text" icon="edit" onclick={() => { descriptionDraft = asset?.description ?? ''; describing = true; }}>{asset.description ? 'Edit' : 'Add'}</Button>
					{/if}
				</div>
				{#if describing}
					<MarkdownEditor
						bind:value={descriptionDraft}
						label="Description"
						{projectId}
						mentionables={() => people.mentionables(projectId)}
						{images}
						placeholder="What it is, who owns it, why it matters…"
						submitLabel="Save"
						submitIcon="check"
						minRows={4}
						onsubmit={saveDescription}
						oncancel={() => (describing = false)}
						autofocus
					/>
				{:else if asset.description.trim()}
					<RichText text={asset.description} {projectId} class="rounded-md bg-surface-container px-3 py-2 body-medium text-on-surface" />
				{:else}
					<p class="body-small text-on-surface-variant italic">No description.</p>
				{/if}
			</section>

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

			{#if asset.attachment}
				<a href={api().attachments.url(asset.attachment.id)} class="state-layer flex w-fit items-center gap-2 rounded-md border border-outline-variant px-3 py-2 body-medium text-on-surface">
					<Icon name="download" size={18} class="text-primary" />Downloaded copy: {asset.attachment.filename}
				</a>
			{/if}

			<section aria-label="Relationships">
				<div class="mb-1.5 flex items-center gap-2">
					<h3 class="flex-1 label-large text-on-surface-variant">Relationships</h3>
					{#if onshowgraph && asset.links.length}
						<Button variant="text" icon="hub" onclick={() => onshowgraph(asset!.id)}>Graph</Button>
					{/if}
					{#if canEdit() && asset.status === 'confirmed' && relations.length}
						<Button variant="text" icon={relating ? 'close' : 'add_link'} aria-expanded={relating} onclick={startRelating}>{relating ? 'Cancel' : 'Add'}</Button>
					{/if}
				</div>
				{#if relating}
					<form class="mb-3 flex flex-col gap-3 rounded-md border border-outline-variant bg-surface-container px-3 py-3" onsubmit={(e) => { e.preventDefault(); void relate(); }}>
						<div class="flex flex-wrap items-end gap-2">
							<Select
								label="{asset.label} …"
								bind:value={relation}
								onchange={() => (relateTo = '')}
								class="min-w-44 flex-1"
								options={relations.map((r) => ({
									value: `${r.direction}:${r.kind}`,
									label: r.label,
									description: r.others.length ? r.others.map((t) => assetTypes.get(t)?.label.toLowerCase() ?? t).join(', ') : 'anything',
									group: r.kind === 'related' ? 'Other' : undefined
								}))}
							/>
							<Select label="…this asset" bind:value={relateTo} class="min-w-56 flex-[2]" options={targets} supporting={targets.length ? undefined : 'No asset of the right type yet'} disabled={!targets.length} />
						</div>
						<TextField label="Note (optional)" bind:value={relateNote} placeholder="How you know, what you did with it…" />
						<div class="flex justify-end"><Button type="submit" icon="add_link" disabled={!chosen || !relateTo}>Add relationship</Button></div>
					</form>
				{/if}
				{#if linkGroups.length}
					<div class="flex flex-col gap-3">
						{#each linkGroups as [label, links] (label)}
							<div>
								<p class="mb-1 body-small font-medium text-on-surface-variant">{asset.label} <span class="text-on-surface">{label}</span></p>
								<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md bg-surface-container">
									{#each links as l (l.id)}
										<li class="flex items-start gap-2 px-3 py-2">
											<Icon name={assetTypes.get(l.asset.type)?.icon ?? 'radar'} size={18} class="mt-0.5 shrink-0 text-on-surface-variant" />
											<div class="min-w-0 flex-1">
												<button type="button" class="text-left body-medium break-all text-on-surface hover:text-primary hover:underline" onclick={() => onopenasset(l.asset.id)}>{l.asset.label}</button>
												<p class="body-small text-on-surface-variant">
													{#if details(l.fields)}<span class="font-medium text-on-surface">{details(l.fields)}</span> · {/if}{linkSource(l)}{#if l.task}{' '}on <a href={l.task.link} class="text-primary hover:underline">{l.task.title}</a>{/if}
													· <span title={absoluteTime(l.first_seen)}>{relativeTime(l.first_seen)}</span>
												</p>
												{#if l.note}<p class="mt-0.5 body-small text-on-surface italic">{l.note}</p>{/if}
											</div>
											{#if canEdit()}<IconButton icon="link_off" label="Remove this relationship" size="sm" onclick={() => unrelate(l)} />{/if}
										</li>
									{/each}
								</ul>
							</div>
						{/each}
					</div>
				{:else if !relating}
					<p class="body-small text-on-surface-variant italic">None yet. Tools add them (a login that worked, a file taken from a share), or add one.</p>
				{/if}
			</section>

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
								{#if h.source.link && !['parser', 'manual', 'import'].includes(h.source.kind)}<a href={h.source.link} class="text-primary hover:underline">{sourceText(h.source)}</a>{:else}{sourceText(h.source)}{/if}{#if h.source.task}{' '}on <a href={h.source.task.link} class="text-primary hover:underline">{h.source.task.title}</a>{/if} · {relativeTime(h.source.at)}
							</p>
							{#if h.source.excerpt}<p class="mb-0.5 text-on-surface-variant italic">“{h.source.excerpt}”</p>{/if}
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
		{#if asset && canEdit()}
			<Button variant="text" icon="delete" class="text-error!" onclick={remove}>Remove</Button>
			<Button variant="text" icon="merge" onclick={startMerge}>Same as…</Button>
			{#if asset.status === 'confirmed'}<PluginActions {projectId} place="asset" assetId={asset.id} assetType={asset.type} />{/if}
			<span class="mr-auto"></span>
		{/if}
		<Button variant="text" onclick={() => (open = false)}>Close</Button>
	{/snippet}
</Dialog>

<Dialog bind:open={mergeOpen} title="The same {type?.label.toLowerCase() ?? 'asset'} as…" icon="merge" size="sm">
	<div class="flex flex-col gap-3 pt-1 pb-2">
		<p class="body-medium text-on-surface-variant">
			{asset?.label} is folded into the one you pick: its history, links and values move over, and it’s removed. Use it for the same machine seen by name and by address, say.
		</p>
		{#if mergeOptions.length}
			<Select label="Merge into" bind:value={mergeInto} options={[{ value: '', label: 'Pick one' }, ...mergeOptions.map((a) => ({ value: a.id, label: a.label }))]} />
		{:else}
			<p class="body-medium text-on-surface-variant">There’s no other {type?.label.toLowerCase() ?? 'asset'} in the inventory to merge it with.</p>
		{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (mergeOpen = false)}>Cancel</Button>
		<Button icon="merge" disabled={!mergeInto} onclick={merge}>Merge</Button>
	{/snippet}
</Dialog>
