import type { Agent, AgentSelector } from '$lib/api';

/** Where a card or a playbook run goes: as a Select value and back, and in words. Values:
 *  '' any agent, the agent's id, `tag:<tags>` one agent with them, `all:<tags>` every
 *  connected agent with them (tags joined by commas). */
export function selectorValue(s: AgentSelector | null | undefined): string {
	if (!s) return '';
	if (s.agent_id) return s.agent_id;
	if (s.tags?.length) return `${s.all ? 'all' : 'tag'}:${s.tags.join(',')}`;
	return '';
}

export function selectorFrom(value: string): AgentSelector {
	if (!value) return {};
	const m = /^(tag|all):(.+)$/.exec(value);
	if (!m) return { agent_id: value };
	const tags = m[2].split(',').filter(Boolean);
	return m[1] === 'all' ? { tags, all: true } : { tags };
}

/** The agents that may run a project's jobs (an agent limited to other projects isn't offered). */
export function agentsFor(agents: Agent[], projectId: string | null | undefined): Agent[] {
	if (!projectId) return agents;
	return agents.filter((a) => a.project_ids == null || a.project_ids.includes(projectId));
}

const hasTags = (a: Agent, tags: string[]) => tags.every((t) => a.tags.includes(t));

export function describeSelector(s: AgentSelector | null | undefined, agents: Agent[]): string {
	if (!s || (!s.agent_id && !s.tags?.length)) return 'any available agent';
	if (s.agent_id) return agents.find((a) => a.id === s.agent_id)?.name ?? 'an agent that was removed';
	const tags = s.tags!.join(', ');
	if (!s.all) return `any agent tagged ${tags}`;
	const n = agents.filter((a) => a.connected && hasTags(a, s.tags!)).length;
	return `every agent tagged ${tags} (${n} connected)`;
}

export interface SelectOption {
	value: string;
	label: string;
	description?: string;
	group?: string;
	keywords?: string;
}

/** Any agent; per tag, any one agent with it and (with `every`) every agent with it; then
 *  each agent. A selector with several tags that isn't one of these is kept as an option. */
export function selectorOptions(agents: Agent[], current?: AgentSelector | null, every = true): SelectOption[] {
	const tags = [...new Set(agents.flatMap((a) => a.tags))].sort();
	const options: SelectOption[] = [{ value: '', label: 'Any available agent' }];
	for (const tag of tags) {
		const all = agents.filter((a) => hasTags(a, [tag]));
		const online = all.filter((a) => a.connected).length;
		options.push({
			value: `tag:${tag}`,
			label: `Any agent tagged ${tag}`,
			description: `One of ${all.length} (${online} connected)`,
			group: 'By tag',
			keywords: tag
		});
		if (every)
			options.push({
				value: `all:${tag}`,
				label: `Every agent tagged ${tag}`,
				description: `Runs once on each connected one: ${all.filter((a) => a.connected).map((a) => a.name).join(', ') || 'none connected now'}`,
				group: 'By tag',
				keywords: tag
			});
	}
	const value = selectorValue(current);
	if (value && !options.some((o) => o.value === value) && current?.tags?.length) {
		options.push({ value, label: describeSelector(current, agents).replace(/^./, (c) => c.toUpperCase()), group: 'By tag' });
	}
	for (const a of agents)
		options.push({
			value: a.id,
			label: `${a.name}${a.connected ? '' : ' (offline)'}`,
			description: [a.ssh_host_id ? 'SSH host' : a.hostname, a.os, a.tags.join(', ')].filter(Boolean).join(' · '),
			group: 'Agent',
			keywords: a.tags.join(' ')
		});
	return options;
}

/** Where an HTTP action's requests go out from: the Castor server (''), or through an agent
 *  (one, or any one with a tag) that sends HTTP requests, from its network. SSH hosts and agents
 *  started with --no-http (or older ones) can't. */
export function httpSenderOptions(agents: Agent[], current?: AgentSelector | null): SelectOption[] {
	const able = agents.filter((a) => a.capabilities?.http);
	const options: SelectOption[] = [{ value: '', label: 'The Castor server', description: 'From the server’s own network' }];
	for (const tag of [...new Set(able.flatMap((a) => a.tags))].sort()) {
		const all = able.filter((a) => hasTags(a, [tag]));
		options.push({
			value: `tag:${tag}`,
			label: `Any agent tagged ${tag}`,
			description: `From its network: one of ${all.length} (${all.filter((a) => a.connected).length} connected)`,
			group: 'Through an agent',
			keywords: tag
		});
	}
	for (const a of able)
		options.push({
			value: a.id,
			label: `${a.name}${a.connected ? '' : ' (offline)'}`,
			description: `From its network · ${[a.hostname, a.tags.join(', ')].filter(Boolean).join(' · ')}`,
			group: 'Through an agent',
			keywords: a.tags.join(' ')
		});
	const value = selectorValue(current);
	if (value && !options.some((o) => o.value === value))
		options.push({ value, label: describeSelector(current, agents).replace(/^./, (c) => c.toUpperCase()), description: 'Doesn’t send HTTP requests now', group: 'Through an agent' });
	return options;
}

/** An HTTP action's sender, in words. */
export function describeSender(s: AgentSelector | null | undefined, agents: Agent[]): string {
	if (!s || (!s.agent_id && !s.tags?.length)) return 'the Castor server';
	return `the Castor server, through ${describeSelector({ ...s, all: false }, agents)}`;
}
