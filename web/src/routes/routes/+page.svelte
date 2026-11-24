<script lang="ts">
	import { brand } from '$lib/brand.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { Agent, NetworkRoute, NetworkRouteIn, RouteHopIn, RouteLive, RouteTest, RouteUsage, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Chip, Dialog, Icon, Select, Switch, TagInput, TextField, TopAppBar } from '$lib/m3';
	import RouteChain, { type ChainHop } from '$lib/components/routes/RouteChain.svelte';
	import HopEditor, { type DraftHop } from '$lib/components/routes/HopEditor.svelte';
	import AgentProjectsPicker from '$lib/components/AgentProjectsPicker.svelte';
	import LibraryTransfer from '$lib/components/LibraryTransfer.svelte';
	import { HOP_KINDS, METHOD_WORDS, TONE_CLASS, hopKind, hopResult, routeAllows, routeState } from '$lib/routes';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { canEdit, isAdmin } from '$lib/session';
	import { enter, leave } from '$lib/motion';
	import { loadTags, type TagSuggestion } from '$lib/tags';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let routes = $state<NetworkRoute[]>(data.routes);
	// svelte-ignore state_referenced_locally
	let agents = $state<Agent[]>(data.agents);
	// svelte-ignore state_referenced_locally
	const projects = data.projects;
	// svelte-ignore state_referenced_locally
	const sshHosts = data.sshHosts;
	const editable = canEdit();

	interface Draft {
		id: UUID | null;
		name: string;
		description: string;
		tags: string[];
		/** null: every project. */
		projects: UUID[] | null;
		dns: 'remote' | 'local';
		strict: boolean;
		segment: string;
		/** null: every agent; else these agents and any agent with one of the tags. */
		agents: { ids: UUID[]; tags: string[] } | null;
		hops: DraftHop[];
	}

	let filter = $state('');
	let draft = $state<Draft | null>(null);
	let base = $state('');
	let selectedHop = $state<number | null>(0);
	let saving = $state(false);
	let errors = $state<string | null>(null);
	let usage = $state<RouteUsage | null>(null);
	let testAgent = $state('');
	let testTarget = $state('');
	let testing = $state(false);
	let fromSshOpen = $state(false);
	let fromSsh = $state({ host: '', name: '' });
	let tagSuggestions = $state<TagSuggestion[]>([]);

	const current = $derived(draft?.id ? routes.find((r) => r.id === draft!.id) ?? null : null);
	const dirty = $derived(!!draft && JSON.stringify(snapshotOf(draft)) !== base);
	const agentName = (id: string) => agents.find((a) => a.id === id)?.name ?? 'an agent';

	// ----- drafts -----

	let keySeq = 0;
	const newKey = () => `h${++keySeq}`;

	function blankHop(kind: DraftHop['kind'] = 'ssh'): DraftHop {
		return { key: newKey(), kind, host: '', port: '', username: '', label: '', auth: kind === 'ssh' ? 'key' : 'none',
			secret: { mode: kind === 'ssh' ? 'generate' : 'none', password: '', private_key: '', passphrase: '' }, pin: '' };
	}
	function draftOf(r: NetworkRoute): Draft {
		return {
			id: r.id, name: r.name, description: r.description, tags: [...r.tags], projects: r.project_ids.length ? [...r.project_ids] : null,
			dns: r.dns, strict: r.strict, segment: r.segment, agents: r.agents ? { ids: [...r.agents.ids], tags: [...r.agents.tags] } : null,
			hops: r.hops.map((h) => ({ key: newKey(), id: h.id, kind: h.kind, host: h.host, port: String(h.port), username: h.username, label: h.label,
				auth: h.auth, secret: { mode: h.has_secret ? 'keep' : h.auth === 'none' ? 'none' : h.auth === 'password' ? 'password' : 'generate',
					password: '', private_key: '', passphrase: '' }, pin: '', stored: h }))
		};
	}
	/** What decides "unsaved changes" (secrets typed count; stored hop details don't). */
	function snapshotOf(d: Draft) {
		return { ...d, hops: d.hops.map(({ stored: _s, key: _k, ...rest }) => rest) };
	}
	function open(r: NetworkRoute | null, push = true) {
		draft = r ? draftOf(r) : { id: null, name: '', description: '', tags: [], projects: isAdmin() ? null : [], dns: 'remote', strict: true, segment: '', agents: null, hops: [blankHop()] };
		base = JSON.stringify(snapshotOf(draft));
		selectedHop = r ? null : 0;
		errors = null;
		usage = null;
		testTarget = r?.last_test?.target ?? '';
		if (r) api().routes.usage(r.id).then((u) => (usage = u)).catch(() => {});
		if (push) {
			const url = new URL(page.url);
			url.searchParams.delete('r');
			url.searchParams.delete('new');
			if (r) url.searchParams.set('r', r.id);
			else url.searchParams.set('new', '1');
			goto(url, { replaceState: true, keepFocus: true, noScroll: true });
		}
	}
	/** The route changed elsewhere (a test, a host key, someone else's edit): show it, keeping the place. */
	function refresh(r: NetworkRoute) {
		const keep = selectedHop;
		draft = draftOf(r);
		base = JSON.stringify(snapshotOf(draft));
		selectedHop = keep !== null && keep < draft.hops.length ? keep : null;
		api().routes.usage(r.id).then((u) => (usage = u)).catch(() => {});
	}
	async function choose(r: NetworkRoute | null) {
		if (dirty && !(await confirmAction({ title: 'Leave unsaved changes?', message: 'The changes to this route aren’t saved.', confirmLabel: 'Leave them', danger: true }))) return;
		open(r);
	}

	// From the address: ?r=<id> or ?new=1.
	$effect(() => {
		const want = page.url.searchParams.get('r');
		if (want && draft?.id !== want) {
			const r = routes.find((x) => x.id === want);
			if (r) open(r, false);
		} else if (!want && page.url.searchParams.get('new') && !draft) open(null, false);
	});
	$effect(() => {
		loadTags('library').then((t) => (tagSuggestions = t)).catch(() => {});
		loadTags('agents').then((t) => (agentTagSuggestions = t)).catch(() => {});
	});

	// ----- live -----

	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name === 'route.updated') {
				const r = e.data as NetworkRoute;
				const had = routes.some((x) => x.id === r.id);
				routes = had ? routes.map((x) => (x.id === r.id ? r : x)) : [...routes, r].sort((a, b) => a.name.localeCompare(b.name));
				if (draft?.id === r.id) {
					if (!dirty) refresh(r);
					else for (const h of draft.hops) h.stored = r.hops.find((s) => s.id === h.id) ?? h.stored; // keys, fingerprints
				}
			} else if (e.name === 'route.deleted') {
				const id = (e.data as { id: string }).id;
				routes = routes.filter((x) => x.id !== id);
				if (draft?.id === id) draft = null;
			} else if (e.name === 'route.live') {
				const l = e.data as RouteLive & { route_id: string };
				routes = routes.map((x) => {
					if (x.id !== l.route_id) return x;
					const others = x.live.filter((y) => y.agent_id !== l.agent_id);
					return { ...x, live: l.state === 'down' ? others : [...others, l] };
				});
			} else if (e.name === 'agent.updated') {
				const a = e.data as Agent;
				agents = [...agents.filter((x) => x.id !== a.id), a];
			}
		})
	);

	// ----- editing the chain -----

	function insertHop(at: number) {
		if (!draft) return;
		const prev = draft.hops[at - 1];
		draft.hops.splice(at, 0, blankHop(prev?.kind === 'ssh' ? 'socks5' : 'ssh'));
		selectedHop = at;
	}
	function removeHop(i: number) {
		if (!draft || draft.hops.length <= 1) return;
		draft.hops.splice(i, 1);
		selectedHop = Math.min(i, draft.hops.length - 1);
	}
	function moveHop(i: number, delta: number) {
		if (!draft) return;
		const j = i + delta;
		if (j < 0 || j >= draft.hops.length) return;
		const [h] = draft.hops.splice(i, 1);
		draft.hops.splice(j, 0, h);
		selectedHop = j;
	}

	const chainHops = $derived<ChainHop[]>(
		(draft?.hops ?? []).map((h, i) => {
			const unsaved = h.secret.mode === 'password' && h.secret.password ? true : h.secret.mode === 'paste' || h.secret.mode === 'generate';
			const s = h.stored && h.stored.kind === h.kind && h.stored.host === h.host.trim() ? h.stored : undefined;
			return {
				key: h.key, kind: h.kind, host: h.host.trim(), port: Number(h.port) || hopKind(h.kind).port, username: h.username.trim(), label: h.label,
				auth: h.kind === 'socks4' ? 'none' : unsaved && !(h.secret.mode === 'keep') ? (h.auth === 'key' && h.secret.mode === 'generate' && !s ? 'new' : h.auth) : h.auth,
				hostKey: s?.host_key_pending ? 'pending' : s?.host_key ? 'trusted' : 'new',
				result: current && s ? hopResult(current, s, i) : null
			};
		})
	);
	const lastTarget = $derived.by(() => {
		const t = current?.last_test;
		if (!t?.target) return null;
		return { label: t.target, ok: t.target_result ? t.target_result.ok : null, ms: t.target_result?.ms, error: t.target_result?.error };
	});
	const flowing = $derived(!!current?.live.some((l) => l.state === 'up' && (l.jobs ?? 0) > 0));
	const liveFrom = $derived(current?.live.find((l) => l.state === 'up' || l.state === 'broken'));

	// ----- saving -----

	function hopIn(h: DraftHop): RouteHopIn {
		const out: RouteHopIn = { kind: h.kind, host: h.host.trim(), port: h.port.trim() ? Number(h.port) : null, username: h.username.trim(), label: h.label.trim() };
		if (h.id) out.id = h.id;
		if (h.kind === 'socks4') out.auth = 'none';
		else if (h.auth === 'none') out.auth = 'none';
		else if (h.secret.mode === 'password' && h.secret.password) out.password = h.secret.password;
		else if (h.secret.mode === 'paste' && h.secret.private_key.trim()) {
			out.private_key = h.secret.private_key;
			if (h.secret.passphrase) out.passphrase = h.secret.passphrase;
		} else if (h.secret.mode === 'generate') out.generate_key = true;
		if (h.pin.trim()) out.host_key = h.pin.trim();
		return out;
	}
	function problems(d: Draft): string | null {
		if (!d.name.trim()) return 'Give the route a name.';
		if (d.agents && !d.agents.ids.length && !d.agents.tags.length) return 'Pick the agents it works from (or a tag of theirs), or let any agent use it.';
		for (const [i, h] of d.hops.entries()) {
			if (!h.host.trim()) return `Hop ${i + 1} needs a host.`;
			const port = h.port.trim();
			if (port && !(/^\d{1,5}$/.test(port) && Number(port) >= 1 && Number(port) <= 65535)) return `Hop ${i + 1}: the port is a number from 1 to 65535.`;
			if (h.kind === 'ssh' && !h.username.trim()) return `Hop ${i + 1} (SSH) needs a user.`;
			const stored = h.stored?.kind === h.kind && h.stored.has_secret && h.stored.auth === h.auth;
			if (h.auth === 'password' && !h.secret.password && !stored) return `Hop ${i + 1} needs its password.`;
			if (h.auth === 'key' && h.secret.mode === 'paste' && !h.secret.private_key.trim()) return `Paste hop ${i + 1}’s private key, or let Castor make one.`;
			if (h.auth === 'password' && h.kind !== 'ssh' && !h.username.trim()) return `Hop ${i + 1}: a proxy password goes with a user.`;
		}
		return null;
	}
	async function save() {
		if (!draft) return;
		errors = problems(draft);
		if (errors) return;
		const body: NetworkRouteIn = {
			name: draft.name.trim(), description: draft.description, tags: draft.tags, project_ids: draft.projects ?? [],
			hops: draft.hops.map(hopIn), dns: draft.dns, strict: draft.strict, segment: draft.segment.trim(), agents: draft.agents
		};
		saving = true;
		try {
			const saved = draft.id ? await api().routes.update(draft.id, body) : await api().routes.create(body);
			routes = [...routes.filter((r) => r.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name));
			const wasNew = !draft.id;
			if (wasNew) open(saved);
			else refresh(saved);
			const fresh = saved.hops.filter((h) => h.public_key && body.hops.find((b) => b.generate_key && b.host === h.host));
			snackbar.show(fresh.length ? `Saved. Authorize the new key on ${fresh.map((h) => h.host).join(', ')}, then test it.` : wasNew ? 'Route made. Test it to see it works.' : 'Saved');
		} catch (err) {
			errors = err instanceof ApiError ? err.message : String(err);
		} finally {
			saving = false;
		}
	}
	async function remove() {
		if (!draft?.id) return;
		const u = usage ?? (await api().routes.usage(draft.id).catch(() => null));
		const inUse = u && (u.card_count || u.projects.length || u.agents.length);
		const ok = await confirmAction({
			title: `Delete the route “${draft.name}”?`,
			message: inUse
				? `It’s in use (${u!.summary}). Their jobs won’t go direct afterwards: they refuse to run until they’re given another route, or none.`
				: 'Nothing uses it.',
			confirmLabel: 'Delete it', danger: true, icon: 'delete'
		});
		if (!ok) return;
		try {
			await api().routes.remove(draft.id, !!inUse);
			routes = routes.filter((r) => r.id !== draft!.id);
			draft = null;
			goto('/routes', { replaceState: true });
		} catch (err) {
			snackbar.show(err instanceof ApiError ? err.message : String(err));
		}
	}
	async function acceptKey(hopId: string) {
		if (!draft?.id) return;
		const hop = current?.hops.find((h) => h.id === hopId);
		const ok = await confirmAction({
			title: 'Trust the new host key?',
			message: `Only if whoever runs ${hop?.host} confirms it changed. Its fingerprint now is ${hop?.host_key_pending_fingerprint}.`,
			confirmLabel: 'Trust it', icon: 'verified_user'
		});
		if (!ok) return;
		try {
			const r = await api().routes.acceptHostKey(draft.id, hopId);
			routes = routes.map((x) => (x.id === r.id ? r : x));
			for (const h of draft.hops) h.stored = r.hops.find((s) => s.id === h.id) ?? h.stored;
		} catch (err) {
			snackbar.show(err instanceof ApiError ? err.message : String(err));
		}
	}

	// ----- testing -----

	// The saved route's agents (a test uses the saved route).
	const testAgents = $derived(
		agents.filter((a) => a.connected && a.capabilities?.routes && routeAllows(current, a) && (!draft?.projects?.length || a.project_ids == null || a.project_ids.some((p) => draft!.projects!.includes(p))))
	);

	// ----- which agents can use it -----

	let agentTagSuggestions = $state<TagSuggestion[]>([]);
	/** Agents that can bring routes up (SSH hosts and plugin machines can't). */
	const routeAgents = $derived(agents.filter((a) => !a.ssh_host_id && !a.plugin));
	/** Who it works from now, as the draft says. */
	const worksFrom = $derived(draft?.agents ? routeAgents.filter((a) => draft!.agents!.ids.includes(a.id) || draft!.agents!.tags.some((t) => a.tags.includes(t))) : []);
	function limitTo(some: boolean) {
		if (!draft) return;
		draft.agents = some ? (draft.agents ?? { ids: [], tags: [] }) : null;
	}
	function toggleAgent(id: string) {
		if (!draft?.agents) return;
		const ids = draft.agents.ids;
		draft.agents.ids = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
	}
	async function test() {
		if (!draft?.id) return;
		testing = true;
		try {
			const t: RouteTest = await api().routes.test(draft.id, { agent_id: testAgent || null, target: testTarget.trim() || null });
			routes = routes.map((r) => (r.id === draft!.id ? { ...r, last_test: t } : r));
			if (t.error) snackbar.show(t.error);
		} catch (err) {
			snackbar.show(err instanceof ApiError ? err.message : String(err));
		} finally {
			testing = false;
		}
	}

	async function makeFromSsh() {
		try {
			const r = await api().routes.fromSshHost({ ssh_host_id: fromSsh.host, name: fromSsh.name.trim(), project_ids: [] });
			routes = [...routes.filter((x) => x.id !== r.id), r].sort((a, b) => a.name.localeCompare(b.name));
			fromSshOpen = false;
			open(r);
		} catch (err) {
			snackbar.show(err instanceof ApiError ? err.message : String(err));
		}
	}

	// ----- the list -----

	const shown = $derived(
		routes.filter((r) => {
			const q = filter.trim().toLowerCase();
			return !q || `${r.name} ${r.description} ${r.label} ${r.tags.join(' ')} ${r.segment}`.toLowerCase().includes(q);
		})
	);
	const projectCodes = (ids: string[]) => (ids.length ? ids.map((id) => projects.find((p) => p.id === id)?.codename ?? '…').join(', ') : 'Every project');
	const status = $derived(current ? routeState(current, agentName) : null);
