/** Network routes in the browser: words, icons and states (pure). */
import type { HopKind, NetworkRoute, RouteHop, RouteMethod, RoutePlan } from './api';
import { relativeTime } from './format';

export const HOP_KINDS: { value: HopKind; label: string; icon: string; port: number; hint: string }[] = [
	{ value: 'ssh', label: 'SSH jump', icon: 'terminal', port: 22, hint: 'A jumpbox: signs in with a key or a password' },
	{ value: 'socks5', label: 'SOCKS5', icon: 'hub', port: 1080, hint: 'A SOCKS5 proxy, with or without a password' },
	{ value: 'http', label: 'HTTP proxy', icon: 'language', port: 8080, hint: 'An HTTP proxy that allows CONNECT' },
	{ value: 'socks4', label: 'SOCKS4', icon: 'hub', port: 1080, hint: 'An older SOCKS4(a) proxy: a user id, no password' }
];

export function hopKind(kind: HopKind) {
	return HOP_KINDS.find((k) => k.value === kind) ?? HOP_KINDS[0];
}

export function hopAddress(h: { host: string; port: number | null | undefined }): string {
	const host = h.host.includes(':') ? `[${h.host}]` : h.host;
	return h.port ? `${host}:${h.port}` : host;
}

export const METHOD_WORDS: Record<RouteMethod, { label: string; detail: string }> = {
	native: { label: 'its own proxy setting', detail: 'the tool is told where the route starts (a flag such as -proxy)' },
	env: { label: 'proxy variables', detail: 'ALL_PROXY, HTTPS_PROXY and HTTP_PROXY point at the route' },
	hooked: { label: 'proxychains', detail: 'every connection the program makes is sent through the route' },
	transparent: { label: 'everything in its network', detail: 'the job joins the route’s network, where every connection and name lookup goes through it' },
	proxy: { label: 'the agent’s proxy', detail: 'the agent sends each request through the route' }
};

export type RouteState = { tone: 'ok' | 'warn' | 'error' | 'idle'; icon: string; text: string; detail?: string };

/** One line about how a route is doing: live first, then a changed host key, then its last test. */
export function routeState(route: NetworkRoute, agentName: (id: string) => string = (id) => id): RouteState {
	if (route.pending_keys) {
		const i = route.hops.findIndex((h) => h.host_key_pending);
		return { tone: 'error', icon: 'gpp_maybe', text: `Hop ${i + 1}’s host key changed`, detail: 'Jobs wait until someone checks and accepts it' };
	}
	const up = route.live.filter((l) => l.state === 'up');
	const broken = route.live.find((l) => l.state === 'broken' || l.state === 'error');
	if (broken) {
		const where = broken.broken?.index != null ? `hop ${broken.broken.index + 1}` : 'the route';
		return { tone: 'error', icon: 'link_off', text: `Broken at ${where} on ${agentName(broken.agent_id)}`,
			detail: broken.broken?.error ?? broken.error ?? undefined };
	}
	if (up.length) {
		const jobs = up.reduce((n, l) => n + (l.jobs ?? 0), 0);
		return { tone: 'ok', icon: 'sync_alt', text: `Up on ${up.map((l) => agentName(l.agent_id)).join(', ')}`,
			detail: jobs ? `${jobs} job${jobs === 1 ? '' : 's'} going through it` : 'Idle; it stops after a while' };
	}
	const t = route.last_test;
	if (!t) return { tone: 'idle', icon: 'help', text: 'Not tested yet' };
	const when = relativeTime(new Date(t.at * 1000).toISOString());
	if (t.error) return { tone: 'error', icon: 'error', text: `Couldn’t be tested ${when}`, detail: t.error };
	const bad = t.hops.findIndex((h) => h.ok === false);
	if (bad >= 0) return { tone: 'error', icon: 'link_off', text: `Broke at hop ${bad + 1} ${when}`, detail: t.hops[bad].error };
	if (t.target && t.target_result && !t.target_result.ok)
		return { tone: 'warn', icon: 'warning', text: `Works, but didn’t reach ${t.target}`, detail: t.target_result.error };
	return { tone: 'ok', icon: 'check_circle', text: `Worked ${when} from ${t.agent_name}` };
}

export const TONE_CLASS: Record<RouteState['tone'], string> = {
	ok: 'text-primary',
	warn: 'text-tertiary',
	error: 'text-error',
	idle: 'text-on-surface-variant'
};

