<script lang="ts">
	import type { VariableKind, VariableSet } from '$lib/api';
	import { Button, Card, Chip, Icon, Tabs, TopAppBar } from '$lib/m3';
	import VariableSetDialog from '$lib/components/VariableSetDialog.svelte';
	import VariableKindDialog from '$lib/components/VariableKindDialog.svelte';
	import { FIELD_TYPES, fieldLabel, fieldSummary, isSecret, kindIcon } from '$lib/variables';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let kinds = $state<VariableKind[]>(data.kinds);
	// svelte-ignore state_referenced_locally
	let sets = $state<VariableSet[]>(data.sets);
	// svelte-ignore state_referenced_locally
	const projects = data.projects;

	let tab = $state('sets');
	let filter = $state('');

	let setDialogOpen = $state(false);
	let editingSet = $state<VariableSet | null>(null);
	let newSetKind = $state('');
	let kindDialogOpen = $state(false);
	let editingKind = $state<VariableKind | null>(null);

	const projectCodes = (ids: string[]) => ids.map((id) => projects.find((p) => p.id === id)?.codename ?? 'deleted project');

	function openSet(set: VariableSet | null, kindId = '') {
		editingSet = set;
		newSetKind = kindId;
		setDialogOpen = true;
	}
	function openKind(kind: VariableKind | null) {
		editingKind = kind;
		kindDialogOpen = true;
	}

	function setSaved(saved: VariableSet) {
		const isNew = !sets.some((s) => s.id === saved.id);
		sets = [...sets.filter((s) => s.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name));
		if (isNew) kinds = kinds.map((k) => (k.id === saved.kind_id ? { ...k, set_count: k.set_count + 1 } : k));
	}
	function setDeleted(id: string) {
		const gone = sets.find((s) => s.id === id);
		sets = sets.filter((s) => s.id !== id);
		if (gone) kinds = kinds.map((k) => (k.id === gone.kind_id ? { ...k, set_count: k.set_count - 1 } : k));
	}
	function kindSaved(saved: VariableKind) {
		kinds = [...kinds.filter((k) => k.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name));
	}

	let visibleSets = $derived(
		sets.filter((s) => {
			const q = filter.trim().toLowerCase();
			return !q || s.name.toLowerCase().includes(q) || s.kind_id.includes(q) || s.description.toLowerCase().includes(q);
		})
	);
	// Group by kind, in kind order; kinds with no matching sets are left out.
	let groups = $derived(
		kinds.map((k) => ({ kind: k, sets: visibleSets.filter((s) => s.kind_id === k.id) })).filter((g) => g.sets.length > 0)
	);

	const tabs = $derived([
		{ id: 'sets', label: 'Sets', icon: 'key', badge: sets.length },
		{ id: 'kinds', label: 'Kinds', icon: 'category', badge: kinds.length }
	]);
</script>

<svelte:head><title>Variables · Castor</title></svelte:head>

