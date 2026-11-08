/**
 * The library as a rack: each tool is a folder and its commands are its verbs. Pure helpers
 * shared by the Library page and (later) the playbook editor's step palette.
 */
import type { CommandDefinition, ToolAgentState, ToolDefinition } from '$lib/api';

/** Commands that name an image, or nothing, instead of a tool. */
export const NO_TOOL = '';

export interface Folder {
	/** The tool id, or NO_TOOL. */
	id: string;
	name: string;
	tool: ToolDefinition | null;
	commands: CommandDefinition[];
}

const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });

/** Folders sorted by name (case-insensitive), "Images and shell" last; a tool without commands still gets one. */
export function folders(commands: CommandDefinition[], tools: ToolDefinition[]): Folder[] {
	const toolIds = new Set(tools.map((t) => t.id));
	const out: Folder[] = tools.map((t) => ({ id: t.id, name: t.name, tool: t, commands: [] }));
	const rest: Folder = { id: NO_TOOL, name: 'Images and shell', tool: null, commands: [] };
	const index = new Map(out.map((f) => [f.id, f]));
	for (const c of commands) {
		const tool = c.spec.tool && toolIds.has(c.spec.tool) ? c.spec.tool : null;
		(tool ? index.get(tool)! : rest).commands.push(c);
	}
	for (const f of out) f.commands.sort(byName);
	rest.commands.sort(byName);
	out.sort(byName);
	return rest.commands.length ? [...out, rest] : out;
}

/** What a command takes and gives: its list (or required) arguments → its parsed output types. */
export function signature(c: CommandDefinition): { inputs: string[]; outputs: string[] } {
	const lists = c.spec.args.filter((a) => a.type === 'list').map((a) => a.name);
	const inputs = lists.length ? lists : c.spec.args.filter((a) => a.required).map((a) => a.name);
	const outputs = c.spec.outputs.filter((o) => o.parser).map((o) => o.type || o.parser || o.name);
	return { inputs, outputs: [...new Set(outputs)] };
}

export const needsCredentials = (c: CommandDefinition) => (c.spec.vars?.length ?? 0) > 0;
export const usageCount = (c: CommandDefinition) => (c.card_count ?? 0) + (c.playbooks?.length ?? 0);

export type Readiness = 'ready' | 'partial' | 'unprepared' | 'failed' | 'cannot' | 'preparing' | 'no-agents';

/** One agent's state for a tool, in words. */
export function agentState(a: ToolAgentState): { text: string; icon: string; tone: 'good' | 'busy' | 'bad' | 'idle' } {
	if (a.state === 'preparing') return { text: 'preparing…', icon: 'progress_activity', tone: 'busy' };
	if (a.state === 'failed') return { text: 'prepare failed', icon: 'error', tone: 'bad' };
	if (!a.way) return { text: 'can’t run it', icon: 'block', tone: 'idle' };
	if (a.ready) return { text: a.way === 'build' ? 'built, ready' : 'ready', icon: 'check_circle', tone: 'good' };
	if (a.way === 'build') return { text: 'builds on first use', icon: 'construction', tone: 'idle' };
	if (a.way === 'image') return { text: 'pulls the image', icon: 'download', tone: 'idle' };
	return { text: 'on its host', icon: 'computer', tone: 'idle' };
}

/** How ready a tool is across the connected agents, for a folder header. */
export function readiness(t: ToolDefinition): { state: Readiness; label: string } {
	const agents = t.agents;
	if (!agents.length) return { state: 'no-agents', label: 'no agents' };
	if (agents.some((a) => a.state === 'preparing')) return { state: 'preparing', label: 'preparing…' };
	if (agents.some((a) => a.state === 'failed')) return { state: 'failed', label: 'prepare failed' };
	const able = agents.filter((a) => a.way);
	if (!able.length) return { state: 'cannot', label: 'no agent can run it' };
	const ready = able.filter((a) => a.ready).length;
	if (ready === able.length) return { state: 'ready', label: able.length === 1 ? 'ready' : `ready on ${ready}` };
	if (ready) return { state: 'partial', label: `ready on ${ready} of ${able.length}` };
	return { state: 'unprepared', label: 'not prepared' };
}

/** How the tool reaches agents: a published image, a recipe they build, or their own host. */
export function provision(t: ToolDefinition): { icon: string; label: string }[] {
	const out = [];
	if (t.spec.image) out.push({ icon: 'download', label: 'image' });
	if (t.spec.build) out.push({ icon: 'construction', label: 'recipe' });
	if (t.spec.host) out.push({ icon: 'computer', label: 'host' });
	return out;
}

export interface Filters {
	q: string;
	mine: boolean;
	credentials: boolean;
	unused: boolean;
	notReady: boolean;
	tag: string | null;
}

export const NO_FILTERS: Filters = { q: '', mine: false, credentials: false, unused: false, notReady: false, tag: null };
export const filtering = (f: Filters) => !!(f.q.trim() || f.mine || f.credentials || f.unused || f.notReady || f.tag);

function words(q: string) {
	return q.toLowerCase().split(/\s+/).filter(Boolean);
}

/** Every word of the search must appear somewhere in the command (or its tool's name). */
export function commandMatches(c: CommandDefinition, f: Filters, toolName = ''): boolean {
	if (f.mine && c.origin !== 'user') return false;
	if (f.credentials && !needsCredentials(c)) return false;
	if (f.unused && usageCount(c) > 0) return false;
	if (f.tag && !c.tags.includes(f.tag)) return false;
	const hay = [c.name, c.id, c.description, toolName, ...c.tags, ...signature(c).outputs].join(' ').toLowerCase();
	return words(f.q).every((w) => hay.includes(w));
}

export function toolMatches(t: ToolDefinition, f: Filters): boolean {
	if (f.mine && t.origin !== 'user') return false;
	if (f.tag && !t.spec.tags.includes(f.tag)) return false;
	const hay = [t.name, t.id, t.description, ...t.spec.tags].join(' ').toLowerCase();
	return words(f.q).every((w) => hay.includes(w));
}

/** The folders and commands left once the filters apply. A folder whose tool matches keeps all
 *  its (otherwise matching) commands; a folder with no match at all drops out. */
export function filterFolders(all: Folder[], f: Filters): Folder[] {
	if (!filtering(f)) return all;
	const commandFacets = f.credentials || f.unused;
	const out: Folder[] = [];
	for (const folder of all) {
		if (f.notReady && (!folder.tool || ['ready', 'no-agents'].includes(readiness(folder.tool).state))) continue;
		const toolHit = !!folder.tool && !commandFacets && toolMatches(folder.tool, f);
		const facetsOnly = { ...f, q: '' };
		const commands = folder.commands.filter((c) => (toolHit ? commandMatches(c, facetsOnly) : commandMatches(c, f, folder.name)));
		if (commands.length || toolHit) out.push({ ...folder, commands });
	}
	return out;
}

/** Split a command line into literal text and `{{ placeholder }}` parts, for highlighting. */
export function commandParts(command: string): { text: string; arg: boolean }[] {
	const out: { text: string; arg: boolean }[] = [];
	let last = 0;
	for (const m of command.matchAll(/\{\{\s*[^}]+?\s*\}\}/g)) {
		if (m.index > last) out.push({ text: command.slice(last, m.index), arg: false });
		out.push({ text: m[0], arg: true });
		last = m.index + m[0].length;
	}
	if (last < command.length) out.push({ text: command.slice(last), arg: false });
	return out;
}
