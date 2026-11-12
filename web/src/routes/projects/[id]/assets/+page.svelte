<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { Asset, AssetTrigger, AssetTriggerFire, AssetVerdict, Scope, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, IconButton, Select, Switch, Tabs, TextField, TopAppBar } from '$lib/m3';
	import ProjectSwitcher from '$lib/components/ProjectSwitcher.svelte';
	import AssetDialog from '$lib/components/AssetDialog.svelte';
	import TriggerDialog from '$lib/components/TriggerDialog.svelte';
	import AssetRelationships from '$lib/components/AssetRelationships.svelte';
	import PluginActions from '$lib/components/PluginActions.svelte';
	import PluginRunsBar from '$lib/components/PluginRunsBar.svelte';
	import PluginFrame from '$lib/components/PluginFrame.svelte';
	import { pluginPanels } from '$lib/plugins.svelte';
	import { assetTypes, show } from '$lib/assetTypes.svelte';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';
	import { canEdit } from '$lib/session';
	import { aiStatus } from '$lib/ai.svelte';
	import { enter, leave } from '$lib/motion';

	let { data } = $props();
	let project = $derived(data.project);
	$effect(() => void assetTypes.load());

	const TABS = ['inventory', 'suggested', 'relationships', 'triggers'] as const;
	const asked = page.url.searchParams.get('tab');
	// Built-in tabs, or a plugin's page (`plugin:<plugin id>:<panel id>`, manifest `ui` slot project.page).
	let tab = $state<(typeof TABS)[number] | `plugin:${string}`>(TABS.find((t) => t === asked) ?? (asked?.startsWith('plugin:') ? (asked as `plugin:${string}`) : 'inventory'));
	$effect(() => void pluginPanels.load(data.project.id));
	let pagePanels = $derived(pluginPanels.for(data.project.id, 'project.page'));
	let openPanel = $derived(tab.startsWith('plugin:') ? pagePanels.find((p) => tab === `plugin:${p.plugin.id}:${p.id}`) : undefined);
	/** Relationships tab: a list or a graph, and the asset to select in the graph. */
	let relView = $state<'list' | 'graph'>(page.url.searchParams.get('view') === 'graph' ? 'graph' : 'list');
	let relFocus = $state<UUID | null>(page.url.searchParams.get('focus'));
	/** The graph fills the page instead of sitting in the centred column. */
	let filling = $derived((tab === 'relationships' && relView === 'graph') || !!openPanel);
	function showInGraph(id: UUID) {
		relFocus = id;
		relView = 'graph';
		tab = 'relationships';
		assetOpen = false;
	}
	/** Suggested tab: what's waiting, or what people dismissed. */
	let showDismissed = $state(false);
	let status = $derived<Asset['status']>(tab === 'suggested' ? (showDismissed ? 'dismissed' : 'suggested') : 'confirmed');
	let suggestedCount = $state(0);

	// ---- inventory ----
	let type = $state('');
	let where = $state('');
	let items = $state<Asset[]>([]);
	let total = $state(0);
	let counts = $state<Record<string, number>>({});
	let loaded = $state(false);
	let live = $state(false);
	let seq = 0;
	async function load(liveChange = false) {
		const mine = ++seq;
		try {
			const res = await api().assets.list(project.id, { type: type || undefined, where, limit: 500, status, scope: status === 'confirmed' ? scopeFilter || undefined : undefined });
			if (mine !== seq) return;
			live = liveChange;
			items = res.items;
			total = res.total;
			counts = res.counts;
			suggestedCount = res.suggested;
			loaded = true;
		} catch (err) {
			if (mine === seq) snackbar.error(err instanceof ApiError ? err.message : 'Could not load the assets');
		}
	}
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		void type;
		void where;
		void status;
		void scopeFilter;
		clearTimeout(timer);
		timer = setTimeout(() => void load(), 200);
	});

	// ---- scope: named groups (Internal, External, OT…) and exclusions ----
	let scope = $state<Scope>({ groups: [], exclude: [], include: [] });
	/** Show only one scope group's assets ('-': outside every group). */
	let scopeFilter = $state('');
	let scopeOpen = $state(false);
	let groupDrafts = $state<{ name: string; text: string }[]>([]);
	let scopeExclude = $state('');
	const GROUP_IDEAS = ['Internal', 'External', 'Sensitive', 'OT', 'Cloud', 'Wireless', 'Web'];
	$effect(() => {
		void project.id;
		api().assets.scope(project.id).then((s) => (scope = s));
	});
	function editScope() {
		groupDrafts = scope.groups.length ? scope.groups.map((g) => ({ name: g.name, text: g.include.join('\n') })) : [{ name: 'Scope', text: '' }];
		scopeExclude = scope.exclude.join('\n');
		scopeOpen = true;
	}
	function addGroup(name = '') {
		groupDrafts = [...groupDrafts, { name, text: '' }];
	}
	async function saveScope() {
		const lines = (t: string) => t.split(/[\n,]+/).map((x) => x.trim()).filter(Boolean);
		const groups = groupDrafts.filter((g) => g.name.trim() || g.text.trim()).map((g) => ({ name: g.name.trim() || 'Scope', include: lines(g.text) }));
		try {
			scope = await api().assets.setScope(project.id, { groups, exclude: lines(scopeExclude) });
			scopeOpen = false;
			if (scopeFilter && scopeFilter !== '-' && !scope.groups.some((g) => g.name === scopeFilter)) scopeFilter = '';
			void load();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save the scope');
		}
	}

	// ---- triggers ----
	let triggers = $state<AssetTrigger[]>([]);
	let fires = $state<AssetTriggerFire[]>([]);
	async function loadTriggers() {
		[triggers, fires] = await Promise.all([api().assets.triggers(project.id), api().assets.fires(project.id)]);
	}
	$effect(() => {
		void project.id;
		void loadTriggers();
	});
	let waiting = $derived(fires.filter((f) => f.status === 'waiting'));
	let triggerOpen = $state(false);
	let editingTrigger = $state<AssetTrigger | null>(null);
	async function toggle(t: AssetTrigger, on: boolean) {
		const { id, project_id, fires: _f, created_at, ...body } = t;
		void id; void project_id; void _f; void created_at;
		await api().assets.updateTrigger(t.id, { ...body, enabled: on });
		void loadTriggers();
	}
	async function removeTrigger(t: AssetTrigger) {
		if (!(await confirmAction({ title: `Delete “${t.name}”?`, message: 'It stops running; runs it already started keep going.', confirmLabel: 'Delete' }))) return;
		await api().assets.removeTrigger(t.id);
		void loadTriggers();
	}
	async function decide(f: AssetTriggerFire, run: boolean) {
		try {
			const res = run ? await api().assets.approve(f.id) : await api().assets.skip(f.id);
			if (run && res.status === 'failed') snackbar.error(`Couldn't start it: ${(res as { reason?: string }).reason ?? ''}`);
			else snackbar.show(run ? `Started “${f.trigger.name}” for ${f.asset.label}` : 'Skipped');
			void loadTriggers();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not do that');
		}
	}

	// Live: new assets, trigger runs.
	let liveTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() =>
		realtime.subscribe((e) => {
			if (e.project_id !== project.id) return;
			if (e.name === 'assets.changed') {
				clearTimeout(liveTimer);
				liveTimer = setTimeout(() => void load(true), 400);
			}
			if (e.name === 'asset_triggers.changed') void loadTriggers();
		})
	);

	// ---- the asset dialog (also opened by ?asset=<id>, e.g. from search) ----
	let openId = $state<UUID | null>(page.url.searchParams.get('asset'));
	let assetOpen = $state(page.url.searchParams.has('asset'));
	function openAsset(id: UUID) {
		openId = id;
		assetOpen = true;
	}
	$effect(() => {
		if (!assetOpen && page.url.searchParams.has('asset')) {
			const url = new URL(page.url);
			url.searchParams.delete('asset');
			void goto(url, { replaceState: true, noScroll: true, keepFocus: true });
		}
	});

	// ---- adding ----
	let addOpen = $state(false);
	let addType = $state('host');
	let addFields = $state<Record<string, string>>({});
	let addSecret = $state('');
	let importOpen = $state(false);
	let importType = $state('ip');
	let importText = $state('');
	async function add() {
		try {
			await api().assets.add(project.id, { type: addType, fields: addFields, secret: addType === 'credential' && addSecret ? addSecret : undefined });
			addOpen = false;
			addFields = {};
			addSecret = '';
			void load(true);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not add it');
		}
	}
	async function collect() {
		try {
			const r = await api().assets.collect(project.id);
			const found = r.added ? `Found ${r.added} asset${r.added === 1 ? '' : 's'} in past results` : 'No new assets in past results';
			snackbar.show(r.suggested ? `${found}; ${r.suggested} mentioned in notes and comments to confirm` : found);
			void load(true);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not collect');
		}
	}
	async function doImport() {
		try {
			const r = await api().assets.import(project.id, importType, importText);
			importOpen = false;
			importText = '';
			snackbar.show(`Added ${r.added}, updated ${r.updated}${r.skipped.length ? `, skipped ${r.skipped.length}` : ''}`);
			void load(true);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not import');
		}
	}

	// ---- columns per type ----
	let current = $derived(type ? assetTypes.get(type) : undefined);
	let columns = $derived.by(() => {
		if (!current) return [];
		const skip = new Set(current.name === 'host' ? ['ip', 'hostname'] : current.identity);
		return current.fields.filter((f) => !skip.has(f.name)).slice(0, 4);
	});
	const summary = (a: Asset) =>
		Object.entries(a.fields)
			.filter(([k]) => !['ip', 'hostname', 'name', 'url', 'host', 'port', 'username'].includes(k))
			.slice(0, 4)
			.map(([k, v]) => `${k}: ${show(v)}`)
			.join(' · ');
	const describeTrigger = (t: AssetTrigger) => {
		const tt = assetTypes.get(t.asset_type);
		const when = `${t.on_event === 'updated' ? 'When a' : 'When a new'} ${(tt?.label ?? t.asset_type).toLowerCase()} ${t.on_event === 'updated' ? 'changes' : t.on_event === 'any' ? 'is found or changes' : 'is found'}`;
		const ifs = t.conditions.map((c) => `${c.field} ${({ equals: 'is', not_equals: 'is not', contains: 'contains', exists: 'is set', missing: 'is empty', gt: '>', lt: '<', gte: '≥', lte: '≤' })[c.op]}${c.value != null && c.value !== '' ? ` ${c.value}` : ''}`);
		return `${when}${ifs.length ? `, if ${ifs.join(' and ')}` : ''}`;
	};
	// ---- suggested: confirm or dismiss ----
	let deciding = $state<Set<UUID>>(new Set());
	async function decideOne(a: Asset, confirm: boolean) {
		deciding = new Set([...deciding, a.id]);
		try {
			if (confirm) await api().assets.confirm(a.id);
			else await api().assets.dismiss(a.id);
			items = items.filter((x) => x.id !== a.id);
			suggestedCount = Math.max(0, suggestedCount - (status === 'suggested' ? 1 : 0));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not do that');
		} finally {
			deciding = new Set([...deciding].filter((x) => x !== a.id));
		}
	}
	async function decideAll(confirm: boolean) {
		const ids = items.map((a) => a.id);
		const what = `${ids.length} suggestion${ids.length === 1 ? '' : 's'}`;
		if (!(await confirmAction({
			title: confirm ? `Confirm ${what}?` : `Dismiss ${what}?`,
			message: confirm ? 'They join the inventory; triggers treat them as new.' : 'They’re hidden, and mentions won’t bring them back.',
			confirmLabel: confirm ? 'Confirm' : 'Dismiss'
		}))) return;
		try {
			const r = await api().assets.decide(project.id, confirm ? 'confirm' : 'dismiss', ids);
			snackbar.show(`${confirm ? 'Confirmed' : 'Dismissed'} ${r.done}`);
			void load(true);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not do that');
		}
	}
	// ---- sorting suggestions with the AI: advice only, people decide ----
	$effect(() => void aiStatus.load());
	const GUIDANCE_KEY = (id: UUID) => `castor.assets.guidance.${id}`;
	let sortOpen = $state(false);
	let sorting = $state(false);
	let guidance = $state('');
	let verdicts = $state<Record<UUID, AssetVerdict>>({});
	let sortedBy = $state<{ model: string; left: number } | null>(null);
	$effect(() => {
		try {
			guidance = localStorage.getItem(GUIDANCE_KEY(project.id)) ?? '';
		} catch {
			guidance = '';
		}
	});
	async function sortWithAi() {
		sorting = true;
		try {
			localStorage.setItem(GUIDANCE_KEY(project.id), guidance);
		} catch {
			/* not kept */
		}
		try {
			const r = await api().assets.sortSuggested(project.id, guidance, items.map((a) => a.id));
			verdicts = { ...verdicts, ...Object.fromEntries(r.verdicts.map((v) => [v.id, v])) };
			sortedBy = { model: r.model, left: r.left };
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'The AI couldn’t sort them');
		} finally {
			sorting = false;
		}
	}
	const VERDICT = {
		real: { label: 'Likely real', icon: 'thumb_up', tone: 'bg-primary-container text-on-primary-container' },
		noise: { label: 'Likely noise', icon: 'thumb_down', tone: 'bg-surface-container-highest text-on-surface-variant' },
		unsure: { label: 'Not sure', icon: 'help', tone: 'bg-tertiary-container text-on-tertiary-container' }
	} as const;
	const ORDER = { real: 0, unsure: 1, noise: 2 } as const;
	/** With verdicts, likely real first and likely noise last. */
	let shown = $derived(
		tab === 'suggested' && Object.keys(verdicts).length
			? [...items].sort((a, b) => (ORDER[verdicts[a.id]?.verdict ?? 'unsure'] - ORDER[verdicts[b.id]?.verdict ?? 'unsure']))
			: items
	);
	let tally = $derived.by(() => {
		const out = { real: [] as UUID[], noise: [] as UUID[], unsure: [] as UUID[] };
		for (const a of items) if (verdicts[a.id]) out[verdicts[a.id].verdict].push(a.id);
		return out;
	});
	async function applyVerdict(kind: 'real' | 'noise') {
		const ids = tally[kind];
		if (!ids.length) return;
		const confirm = kind === 'real';
		if (!(await confirmAction({
			title: confirm ? `Confirm ${ids.length} the AI thinks are real?` : `Dismiss ${ids.length} the AI thinks are noise?`,
			message: confirm ? 'They join the inventory; triggers treat them as new. Check the list first: the AI can be wrong.' : 'They’re hidden, and mentions won’t bring them back. You can restore them under Show dismissed.',
			confirmLabel: confirm ? 'Confirm' : 'Dismiss'
		}))) return;
		try {
			const r = await api().assets.decide(project.id, confirm ? 'confirm' : 'dismiss', ids);
			snackbar.show(`${confirm ? 'Confirmed' : 'Dismissed'} ${r.done}`);
			void load(true);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not do that');
		}
	}

	const SOURCE_ICON: Record<string, string> = { comment: 'chat', post: 'forum', card: 'description', note: 'push_pin', output: 'terminal', ai: 'image' };

	const FIRE: Record<string, string> = { started: 'text-primary', waiting: 'text-tertiary', failed: 'text-error', limited: 'text-on-surface-variant', skipped: 'text-on-surface-variant' };