/** A hop's latest result: the live state on an agent if it's up there, else the last test. */
export function hopResult(route: NetworkRoute, hop: RouteHop, index: number): { ok: boolean | null; ms?: number; error?: string } | null {
	for (const l of route.live) {
		const h = l.hops?.[hop.id];
		if (h) return { ok: h.state === 'up' ? true : h.state === 'error' || h.state === 'down' ? false : null, ms: h.ms ?? undefined, error: h.error ?? undefined };
	}
	const t = route.last_test?.hops.find((h) => h.hop_id === hop.id) ?? route.last_test?.hops[index];
	return t ? { ok: t.ok, ms: t.ms, error: t.error } : null;
}

/** Whether a job may take the route from this agent (server: network_routes.allows; keep them the
 *  same): every agent, unless the route names its agents, or tags one of which the agent has. */
export function routeAllows(route: NetworkRoute | null | undefined, agent: { id: string; tags: string[] }): boolean {
	if (!route?.agents) return true;
	return route.agents.ids.includes(agent.id) || route.agents.tags.some((t) => agent.tags.includes(t));
}

/** Only the agents every one of these routes works from. */
export function agentsForRoutes<A extends { id: string; tags: string[] }>(agents: A[], routes: (NetworkRoute | null | undefined)[]): A[] {
	return agents.filter((a) => routes.every((r) => routeAllows(r, a)));
}

/** “agents tagged vpn-acme or scanner-01”, for hints. */
export function routeAgentsWords(route: NetworkRoute, names: (id: string) => string): string {
	if (!route.agents) return 'any agent';
	const parts: string[] = [];
	if (route.agents.tags.length) parts.push(`agents tagged ${route.agents.tags.join(' or ')}`);
	if (route.agents.ids.length) parts.push(route.agents.ids.map(names).join(', '));
	return parts.join(' or ') || 'no agent';
}

/** The options of a route picker for a card, run or project. */
export function routeOptions(routes: NetworkRoute[], opts: { inherit?: string | null; direct?: boolean } = {}) {
	const out: { value: string; label: string; description?: string; group?: string; keywords?: string }[] = [];
	if (opts.inherit !== undefined) out.push({ value: '', label: opts.inherit ? `Project default (${opts.inherit})` : 'Project default (direct)' });
	if (opts.direct !== false) out.push({ value: 'none', label: 'Direct', description: 'From the agent’s own network, no route' });
	for (const r of routes) out.push({ value: r.id, label: r.name, description: r.label, keywords: r.tags.join(' '), group: 'Routes' });
	return out;
}

/** How a card's runs go through a route, in a sentence (or why they can't). */
export function describePlan(plan: RoutePlan | null): { ok: boolean; text: string } | null {
	if (!plan?.route) return null;
	const c = plan.container, h = plan.host;
	const first = (w?: RouteMethod[]) => (w?.length ? METHOD_WORDS[w[0]].label : '');
	if (plan.runs_in === 'host') {
		if (h?.ways?.length) return { ok: true, text: `Goes through “${plan.route.name}” by ${first(h.ways)}` };
		return { ok: false, text: h?.error ?? 'It can’t go through this route' };
	}
	if (plan.runs_in === 'container' || !h?.ways) {
		if (c?.ways?.length) return { ok: true, text: `Goes through “${plan.route.name}” by ${first(c.ways)}${plan.route.dns === 'local' ? '' : ', names resolved at the far end'}` };
		return { ok: false, text: c?.error ?? h?.error ?? 'It can’t go through this route' };
	}
	if (c?.ways?.length && h?.ways?.length) {
		if (c.ways[0] === h.ways[0]) return { ok: true, text: `Goes through “${plan.route.name}” by ${first(c.ways)}` };
		return { ok: true, text: `Goes through “${plan.route.name}”: in a container by ${first(c.ways)}, in an agent’s shell by ${first(h.ways)}` };
	}
	if (c?.ways?.length && h?.error) return { ok: true, text: `Goes through “${plan.route.name}” by ${first(c.ways)} (in a container; ${h.error.charAt(0).toLowerCase()}${h.error.slice(1)})` };
	return { ok: true, text: `Goes through “${plan.route.name}” by ${first(c?.ways ?? h?.ways)}` };
}