</script>

<svelte:head><title>Routes · {brand.windowName}</title></svelte:head>

<TopAppBar title="Routes" subtitle="How jobs reach networks the agent can’t reach directly: through jumpboxes and proxies">
	{#snippet actions()}
		<label class="hidden items-center gap-2 rounded-full bg-surface-container-high px-4 md:flex">
			<Icon name="search" size={20} class="text-on-surface-variant" />
			<input class="h-10 w-40 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter routes" bind:value={filter} autocomplete="off" />
		</label>
		<LibraryTransfer noun="routes" request={{ routes: routes.map((r) => r.id), label: 'castor-routes' }} count={routes.length} secrets
			onimported={async () => (routes = await api().routes.list())} />
		{#if editable}
			{#if isAdmin() && sshHosts.length}<Button variant="tonal" icon="terminal" class="ml-2" onclick={() => { fromSsh = { host: sshHosts[0].id, name: '' }; fromSshOpen = true; }}>From an SSH host</Button>{/if}
			<Button icon="add" class="ml-2" onclick={() => choose(null)}>New route</Button>
		{/if}
	{/snippet}
</TopAppBar>

<div class="grid min-h-0 flex-1 gap-4 overflow-hidden px-4 pb-4 md:grid-cols-[minmax(260px,320px)_minmax(0,1fr)] md:px-6">
	<nav class="flex min-h-0 flex-col gap-1 overflow-auto {draft ? 'max-md:hidden' : ''}" aria-label="Routes">
		{#each shown as r (r.id)}
			{@const st = routeState(r, agentName)}
			<button
				type="button"
				class="state-layer focus-ring flex flex-col gap-1 rounded-md px-3 py-2.5 text-left {draft?.id === r.id ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface'}"
				aria-current={draft?.id === r.id ? 'true' : undefined}
				onclick={() => choose(r)}
				in:enter={{ enabled: false }}
				out:leave
			>
				<span class="flex items-center gap-2">
					<span class="min-w-0 flex-1 truncate title-small">{r.name}</span>
					<Icon name={st.icon} size={18} class={TONE_CLASS[st.tone]} />
				</span>
				<span class="flex min-w-0 items-center gap-0.5 text-on-surface-variant" aria-label={r.label}>
					<Icon name="dns" size={14} />
					{#each r.hops as h (h.id)}
						<Icon name="chevron_right" size={14} class="opacity-60" />
						<Icon name={hopKind(h.kind).icon} size={14} />
						<span class="max-w-24 truncate font-mono text-[11px]">{h.host}</span>
					{/each}
				</span>
				<span class="truncate body-small {TONE_CLASS[st.tone]}">{st.text}</span>
				<span class="truncate label-small text-on-surface-variant">{projectCodes(r.project_ids)}{r.tags.length ? ` · ${r.tags.join(', ')}` : ''}</span>
			</button>
		{:else}
			<div class="flex flex-col gap-2 rounded-lg bg-surface-container-low p-4">
				<p class="title-small text-on-surface">{routes.length ? 'No route matches' : 'No routes yet'}</p>
				<p class="body-small text-on-surface-variant">A route is the way to a network your agents can’t reach on their own: through a jumpbox over SSH, a SOCKS proxy on a box you got into, an HTTP proxy, or several in a row. Cards, playbook runs and projects pick one; Castor gets each tool’s traffic through it.</p>
				{#if editable && !routes.length}<div><Button icon="add" onclick={() => choose(null)}>Make the first one</Button></div>{/if}
			</div>
		{/each}
	</nav>

	<main class="flex min-h-0 flex-col overflow-auto">
		{#if draft}
			<div class="flex flex-col gap-5 pb-24" in:enter>
				<div class="flex items-start gap-3">
					<button type="button" class="state-layer focus-ring -ml-2 rounded-full p-2 md:hidden" aria-label="Back to the routes" onclick={() => choose(null).then(() => (draft = null))}><Icon name="arrow_back" /></button>
					<div class="flex min-w-0 flex-1 flex-col gap-3">
						<TextField label="Name" bind:value={draft.name} placeholder="DMZ through jump01" disabled={!editable} />
						<TextField label="What it’s for (optional)" bind:value={draft.description} placeholder="Into the client’s server VLAN from the jumpbox they gave us" disabled={!editable} />
					</div>
				</div>

				{#if status}
					<div class="flex items-start gap-2 rounded-md bg-surface-container px-3 py-2" role="status">
						<Icon name={status.icon} size={20} class={TONE_CLASS[status.tone]} />
						<div class="min-w-0">
							<p class="title-small {TONE_CLASS[status.tone]}">{status.text}</p>
							{#if status.detail}<p class="body-small break-words text-on-surface-variant">{status.detail}</p>{/if}
						</div>
					</div>
				{/if}

				<Card variant="outlined" class="p-4">
					<div class="mb-1 flex items-center gap-2">
						<h2 class="min-w-0 flex-1 title-medium text-on-surface">The way through</h2>
						{#if editable}<Button variant="text" icon="add" onclick={() => insertHop(draft!.hops.length)}>Add a hop</Button>{/if}
					</div>
					<p class="mb-3 body-small text-on-surface-variant">From the agent outwards: each hop is reached through the one before it, and the last one reaches the targets. Click a hop to change it.</p>
					<RouteChain hops={chainHops} selected={selectedHop} from={liveFrom ? agentName(liveFrom.agent_id) : current?.last_test ? current.last_test.agent_name : 'The agent'}
						target={lastTarget} {flowing} editable={editable}
						onselect={(i) => (selectedHop = selectedHop === i ? null : i)} oninsert={insertHop} />
				</Card>

				{#if selectedHop !== null && draft.hops[selectedHop]}
					{#key draft.hops[selectedHop].key}
						<div in:enter>
							<HopEditor bind:hop={draft.hops[selectedHop]} index={selectedHop} count={draft.hops.length} readonly={!editable}
								onremove={() => removeHop(selectedHop!)} onmove={(d) => moveHop(selectedHop!, d)}
								onaccept={draft.id && draft.hops[selectedHop].stored?.host_key_pending ? () => acceptKey(draft!.hops[selectedHop!].stored!.id) : undefined} />
						</div>
					{/key}
				{/if}

				<section class="flex flex-col gap-4" aria-labelledby="route-agents">
					<h2 id="route-agents" class="title-medium text-on-surface">Which agents can use it</h2>
					<div class="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Which agents can use it">
						{#each [{ v: false, t: 'Any agent', d: 'Every agent can reach the first hop.', i: 'public' }, { v: true, t: 'Only some agents', d: 'The first hop is only reachable from some: on a VPN, or in a segment. Jobs on the route only go to them.', i: 'vpn_lock' }] as o (o.t)}
							{@const on = !!draft.agents === o.v}
							<button type="button" role="radio" aria-checked={on} disabled={!editable}
								class="state-layer focus-ring flex items-start gap-3 rounded-md border p-3 text-left {on ? 'border-primary bg-primary-container/40' : 'border-outline-variant'}"
								onclick={() => limitTo(o.v)}>
								<Icon name={o.i} size={20} class={on ? 'text-primary' : 'text-on-surface-variant'} />
								<span class="min-w-0"><span class="block title-small text-on-surface">{o.t}</span><span class="block body-small text-on-surface-variant">{o.d}</span></span>
							</button>
						{/each}
					</div>
					{#if draft.agents}
						<TagInput label="Agents tagged (any of)" bind:value={draft.agents.tags} suggestions={agentTagSuggestions} placeholder="vpn-acme" disabled={!editable} />
						<div class="flex flex-col gap-2">
							<span class="label-large text-on-surface">And these agents</span>
							<div class="flex flex-wrap gap-2">
								{#each routeAgents as a (a.id)}
									{@const picked = draft.agents.ids.includes(a.id)}
									{@const byTag = !picked && draft.agents.tags.some((t) => a.tags.includes(t))}
									<Chip variant="filter" selected={picked || byTag} icon={picked ? 'check' : byTag ? 'sell' : undefined} disabled={!editable || byTag}
										title={byTag ? 'Has one of the tags' : a.connected ? a.hostname : 'Offline'} onclick={() => toggleAgent(a.id)}>
										{a.name}{a.connected ? '' : ' (offline)'}
									</Chip>
								{/each}
								{#each draft.agents.ids.filter((id) => !routeAgents.some((a) => a.id === id)) as id (id)}
									<Chip variant="filter" selected icon="help" disabled={!editable} title="No such agent here (forgotten, or the route came from another Castor)" onclick={() => toggleAgent(id)}>An unknown agent</Chip>
								{/each}
							</div>
						</div>
						<p class="flex items-start gap-1.5 body-small {worksFrom.length ? 'text-on-surface-variant' : 'text-error'}">
							<Icon name={worksFrom.length ? 'alt_route' : 'block'} size={16} class="mt-px shrink-0 {worksFrom.length ? 'text-primary' : ''}" />
							{#if worksFrom.length}
								Works from {worksFrom.map((a) => a.name).join(', ')} ({worksFrom.filter((a) => a.connected).length} connected). Cards and runs on it wait for one of them.
							{:else}
								No agent here matches yet: jobs on it would wait until one does.
							{/if}
						</p>
					{/if}
				</section>

				<section class="flex flex-col gap-4" aria-labelledby="route-how">
					<h2 id="route-how" class="title-medium text-on-surface">How jobs use it</h2>
					<div class="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Where names are resolved">
						{#each [{ v: 'remote', t: 'Names resolved at the far end', d: 'The last hop looks names up, so internal names work and nothing leaks from here. Recommended.', i: 'travel_explore' }, { v: 'local', t: 'Names resolved here', d: 'The agent looks names up before connecting through the route.', i: 'home_pin' }] as o (o.v)}
							<button type="button" role="radio" aria-checked={draft.dns === o.v} disabled={!editable}
								class="state-layer focus-ring flex items-start gap-3 rounded-md border p-3 text-left {draft.dns === o.v ? 'border-primary bg-primary-container/40' : 'border-outline-variant'}"
								onclick={() => (draft!.dns = o.v as 'remote' | 'local')}>
								<Icon name={o.i} size={20} class={draft.dns === o.v ? 'text-primary' : 'text-on-surface-variant'} />
								<span class="min-w-0"><span class="block title-small text-on-surface">{o.t}</span><span class="block body-small text-on-surface-variant">{o.d}</span></span>
							</button>
						{/each}
					</div>
					<div class="flex flex-col gap-1">
						<Switch bind:checked={draft.strict} disabled={!editable} label="Only through the route" />
						<p class="body-small text-on-surface-variant">
							{#if draft.strict}
								Jobs run in a container whose only way out is the route: anything that can’t go through it (UDP, pings, a tool that ignores proxies) is dropped, not sent from the agent’s own network. Commands in an agent’s shell can’t be held to that, so they’re refused.
							{:else}
								Jobs in an agent’s shell can use it too, but whatever doesn’t go through the route goes out from the agent’s own network.
							{/if}
						</p>
					</div>
					<div class="grid gap-3 sm:grid-cols-2">
						<TextField label="Seen from (vantage point)" bind:value={draft.segment} placeholder={draft.name || 'the route’s name'} disabled={!editable}
							supporting="What jobs through it find is recorded as seen from here (Assets → Reachability)" />
						<TagInput label="Tags" bind:value={draft.tags} suggestions={tagSuggestions} placeholder="Add a tag" disabled={!editable} />
					</div>
					{#if editable}
						<AgentProjectsPicker bind:value={draft.projects} limited={!isAdmin()} listLabel="Can use it"
							supporting="The projects whose cards, runs and defaults may use it. A route reaches into a client’s network: keep it to theirs." />
					{:else}
						<p class="body-small text-on-surface-variant">For {projectCodes(draft.projects ?? [])}</p>
					{/if}
				</section>

				{#if draft.id}
					<section class="flex flex-col gap-3" aria-labelledby="route-test">
						<h2 id="route-test" class="title-medium text-on-surface">Test it</h2>
						<p class="body-small text-on-surface-variant">An agent brings the route up and tries it hop by hop: each hop’s time, or the hop where it breaks and why. A host key seen for the first time is trusted then.</p>
						<div class="grid items-start gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
							<Select label="From" bind:value={testAgent} options={[{ value: '', label: 'Any agent that can' }, ...testAgents.map((a) => ({ value: a.id, label: a.name, description: a.hostname }))]} />
							<TextField label="Then reach (optional)" bind:value={testTarget} mono placeholder="10.0.20.5:445" supporting="host:port beyond the last hop" />
							<Button icon="network_check" loading={testing} disabled={!editable || dirty || !testAgents.length} onclick={test} class="sm:mt-2"
								title={dirty ? 'Save first: the test uses the saved route' : !testAgents.length ? 'No connected agent can bring routes up' : ''}>Test</Button>
						</div>
						{#if !testAgents.length}<p class="body-small text-on-surface-variant">No connected agent can bring it up (agents need Castor 1.13 or later).</p>{/if}
					</section>

					{#if usage}
						<section class="flex flex-col gap-2" aria-labelledby="route-usage">
							<h2 id="route-usage" class="title-medium text-on-surface">Used by</h2>
							{#if !usage.summary}
								<p class="body-small text-on-surface-variant">Nothing yet. A card picks it next to where it runs, a playbook run when it starts, a project as its default (project settings), an agent for everything it runs (Agents).</p>
							{:else}
								<p class="body-small text-on-surface-variant">{usage.summary}</p>
								<ul class="flex flex-wrap gap-2">
									{#each usage.cards.slice(0, 12) as c (c.id)}
										<li><a class="state-layer focus-ring inline-flex h-8 items-center gap-1 rounded-sm border border-outline-variant px-2 label-large text-on-surface" href={`/projects/${c.project_id}?task=${c.id}`}><Icon name="task_alt" size={16} />{c.title}</a></li>
									{/each}
									{#each usage.agents as a (a.id)}
										<li><a class="state-layer focus-ring inline-flex h-8 items-center gap-1 rounded-sm border border-outline-variant px-2 label-large text-on-surface" href="/agents"><Icon name="dns" size={16} />{a.name}</a></li>
									{/each}
								</ul>
							{/if}
						</section>
					{/if}
				{/if}
			</div>

			{#if editable}
				<div class="sticky bottom-0 z-10 mt-auto flex items-center gap-2 border-t border-outline-variant bg-surface py-3">
					{#if draft.id}<Button variant="text" icon="delete" onclick={remove}>Delete</Button>{/if}
					<span class="min-w-0 flex-1 truncate body-small text-error" role={errors ? 'alert' : undefined}>{errors ?? ''}</span>
					{#if dirty}
						<Button variant="text" onclick={() => (current ? open(current, false) : (draft = null))}>Discard</Button>
					{/if}
					<Button icon="check" loading={saving} disabled={!dirty} onclick={save}>{draft.id ? 'Save' : 'Make the route'}</Button>
				</div>
			{/if}
		{:else}
			<div class="mx-auto flex max-w-2xl flex-col gap-4 py-6 max-md:hidden">
				<h2 class="headline-small text-on-surface">Getting a tool’s traffic where it needs to go</h2>
				<p class="body-medium text-on-surface-variant">Pick a route on a card (next to where it runs), a playbook run, a project (its default) or an agent (everything it runs). Castor brings the chain up on the agent, checks each hop, and sends the job through it in the way that tool takes best:</p>
				<ul class="grid gap-3 sm:grid-cols-2">
					{#each (['native', 'env', 'hooked', 'transparent'] as const) as m (m)}
						<li class="rounded-md bg-surface-container-low p-3">
							<p class="title-small text-on-surface first-letter:uppercase">{METHOD_WORDS[m].label}</p>
							<p class="body-small text-on-surface-variant">{METHOD_WORDS[m].detail}.</p>
						</li>
					{/each}
				</ul>
				<p class="body-small text-on-surface-variant">Nothing about a route is written into a card’s command, its passwords and keys stay in Castor’s vault, and a scan that can’t go through one (a SYN scan, a ping sweep, UDP) is refused with the reason instead of quietly going out from the agent’s own network.</p>
				<div class="flex flex-wrap gap-2">
					{#each HOP_KINDS as k (k.value)}<span class="inline-flex items-center gap-1 rounded-sm bg-surface-container px-2 py-1 label-medium text-on-surface-variant"><Icon name={k.icon} size={16} />{k.label}</span>{/each}
				</div>
			</div>
		{/if}
	</main>
</div>

<Dialog open={fromSshOpen} title="A route from an SSH host" icon="terminal" onclose={() => (fromSshOpen = false)}>
	<div class="flex flex-col gap-4 pt-2">
		<p class="body-medium text-on-surface-variant">Its jump hosts (from the shared SSH config) and the host itself become SSH hops, signing in with the host’s key and keeping the host keys Castor already trusts.</p>
		<Select label="SSH host" bind:value={fromSsh.host} options={sshHosts.map((h) => ({ value: h.id, label: h.name, description: (h.route ?? []).join(' → ') }))} />
		<TextField label="Route name" bind:value={fromSsh.name} placeholder="Through {sshHosts.find((h) => h.id === fromSsh.host)?.name ?? 'the host'}" />
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (fromSshOpen = false)}>Cancel</Button>
		<Button icon="check" disabled={!fromSsh.host || !fromSsh.name.trim()} onclick={makeFromSsh}>Make it</Button>
	{/snippet}
</Dialog>
