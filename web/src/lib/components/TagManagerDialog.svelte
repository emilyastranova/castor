<script lang="ts">
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { loadTags, type TagSuggestion } from '$lib/tags';

	/** Rename a tag, or merge several into one: a project's card tags, or agent tags (which
	 *  also follow into the cards and runs that target agents by them). */
	interface Props {
		open: boolean;
		kind: 'cards' | 'agents';
		/** For card tags: the project whose cards change. */
		projectId?: string;
		onchanged?: () => void;
	}
	let { open = $bindable(false), kind, projectId, onchanged }: Props = $props();

	let tags = $state<TagSuggestion[]>([]);
	let loading = $state(false);
	let filter = $state('');
	let picked = $state<string[]>([]);
	let target = $state('');
	let saving = $state(false);
	let openedFor = false;

	async function reload() {
		loading = true;
		const all = await loadTags(kind, projectId);
		// Card tags: this project's only (renaming changes this project's cards).
		tags = kind === 'cards' ? all.filter((t) => (t.here ?? 0) > 0).map((t) => ({ ...t, count: t.here })) : all;
		loading = false;
	}
	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (!open) return;
		filter = '';
		picked = [];
		target = '';
		void reload();
	});

	let shown = $derived(filter.trim() ? tags.filter((t) => t.tag.toLowerCase().includes(filter.trim().toLowerCase())) : tags);
	const noun = $derived(kind === 'cards' ? 'card' : 'agent');
	const countOf = (tag: string) => tags.find((t) => t.tag === tag)?.count ?? 0;

	function toggle(tag: string) {
		const was = picked.includes(tag);
		picked = was ? picked.filter((t) => t !== tag) : [...picked, tag];
		// Start the new name from the most used of the picked tags (merging keeps it); a
		// single tag starts as itself, ready to edit.
		if (!was && (!target || picked.length === 1)) target = tag;
		else if (was && target === tag) target = [...picked].sort((a, b) => countOf(b) - countOf(a))[0] ?? '';
	}

	let cleanTarget = $derived(target.trim().replace(/^#+/, '').replace(/[\s,]+/g, ''));
	/** Renaming onto a tag that's already in use (and not one of those picked) merges into it. */
	let mergesInto = $derived(cleanTarget && !picked.includes(cleanTarget) ? tags.find((t) => t.tag === cleanTarget) : undefined);
	let sources = $derived(picked.filter((t) => t !== cleanTarget));
	let action = $derived(picked.length > 1 || mergesInto ? 'Merge' : 'Rename');
	let canSave = $derived(!!cleanTarget && sources.length > 0 && !saving);
	let summary = $derived.by(() => {
		if (!picked.length) return `Pick a tag to rename, or several to merge into one.`;
		if (!cleanTarget) return 'Give the new name.';
		if (!sources.length) return `${cleanTarget} is already that name.`;
		const uses = sources.reduce((n, t) => n + countOf(t), 0);
		const list = sources.join(', ');
		return mergesInto || picked.length > 1
			? `${list} → ${cleanTarget} on ${uses} ${noun}${uses === 1 ? '' : 's'}. A ${noun} that ends up with ${cleanTarget} twice keeps one.`
			: `${list} → ${cleanTarget} on ${uses} ${noun}${uses === 1 ? '' : 's'}.`;
	});

	async function save() {
		if (!canSave) return;
		saving = true;
		try {
			const body = { from: sources, to: cleanTarget };
			const res = kind === 'cards' ? await api().tags.renameCards(projectId!, body) : await api().tags.renameAgents(body);
			const c = res.changed;
			const parts =
				kind === 'cards'
					? [`${c.cards ?? 0} card${c.cards === 1 ? '' : 's'}`]
					: [`${c.agents ?? 0} agent${c.agents === 1 ? '' : 's'}`, c.cards ? `${c.cards} card${c.cards === 1 ? '' : 's'} targeting them` : '', c.tokens ? `${c.tokens} token${c.tokens === 1 ? '' : 's'}` : ''].filter(Boolean);
			snackbar.show(`${action === 'Merge' ? 'Merged' : 'Renamed'} to ${cleanTarget}: ${parts.join(', ')}`);
			picked = [];
			target = '';
			await reload();
			onchanged?.();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not change the tags');
		} finally {
			saving = false;
		}
	}
</script>

<Dialog bind:open title={kind === 'cards' ? 'Card tags' : 'Agent tags'} icon="sell" size="md">
	<div class="flex flex-col gap-3 pb-2">
		<p class="body-medium text-on-surface-variant">
			{#if kind === 'cards'}
				Rename a tag on this project's cards, or pick several to merge them into one.
			{:else}
				Rename an agent tag, or merge several. Cards and playbook runs that target agents by the tag follow it, so they keep matching. An agent started with
				<code class="font-mono">--tags</code> brings its own back when it reconnects: change them there too.
			{/if}
		</p>
		{#if tags.length > 8}
			<TextField label="Find a tag" bind:value={filter} leadingIcon="search" />
		{/if}
		<div class="max-h-80 overflow-y-auto rounded-md border border-outline-variant">
			{#if loading && !tags.length}
				<p class="px-4 py-6 text-center body-medium text-on-surface-variant">Loading…</p>
			{:else}
				<ul role="listbox" aria-multiselectable="true" aria-label="Tags">
					{#each shown as t (t.tag)}
						{@const on = picked.includes(t.tag)}
						<li role="option" aria-selected={on} class="border-b border-outline-variant/50 last:border-0">
							<button type="button" class="state-layer flex h-12 w-full items-center gap-3 px-3 text-left" onclick={() => toggle(t.tag)}>
								<Icon name={on ? 'check_box' : 'check_box_outline_blank'} size={20} filled={on} class={on ? 'text-primary' : 'text-on-surface-variant'} />
								<span class="inline-flex h-7 max-w-[60%] items-center truncate rounded-sm bg-secondary-container px-2.5 label-large text-on-secondary-container">{t.tag}</span>
								<span class="ml-auto shrink-0 body-small text-on-surface-variant">{t.count} {noun}{t.count === 1 ? '' : 's'}</span>
							</button>
						</li>
					{:else}
						<li class="px-4 py-6 text-center body-medium text-on-surface-variant">{filter ? `No tag matches “${filter}”.` : `No ${noun} has a tag yet.`}</li>
					{/each}
				</ul>
			{/if}
		</div>
		{#if picked.length}
			<TextField
				label={picked.length > 1 ? 'Merge into' : 'New name'}
				bind:value={target}
				supporting={mergesInto ? `${cleanTarget} is already a tag on ${mergesInto.count} ${noun}${mergesInto.count === 1 ? '' : 's'}: they'll be merged.` : ''}
				onkeydown={(e) => e.key === 'Enter' && (e.preventDefault(), void save())}
			/>
		{/if}
		<p class="body-small text-on-surface-variant" aria-live="polite">{summary}</p>
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Done</Button>
		<Button icon={action === 'Merge' ? 'merge' : 'edit'} loading={saving} disabled={!canSave} onclick={save}>{action}</Button>
	{/snippet}
</Dialog>