</script>

<svelte:head><title>{project.codename} · Assets · Castor</title></svelte:head>

<TopAppBar>
	<ProjectSwitcher {project} />
</TopAppBar>

<div class="flex-1 overflow-auto">
	<!-- The graph takes the whole window below the tabs; everything else is a centred column. -->
	<div class="mx-auto flex w-full flex-col gap-4 px-6 pt-2 {filling ? 'h-full min-h-[34rem] pb-4' : 'max-w-7xl pb-10'}">
		<Tabs
			tabs={[
				{ id: 'inventory', label: 'Inventory', icon: 'radar', badge: status === 'confirmed' ? Object.values(counts).reduce((a, b) => a + b, 0) : undefined },
				{ id: 'suggested', label: 'Suggested', icon: 'lightbulb', badge: suggestedCount || undefined },
				{ id: 'relationships', label: 'Relationships', icon: 'hub' },
				{ id: 'triggers', label: 'Triggers', icon: 'bolt', badge: waiting.length || undefined },
				...pagePanels.map((p) => ({ id: `plugin:${p.plugin.id}:${p.id}`, label: p.title, icon: p.icon }))
			]}
			bind:value={tab}
		/>
		<PluginRunsBar projectId={project.id} />

		{#if tab === 'inventory' || tab === 'suggested'}
			{#if tab === 'suggested'}
				<div class="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md bg-surface-container-low px-4 py-2.5 body-medium">
					<Icon name="lightbulb" size={18} class="text-on-surface-variant" />
					<p class="min-w-0 flex-1 text-on-surface-variant">
						Mentioned, not yet confirmed: addresses, names, URLs and CVEs in comments, posts, card descriptions, notes, output no parser read{project.settings?.ai_read_pictures ? ', and pictures the AI read' : ''}. They stay out of the inventory, arguments and triggers until someone confirms them.
					</p>
					<Switch checked={showDismissed} label="Show dismissed" onchange={(v) => (showDismissed = v)} />
				</div>
			{:else}
			<!-- scope -->
			<div class="flex flex-wrap items-center gap-2 rounded-md bg-surface-container-low px-4 py-2.5 body-medium">
				<span class="flex items-center gap-1.5 text-on-surface-variant"><Icon name="verified" size={18} />Scope</span>
				{#if scope.groups.length}
					{#each scope.groups as g (g.name)}
						<button
							type="button"
							class="state-layer focus-ring flex max-w-full flex-wrap items-center gap-1 rounded-sm border px-1.5 py-1 {scopeFilter === g.name ? 'border-primary bg-primary-container/40' : 'border-outline-variant/70'}"
							aria-pressed={scopeFilter === g.name}
							title={scopeFilter === g.name ? 'Show everything again' : `Show only what's in ${g.name}`}
							onclick={() => (scopeFilter = scopeFilter === g.name ? '' : g.name)}
						>
							<span class="px-0.5 label-medium text-on-surface">{g.name}</span>
							{#each g.include as e (e)}<span class="rounded-xs bg-primary-container px-1.5 py-0.5 font-mono text-[12px] text-on-primary-container">{e}</span>{:else}<span class="body-small text-on-surface-variant">empty</span>{/each}
						</button>
					{/each}
					{#if scope.exclude.length}
						<span class="flex flex-wrap items-center gap-1 px-1">
							<span class="label-medium text-on-surface-variant">Excluded</span>
							{#each scope.exclude as e (e)}<span class="rounded-xs border border-outline-variant px-1.5 py-0.5 font-mono text-[12px] text-on-surface-variant line-through">{e}</span>{/each}
						</span>
					{/if}
					<button type="button" class="rounded-full px-2 py-0.5 label-medium {scopeFilter === '-' ? 'bg-secondary-container text-on-secondary-container' : 'text-primary hover:underline'}" aria-pressed={scopeFilter === '-'} onclick={() => (scopeFilter = scopeFilter === '-' ? '' : '-')}>Outside scope</button>
				{:else}
					<span class="text-on-surface-variant">None set: triggered runs wait for approval.</span>
				{/if}
				{#if canEdit()}<Button variant="text" icon="edit" class="ml-auto" onclick={editScope}>Edit scope</Button>{/if}
			</div>
			{/if}

			<div class="flex flex-wrap items-center gap-2">
				<button type="button" class="rounded-full border px-3 py-1 label-large {type === '' ? 'border-transparent bg-secondary-container text-on-secondary-container' : 'border-outline-variant text-on-surface-variant'}" onclick={() => (type = '')}>All</button>
				{#each assetTypes.list as t (t.name)}
					<button
						type="button"
						class="flex items-center gap-1.5 rounded-full border px-3 py-1 label-large {type === t.name ? 'border-transparent bg-secondary-container text-on-secondary-container' : 'border-outline-variant text-on-surface-variant'} {counts[t.name] ? '' : 'opacity-60'}"
						onclick={() => (type = t.name)}
					>
						<Icon name={t.icon} size={16} />{t.plural}<span class="label-small opacity-80">{counts[t.name] ?? 0}</span>
					</button>
				{/each}
				<span class="flex-1"></span>
				{#if canEdit() && tab === 'suggested' && !showDismissed && items.length && aiStatus.enabled}
					<Button variant={sortOpen ? 'tonal' : 'text'} icon="auto_awesome" onclick={() => (sortOpen = !sortOpen)}>Sort with AI</Button>
				{/if}
				{#if canEdit() && tab === 'suggested' && items.length > 1}
					<Button variant="text" icon={showDismissed ? 'undo' : 'block'} onclick={() => decideAll(showDismissed)}>{showDismissed ? 'Restore all shown' : 'Dismiss all shown'}</Button>
					{#if !showDismissed}<Button variant="tonal" icon="done_all" onclick={() => decideAll(true)}>Confirm all shown</Button>{/if}
				{:else if canEdit() && tab === 'inventory'}
					<PluginActions projectId={project.id} place="project" />
					<Button variant="tonal" icon="upload" onclick={() => (importOpen = true)}>Import</Button>
					<Button icon="add" onclick={() => { addType = type || 'host'; addOpen = true; }}>Add</Button>
				{/if}
			</div>
			<label class="flex items-center gap-2 rounded-full bg-surface-container-high px-4">
				<Icon name="filter_list" size={20} class="text-on-surface-variant" />
				<input class="h-11 min-w-0 flex-1 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter: words, field:value (smb_signing:false, port>1000), relationships (accepts:*, has_admin:alice)" bind:value={where} autocomplete="off" spellcheck="false" />
				{#if where}<IconButton icon="close" label="Clear" size="sm" onclick={() => (where = '')} />{/if}
			</label>

			{#if !loaded}
				<p class="body-medium text-on-surface-variant">Loading…</p>
			{:else if tab === 'suggested'}
				{#if sortOpen && !showDismissed && items.length}
					<section class="flex flex-col gap-3 rounded-md border border-outline-variant bg-surface-container px-4 py-3" aria-label="Sort with AI">
						<div class="flex items-start gap-2">
							<Icon name="auto_awesome" size={18} class="mt-0.5 text-primary" />
							<p class="min-w-0 flex-1 body-medium text-on-surface-variant">
								The AI reads the {items.length > 200 ? 'first 200 of the' : ''} {items.length} suggestion{items.length === 1 ? '' : 's'} shown, where each was mentioned and the project's scope, and marks each likely real, likely noise or not sure. It only advises: nothing changes until you confirm or dismiss.
							</p>
							<IconButton icon="close" label="Close" size="sm" onclick={() => (sortOpen = false)} />
						</div>
						<form class="flex flex-wrap items-end gap-2" onsubmit={(e) => { e.preventDefault(); void sortWithAi(); }}>
							<TextField
								label="Guidance (optional)"
								bind:value={guidance}
								class="min-w-[16rem] flex-1"
								placeholder="e.g. Only 10.40.0.0/16 and acme.local are ours; vendor, CDN and tool sites are noise"
								supporting="Saved for this project in this browser."
							/>
							<Button type="submit" icon="auto_awesome" loading={sorting}>{Object.keys(verdicts).length ? 'Sort again' : 'Sort'}</Button>
						</form>
						{#if sortedBy && (tally.real.length || tally.noise.length || tally.unsure.length)}
							<div class="flex flex-wrap items-center gap-2 border-t border-outline-variant/60 pt-3">
								<span class="body-small text-on-surface-variant">
									{tally.real.length} likely real · {tally.noise.length} likely noise · {tally.unsure.length} not sure · by {sortedBy.model}{sortedBy.left ? ` · ${sortedBy.left} more not sorted; filter and sort again` : ''}
								</span>
								<span class="flex-1"></span>
								{#if tally.noise.length}<Button variant="text" icon="block" onclick={() => applyVerdict('noise')}>Dismiss {tally.noise.length} likely noise</Button>{/if}
								{#if tally.real.length}<Button variant="tonal" icon="done_all" onclick={() => applyVerdict('real')}>Confirm {tally.real.length} likely real</Button>{/if}
								<Button variant="text" onclick={() => { verdicts = {}; sortedBy = null; }}>Clear</Button>
							</div>
						{/if}
					</section>
				{/if}
				{#if !items.length}
					<div class="flex flex-col items-center gap-2 py-16 text-center text-on-surface-variant">
						<Icon name="lightbulb" size={40} />
						<p class="body-large">{showDismissed ? 'Nothing dismissed.' : where || type ? 'Nothing matches.' : 'No suggestions waiting.'}</p>
						{#if !showDismissed && !where && !type}
							<p class="max-w-md body-medium">When someone mentions an address, a name or a CVE in a comment, post or note, or a run prints one no parser reads, it shows up here to confirm.</p>
							{#if canEdit()}<Button variant="tonal" icon="history" onclick={collect}>Look through past notes and results</Button>{/if}
						{/if}
					</div>
				{:else}
					<ul class="flex flex-col divide-y divide-outline-variant/50 rounded-md border border-outline-variant/60">
						{#each shown as a (a.id)}
							{@const m = a.mentions[0]}
							{@const v = verdicts[a.id]}
							<li class="flex flex-wrap items-start gap-x-3 gap-y-1 px-3 py-2.5" in:enter={{ enabled: live }} out:leave={{ enabled: live }}>
								<Icon name={assetTypes.get(a.type)?.icon ?? 'radar'} size={18} class="mt-0.5 shrink-0 text-on-surface-variant" />
								<div class="min-w-0 flex-1">
									<button type="button" class="font-mono text-[13px] text-on-surface hover:underline" onclick={() => openAsset(a.id)}>{a.label}</button>
									<span class="ml-1 body-small text-on-surface-variant">{assetTypes.get(a.type)?.label ?? a.type}</span>
									{#if v}
										<p class="mt-1 flex flex-wrap items-center gap-1.5 body-small">
											<span class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 label-small {VERDICT[v.verdict].tone}"><Icon name={VERDICT[v.verdict].icon} size={12} />{VERDICT[v.verdict].label}</span>
											{#if v.reason}<span class="text-on-surface-variant">{v.reason}</span>{/if}
										</p>
									{/if}
									{#if m}
										<p class="mt-0.5 flex min-w-0 items-start gap-1.5 body-small text-on-surface-variant">
											<Icon name={SOURCE_ICON[m.source] ?? 'notes'} size={14} class="mt-px shrink-0" />
											<span class="min-w-0">
												{#if m.link}<a href={m.link} class="text-primary hover:underline">{m.where}</a>{:else}{m.where}{/if}
												{relativeTime(m.at)}{#if m.excerpt}: <span class="text-on-surface">“{m.excerpt}”</span>{/if}
												{#if a.mentions.length > 1}<span> · and {a.mentions.length - 1} more</span>{/if}
											</span>
										</p>
									{/if}
								</div>
								{#if canEdit()}
									<span class="flex shrink-0 gap-1">
										{#if showDismissed}
											<Button variant="text" icon="undo" loading={deciding.has(a.id)} onclick={() => decideOne(a, true)}>Confirm</Button>
										{:else}
											<Button variant="text" loading={deciding.has(a.id)} onclick={() => decideOne(a, false)}>Dismiss</Button>
											<Button variant="tonal" icon="check" loading={deciding.has(a.id)} onclick={() => decideOne(a, true)}>Confirm</Button>
										{/if}
									</span>
								{/if}
							</li>
						{/each}
					</ul>
					{#if total > items.length}<p class="body-small text-on-surface-variant">Showing {items.length} of {total}; filter to narrow it down.</p>{/if}
				{/if}
			{:else if !items.length}
				<div class="flex flex-col items-center gap-2 py-16 text-center text-on-surface-variant">
					<Icon name="radar" size={40} />
					<p class="body-large">{where || type ? 'Nothing matches.' : 'Nothing found yet.'}</p>
					{#if !where && !type}
						<p class="max-w-md body-medium">Hosts, services, URLs and vulnerabilities appear here as scans finish (nmap, NetExec, ffuf, httpx, nuclei and more are recorded automatically), or add and import them yourself. Things mentioned in comments and notes wait under Suggested.</p>
						{#if canEdit()}<Button variant="tonal" icon="history" onclick={collect}>Collect from past results</Button>{/if}
					{/if}
				</div>
			{:else}
				<div class="overflow-x-auto rounded-md border border-outline-variant/60">
					<table class="w-full body-medium">
						<thead class="bg-surface-container-low text-left body-small text-on-surface-variant">
							<tr>
								{#if !current}<th class="px-3 py-2 font-medium">Type</th>{/if}
								<th class="px-3 py-2 font-medium">{current?.label ?? 'Asset'}</th>
								{#if current}
									{#each columns as c (c.name)}<th class="px-3 py-2 font-medium">{c.label}</th>{/each}
								{:else}
									<th class="px-3 py-2 font-medium">Details</th>
								{/if}
								<th class="px-3 py-2 font-medium">Links</th>
								<th class="px-3 py-2 text-right font-medium">Last seen</th>
							</tr>
						</thead>
						<tbody>
							{#each items as a (a.id)}
								<tr class="cursor-pointer border-t border-outline-variant/50 hover:bg-on-surface/5" onclick={() => openAsset(a.id)} in:enter={{ enabled: live }} out:leave={{ enabled: live }}>
									{#if !current}<td class="px-3 py-2 whitespace-nowrap text-on-surface-variant"><span class="flex items-center gap-1.5"><Icon name={assetTypes.get(a.type)?.icon ?? 'radar'} size={16} />{assetTypes.get(a.type)?.label ?? a.type}</span></td>{/if}
									<td class="px-3 py-2">
										<span class="flex items-center gap-1.5 font-mono text-[13px] text-on-surface">
											{a.label}
											{#each a.scope_groups as g (g)}<span class="rounded-xs bg-primary-container px-1.5 py-px font-sans label-small text-on-primary-container" title="In the {g} scope">{g}</span>{/each}
											{#if a.has_secret}<Icon name="key" size={14} class="text-tertiary" />{/if}
											{#if a.description}<span title={a.description.slice(0, 200)}><Icon name="notes" size={14} class="text-on-surface-variant" /></span>{/if}
										</span>
									</td>
									{#if current}
										{#each columns as c (c.name)}
											<td class="px-3 py-2 {(c.name === 'smb_signing' && a.fields[c.name] === false) || (c.name === 'severity' && ['critical', 'high'].includes(String(a.fields[c.name]))) ? 'font-medium text-error' : 'text-on-surface'}">{show(a.fields[c.name])}</td>
										{/each}
									{:else}
										<td class="max-w-md truncate px-3 py-2 text-on-surface-variant">{summary(a)}</td>
									{/if}
									<td class="px-3 py-2 text-on-surface-variant">{a.links || ''}</td>
									<td class="px-3 py-2 text-right whitespace-nowrap text-on-surface-variant" title={absoluteTime(a.last_seen)}>{relativeTime(a.last_seen)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				{#if total > items.length}<p class="body-small text-on-surface-variant">Showing {items.length} of {total}; filter to narrow it down.</p>{/if}
			{/if}
		{:else if tab === 'relationships'}
			<AssetRelationships projectId={project.id} bind:view={relView} focus={relFocus} onopenasset={openAsset} class={filling ? 'min-h-0 flex-1' : ''} />
		{:else if openPanel}
			{#key openPanel.url}
				<PluginFrame panel={openPanel} projectId={project.id} onopenasset={openAsset} fill />
			{/key}
		{:else if tab.startsWith('plugin:')}
			<p class="py-6 body-medium text-on-surface-variant">That plugin page isn’t here (the plugin may be off in this project).</p>
		{:else}
			{#if waiting.length}
				<section class="rounded-lg border border-tertiary/50 bg-tertiary-container/30 p-4">
					<h2 class="mb-2 flex items-center gap-2 title-medium text-on-surface"><Icon name="front_hand" class="text-tertiary" />Waiting for approval</h2>
					<ul class="flex flex-col gap-1.5">
						{#each waiting as f (f.id)}
							<li class="flex flex-wrap items-center gap-2 rounded-md bg-surface-container px-3 py-2" in:enter out:leave>
								<span class="body-medium text-on-surface">“{f.trigger.name}” on <button type="button" class="font-mono text-primary hover:underline" onclick={() => openAsset(f.asset.id)}>{f.asset.label}</button>{#if f.other}{' '}→ <button type="button" class="font-mono text-primary hover:underline" onclick={() => openAsset(f.other!.id)}>{f.other.label}</button>{/if}</span>
								<span class="body-small text-on-surface-variant">{f.reason} · {relativeTime(f.at)}</span>
								{#if canEdit()}
									<span class="ml-auto flex gap-2">
										<Button variant="text" onclick={() => decide(f, false)}>Skip</Button>
										<Button icon="play_arrow" onclick={() => decide(f, true)}>Run</Button>
									</span>
								{/if}
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			<div class="flex items-center gap-2">
				<h2 class="flex-1 title-large text-on-surface">Triggers</h2>
				{#if canEdit()}<Button icon="add" onclick={() => { editingTrigger = null; triggerOpen = true; }}>New trigger</Button>{/if}
			</div>
			{#if triggers.length}
				<div class="grid gap-3 md:grid-cols-2">
					{#each triggers as t (t.id)}
						<article class="flex flex-col gap-2 rounded-lg bg-surface-container-low p-4 {t.enabled ? '' : 'opacity-70'}" in:enter out:leave>
							<header class="flex items-center gap-2">
								<Icon name="bolt" class={t.enabled ? 'text-primary' : 'text-on-surface-variant'} />
								<h3 class="min-w-0 flex-1 truncate title-medium text-on-surface">{t.name}</h3>
								{#if canEdit()}
									<Switch checked={t.enabled} onchange={(on) => toggle(t, on)} />
									<IconButton icon="edit" label="Edit" size="sm" onclick={() => { editingTrigger = t; triggerOpen = true; }} />
									<IconButton icon="delete" label="Delete" size="sm" onclick={() => removeTrigger(t)} />
								{/if}
							</header>
							<p class="body-medium text-on-surface">{describeTrigger(t)},</p>
							<p class="body-medium text-on-surface">run the {t.action} <span class="font-mono">{t.target}</span>{#if Object.keys(t.args).length}<span class="text-on-surface-variant"> with {Object.entries(t.args).map(([k, v]) => `${k} = ${v}`).join(', ')}</span>{/if}.</p>
							<p class="body-small text-on-surface-variant">
								{t.once_per_asset ? 'Once per asset' : 'Every time'} · at most {t.max_per_hour}/hour ·
								{Object.entries(t.fires).map(([s, n]) => `${n} ${s}`).join(', ') || 'not run yet'}
							</p>
						</article>
					{/each}
				</div>
			{:else}
				<p class="rounded-md border border-dashed border-outline-variant p-6 text-center body-medium text-on-surface-variant">
					No triggers yet. A trigger runs a command or playbook when an asset is found or changes, or gets a new relationship, e.g. “every new host → ping it”, “SMB signing is off → queue the relay check” or “a credential works on a new host → list its shares”.
				</p>
			{/if}

			{#if fires.length}
				<h2 class="mt-2 title-medium text-on-surface">Recent runs</h2>
				<ul class="divide-y divide-outline-variant/50 rounded-md bg-surface-container-low">
					{#each fires.slice(0, 30) as f (f.id)}
						<li class="flex items-center gap-3 px-3 py-2 body-small">
							<span class="w-16 shrink-0 font-medium {FIRE[f.status] ?? ''}">{f.status}</span>
							<span class="min-w-0 flex-1 truncate text-on-surface">“{f.trigger.name}” on <button type="button" class="font-mono text-primary hover:underline" onclick={() => openAsset(f.asset.id)}>{f.asset.label}</button>{#if f.other}{' '}→ <button type="button" class="font-mono text-primary hover:underline" onclick={() => openAsset(f.other!.id)}>{f.other.label}</button>{/if}{f.reason ? ` · ${f.reason}` : ''}</span>
							{#if f.task_id}<a href="/projects/{project.id}?task={f.task_id}" class="shrink-0 text-primary hover:underline">Open card</a>{/if}
							{#if f.run_id}<a href="/playbooks?run={f.run_id}" class="shrink-0 text-primary hover:underline">Open run</a>{/if}
							<span class="shrink-0 text-on-surface-variant">{relativeTime(f.at)}</span>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</div>
</div>

<AssetDialog bind:open={assetOpen} assetId={openId} projectId={project.id} onopenasset={openAsset} onshowgraph={showInGraph} onchanged={() => { void load(true); void api().assets.scope(project.id).then((x) => (scope = x)); }} />
<TriggerDialog bind:open={triggerOpen} projectId={project.id} trigger={editingTrigger} onsaved={() => void loadTriggers()} />

<Dialog bind:open={scopeOpen} title="Project scope" icon="verified" size="md">
	<div class="flex flex-col gap-4 pt-2 pb-2">
		<p class="body-small text-on-surface-variant">
			Group what's in scope by kind: Internal, External, Sensitive, OT… Each takes addresses, CIDR ranges and domains (a domain covers its subdomains), one per line. An asset in any group is in scope, and shows its groups; triggers run by themselves only for assets in scope. Exclusions win over every group.
		</p>
		{#each groupDrafts as g, i (i)}
			<div class="flex flex-col gap-2 rounded-md border border-outline-variant/70 p-3">
				<div class="flex items-center gap-2">
					<TextField label="Group" bind:value={g.name} class="flex-1" placeholder="e.g. Internal" />
					<IconButton icon="delete" label="Remove {g.name || 'this group'}" onclick={() => (groupDrafts = groupDrafts.filter((_, j) => j !== i))} />
				</div>
				<TextField label="In this group" bind:value={g.text} multiline rows={3} mono placeholder={'10.20.0.0/16\ncorp.local'} />
			</div>
		{/each}
		<div class="flex flex-wrap items-center gap-1.5">
			<Button variant="tonal" icon="add" onclick={() => addGroup()}>Add a group</Button>
			{#each GROUP_IDEAS.filter((n) => !groupDrafts.some((g) => g.name.trim().toLowerCase() === n.toLowerCase())) as n (n)}
				<button type="button" class="rounded-full border border-outline-variant px-2.5 py-1 label-medium text-on-surface-variant hover:bg-on-surface/5" onclick={() => addGroup(n)}>+ {n}</button>
			{/each}
		</div>
		<TextField label="Excluded (from every group)" bind:value={scopeExclude} multiline rows={3} mono placeholder="10.20.99.0/24" />
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (scopeOpen = false)}>Cancel</Button>
		<Button icon="check" onclick={saveScope}>Save scope</Button>
	{/snippet}
</Dialog>

<Dialog bind:open={addOpen} title="Add an asset" icon="add" size="sm">
	<div class="flex flex-col gap-3 pt-2 pb-2">
		<Select label="Type" bind:value={addType} options={assetTypes.list.map((t) => ({ value: t.name, label: t.label }))} />
		{#each assetTypes.get(addType)?.fields ?? [] as f (f.name)}
			{#if f.name !== 'fingerprint'}
				<TextField label="{f.label}{assetTypes.get(addType)?.identity.includes(f.name) ? ' *' : ''}" value={addFields[f.name] ?? ''} oninput={(e) => (addFields = { ...addFields, [f.name]: (e.currentTarget as HTMLInputElement).value })} mono={f.type !== 'text'} />
			{/if}
		{/each}
		{#if addType === 'credential'}
			<TextField label="Secret (password, hash or key)" type="password" bind:value={addSecret} supporting="Encrypted with the vault; shown only when someone reveals it." />
		{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (addOpen = false)}>Cancel</Button>
		<Button icon="check" onclick={add}>Add</Button>
	{/snippet}
</Dialog>

<Dialog bind:open={importOpen} title="Import assets" icon="upload" size="sm">
	<div class="flex flex-col gap-3 pt-2 pb-2">
		<Select label="As" value={importType} onchange={(v) => (importType = v)} options={[{ value: 'ip', label: 'Hosts (addresses)' }, { value: 'host', label: 'Hosts (addresses or hostnames)' }, { value: 'domain', label: 'Domains' }, { value: 'url', label: 'URLs' }, { value: 'user', label: 'Users' }]} />
		<TextField label="One per line (or comma separated)" bind:value={importText} multiline rows={8} mono />
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (importOpen = false)}>Cancel</Button>
		<Button icon="upload" disabled={!importText.trim()} onclick={doImport}>Import</Button>
	{/snippet}
</Dialog>