<TopAppBar title="Variables" subtitle="Credentials, keys, listeners and other values commands can use">
	{#snippet actions()}
		{#if tab === 'sets'}
			<label class="hidden items-center gap-2 rounded-full bg-surface-container-high px-4 md:flex">
				<Icon name="search" size={20} class="text-on-surface-variant" />
				<input class="h-10 w-44 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter sets" bind:value={filter} autocomplete="off" />
			</label>
			<Button icon="add" class="ml-2" onclick={() => openSet(null)}>New set</Button>
		{:else}
			<Button icon="add" onclick={() => openKind(null)}>New kind</Button>
		{/if}
	{/snippet}
</TopAppBar>

<div class="px-6"><Tabs {tabs} bind:value={tab} /></div>

<div class="flex-1 overflow-auto px-6 pt-4 pb-8">
	{#if tab === 'sets'}
		{#each groups as g (g.kind.id)}
			<section class="mb-6">
				<div class="mb-2 flex items-center gap-2">
					<Icon name={kindIcon(g.kind.id)} size={20} class="text-primary" />
					<h2 class="title-medium text-on-surface">{g.kind.name}</h2>
					<span class="font-mono body-small text-on-surface-variant">{g.kind.id}</span>
					<span class="flex-1"></span>
					<Button variant="text" icon="add" onclick={() => openSet(null, g.kind.id)}>Add {g.kind.name.toLowerCase()}</Button>
				</div>
				<div class="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3">
					{#each g.sets as s (s.id)}
						<Card variant="elevated" interactive class="flex flex-col gap-3 p-4" role="button" tabindex={0} onclick={() => openSet(s)} onkeydown={(e) => e.key === 'Enter' && openSet(s)}>
							<div class="flex items-start gap-2">
								<div class="min-w-0 flex-1">
									<h3 class="truncate title-small text-on-surface">{s.name}</h3>
									{#if s.description}<p class="line-clamp-1 body-small text-on-surface-variant">{s.description}</p>{/if}
								</div>
								{#if s.missing.length}
									<span class="flex items-center gap-1 rounded-full bg-error-container px-2 py-0.5 label-small text-on-error-container" title="Required fields without a value: {s.missing.join(', ')}">
										<Icon name="warning" size={14} />Incomplete
									</span>
								{/if}
							</div>
							<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 body-small">
								{#each g.kind.fields as f (f.name)}
									{@const v = fieldSummary(s, f)}
									<dt class="text-on-surface-variant">{fieldLabel(f)}</dt>
									<dd class="flex min-w-0 items-center gap-1 font-mono {v ? 'text-on-surface' : 'text-on-surface-variant/60'}">
										{#if isSecret(f.type) && v}<Icon name="lock" size={12} class="shrink-0 text-on-surface-variant" />{/if}
										<span class="truncate">{v ?? '—'}</span>
									</dd>
								{/each}
							</dl>
							<div class="flex flex-wrap gap-1">
								{#if s.project_ids.length === 0}
									<span class="rounded-full bg-surface-container-highest px-2 py-0.5 label-small text-on-surface-variant">All projects</span>
								{:else}
									{#each projectCodes(s.project_ids) as code (code)}
										<span class="rounded-full bg-secondary-container px-2 py-0.5 label-small text-on-secondary-container">{code}</span>
									{/each}
								{/if}
							</div>
						</Card>
					{/each}
				</div>
			</section>
		{:else}
			<div class="mx-auto flex max-w-lg flex-col items-center gap-3 py-16 text-center">
				<Icon name="key" size={48} class="text-on-surface-variant/50" />
				<p class="title-medium text-on-surface">{sets.length ? 'No sets match' : 'No variable sets yet'}</p>
				<p class="body-medium text-on-surface-variant">
					A set holds the values for one kind: a credential, an SSH key, a listener. Commands declare which kinds they need, and you
					choose a set on the card or when you run a playbook. Secrets and files are encrypted and only sent to the agent running the job.
				</p>
				{#if !sets.length}<Button icon="add" onclick={() => openSet(null)}>New set</Button>{/if}
			</div>
		{/each}
	{:else}
		<div class="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4">
			{#each kinds as k (k.id)}
				<Card variant="elevated" interactive class="flex flex-col gap-3 p-5" role="button" tabindex={0} onclick={() => openKind(k)} onkeydown={(e) => e.key === 'Enter' && openKind(k)}>
					<div class="flex items-start gap-3">
						<span class="flex size-10 items-center justify-center rounded-md bg-primary-container text-on-primary-container"><Icon name={kindIcon(k.id)} /></span>
						<div class="min-w-0 flex-1">
							<h2 class="truncate title-medium text-on-surface">{k.name}</h2>
							<p class="truncate font-mono text-[12px] text-on-surface-variant">{k.id}</p>
						</div>
						<span class="rounded-full px-2 py-0.5 label-small {k.origin === 'builtin' ? 'bg-surface-container-highest text-on-surface-variant' : 'bg-tertiary-container text-on-tertiary-container'}">{k.origin === 'builtin' ? 'built-in' : 'yours'}</span>
					</div>
					{#if k.description}<p class="line-clamp-2 body-medium text-on-surface-variant">{k.description}</p>{/if}
					<div class="flex flex-wrap gap-1">
						{#each k.fields as f (f.name)}
							<Chip variant="suggestion" icon={isSecret(f.type) ? 'lock' : undefined}>
								{f.name}<span class="ml-1 text-on-surface-variant">{FIELD_TYPES.find((t) => t.value === f.type)?.label.toLowerCase()}</span>
							</Chip>
						{/each}
					</div>
					<p class="body-small text-on-surface-variant">{k.set_count} set{k.set_count === 1 ? '' : 's'}</p>
				</Card>
			{/each}
		</div>
	{/if}
</div>

<VariableSetDialog bind:open={setDialogOpen} set={editingSet} {kinds} {projects} kindId={newSetKind} onsaved={setSaved} ondeleted={setDeleted} />
<VariableKindDialog bind:open={kindDialogOpen} kind={editingKind} onsaved={kindSaved} ondeleted={(id) => (kinds = kinds.filter((k) => k.id !== id))} />
