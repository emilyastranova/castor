/** Plugins' actions in projects (docs/PLUGINS.md): which actions each project's plugins offer,
 *  starting one (with a file when it takes one), and its run as it goes and when it ends. */
import { browser } from '$app/environment';
import type { PluginRun, ProjectPluginAction, UUID } from '$lib/api';
import { api, ApiError } from '$lib/api';
import { realtime } from '$lib/realtime.svelte';
import { snackbar } from '$lib/snackbar.svelte';

export type ActionPlace = 'project' | 'card' | 'asset';

/** The events a plugin can be told about (server plugin_events.EVENTS), in words. */
export const EVENT_WORDS: Record<string, string> = {
	'asset.created': 'a new asset is found',
	'asset.linked': 'a new relationship is found',
	'job.finished': 'a card’s job finishes',
	'run.finished': 'a playbook run finishes',
	'task.moved': 'a card moves',
	'comment.created': 'someone comments on a card'
};

/** Ask for a file the way an <input type=file> does, without one in the page. */
export function pickFile(accept: string[]): Promise<File | null> {
	return new Promise((resolve) => {
		const input = document.createElement('input');
		input.type = 'file';
		if (accept.length) input.accept = accept.join(',');
		input.addEventListener('change', () => resolve(input.files?.[0] ?? null), { once: true });
		input.addEventListener('cancel', () => resolve(null), { once: true });
		input.click();
	});
}

class PluginActions {
	byProject = $state<Record<UUID, ProjectPluginAction[]>>({});
	/** Runs seen in this tab: started here, or reported live. */
	runs = $state<Record<UUID, PluginRun>>({});
	private loading = new Map<UUID, Promise<void>>();
	/** Runs started in this tab: their result is told here. */
	private mine = new Set<UUID>();

	constructor() {
		if (!browser) return;
		realtime.subscribe((e) => {
			if (e.name === 'plugins.changed') {
				this.loading.clear();
				for (const id of Object.keys(this.byProject)) void this.load(id, true);
			}
			if (e.name === 'plugin_run.updated') this.update(e.data as PluginRun);
		});
	}

	load(projectId: UUID, force = false): Promise<void> {
		if (!force && this.loading.has(projectId)) return this.loading.get(projectId)!;
		const p = api()
			.plugins.actions(projectId)
			.then((a) => {
				this.byProject[projectId] = a;
			})
			.catch(() => {
				this.loading.delete(projectId);
			});
		this.loading.set(projectId, p);
		return p;
	}

	/** The actions offered in a place (and, for an asset, on its type). */
	for(projectId: UUID, place: ActionPlace, assetType?: string): ProjectPluginAction[] {
		return (this.byProject[projectId] ?? []).filter(
			(a) => a.on.includes(place) && (place !== 'asset' || !a.asset_types.length || (assetType && a.asset_types.includes(assetType)))
		);
	}

	running(projectId: UUID): PluginRun[] {
		return Object.values(this.runs).filter((r) => r.project_id === projectId && r.status === 'running');
	}

	private update(run: PluginRun) {
		const before = this.runs[run.id];
		this.runs[run.id] = { ...before, ...run, plugin_name: run.plugin_name ?? before?.plugin_name ?? null, label: run.label ?? before?.label ?? null };
		if (run.status !== 'running' && before?.status === 'running' && this.mine.has(run.id)) {
			this.mine.delete(run.id);
			const what = run.label ?? run.action;
			if (run.status === 'succeeded') snackbar.show(run.message || `${what}: done`, { timeout: 8000 });
			else snackbar.error(`${what} failed: ${run.message}`);
		}
	}

	/** Start an action (asking for its file first, if it takes one). */
	async start(action: ProjectPluginAction, ctx: { project_id: UUID; task_id?: UUID; asset_id?: UUID }): Promise<PluginRun | null> {
		let file: File | undefined;
		if (action.file) {
			const picked = await pickFile(action.file.accept);
			if (!picked) return null;
			if (picked.size > action.file.max_mb * 1024 * 1024) {
				snackbar.error(`${picked.name} is over ${action.file.max_mb} MB`);
				return null;
			}
			file = picked;
		}
		try {
			const run = await api().plugins.run(action.plugin.id, action.id, ctx, file);
			this.mine.add(run.id);
			this.update(run);
			snackbar.show(`${action.label}${file ? `: ${file.name}` : ''}, running…`);
			return run;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : `Could not start ${action.label}`);
			return null;
		}
	}
}

export const pluginActions = new PluginActions();
