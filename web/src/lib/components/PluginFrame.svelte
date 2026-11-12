<script lang="ts">
	import { goto } from '$app/navigation';
	import type { PluginPanel, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { isDark } from '$lib/m3/theme';
	import { canEdit, me } from '$lib/session';
	import { snackbar } from '$lib/snackbar.svelte';

	/** A plugin's page (castor-plugin.yaml `ui`), in a sandboxed frame: scripts only, an origin of
	 *  its own (no cookies, nothing of this page), no network (the server's CSP). It talks to us by
	 *  postMessage (plugin_sdk/ui.js); we pass its requests to the server, which acts as the plugin. */
	interface Props {
		panel: PluginPanel;
		projectId: UUID;
		task?: { id: UUID; title: string } | null;
		asset?: { id: UUID; type: string; label: string } | null;
		/** Fill the space it's given (a page) rather than follow the content's height. */
		fill?: boolean;
		/** Open an asset in place (an asset dialog's own way); otherwise the Assets page opens it. */
		onopenasset?: (id: UUID) => void;
	}
	let { panel, projectId, task = null, asset = null, fill = false, onopenasset }: Props = $props();

	let frame = $state<HTMLIFrameElement | null>(null);
	let height = $state(0);
	let loaded = $state(false);
	$effect(() => {
		void panel.url;
		height = panel.height;
	});

	// The theme's colour roles, type and corners, as plain CSS variables (--primary, --on-surface…).
	const ROLES = [
		'primary', 'on-primary', 'primary-container', 'on-primary-container', 'secondary', 'on-secondary', 'secondary-container',
		'on-secondary-container', 'tertiary', 'on-tertiary', 'tertiary-container', 'on-tertiary-container', 'error', 'on-error',
		'error-container', 'on-error-container', 'background', 'on-background', 'surface', 'on-surface', 'surface-variant',
		'on-surface-variant', 'surface-dim', 'surface-bright', 'surface-container-lowest', 'surface-container-low', 'surface-container',
		'surface-container-high', 'surface-container-highest', 'outline', 'outline-variant', 'inverse-surface', 'inverse-on-surface',
		'inverse-primary', 'scrim', 'shadow'
	];
	function theme() {
		const css = getComputedStyle(document.documentElement);
		const vars: Record<string, string> = {};
		for (const r of ROLES) vars[r] = css.getPropertyValue(`--md-sys-color-${r}`).trim();
		vars.font = css.getPropertyValue('--md-sys-typescale-font').trim() || getComputedStyle(document.body).fontFamily;
		vars.mono = css.getPropertyValue('--md-sys-typescale-mono').trim() || 'ui-monospace, monospace';
		for (const s of ['small', 'medium', 'large']) vars[`radius-${s}`] = css.getPropertyValue(`--md-sys-shape-corner-${s}`).trim();
		return { vars, dark: isDark() };
	}

	function context() {
		const user = me();
		return {
			plugin: { id: panel.plugin.id, name: panel.plugin.name },
			panel: { id: panel.id, slot: panel.slot, title: panel.title },
			project: { id: projectId },
			task,
			asset,
			user: user ? { id: user.id, username: user.username, display_name: user.display_name, can_write: canEdit() } : null
		};
	}

	const post = (message: Record<string, unknown>) => frame?.contentWindow?.postMessage({ castor: 1, ...message }, '*');

	async function answer(id: number, method: string, params: Record<string, unknown>) {
		try {
			const base = { project_id: projectId, task_id: task?.id ?? null, asset_id: asset?.id ?? null };
			const r =
				method === 'api'
					? await api().plugins.panelCall(panel.plugin.id, panel.id, {
							...base, kind: 'api', method: String(params.method ?? 'GET'), path: String(params.path ?? ''),
							query: (params.query as Record<string, unknown> | null) ?? null, body: params.body ?? null
						})
					: await api().plugins.panelCall(panel.plugin.id, panel.id, { ...base, kind: 'call', name: String(params.name ?? ''), args: params.args ?? null });
			if (r.status >= 400) {
				const detail = (r.result as { detail?: unknown } | null)?.detail;
				post({ type: 'reply', id, error: typeof detail === 'string' ? detail : `Castor answered ${r.status}`, status: r.status });
			} else {
				post({ type: 'reply', id, result: r.result });
			}
		} catch (err) {
			post({ type: 'reply', id, error: err instanceof ApiError ? err.message : 'The request failed', status: err instanceof ApiError ? err.status : 0 });
		}
	}

	function open(target: { asset?: string; card?: string }) {
		const id = (s: unknown) => (typeof s === 'string' && /^[0-9a-f-]{36}$/i.test(s) ? s : null);
		const card = id(target?.card);
		const other = id(target?.asset);
		if (card) void goto(`/projects/${projectId}?task=${card}`);
		else if (other && onopenasset) onopenasset(other);
		else if (other) void goto(`/projects/${projectId}/assets?asset=${other}`);
	}

	$effect(() => {
		const onMessage = (event: MessageEvent) => {
			// Only this frame (its origin is opaque, so we go by the window it came from).
			if (!frame || event.source !== frame.contentWindow) return;
			const m = event.data as { castor?: number; type?: string; [k: string]: unknown };
			if (!m || m.castor !== 1) return;
			if (m.type === 'hello') {
				loaded = true;
				post({ type: 'context', context: context(), theme: theme() });
			} else if (m.type === 'request' && typeof m.id === 'number' && (m.method === 'api' || m.method === 'call')) {
				void answer(m.id, m.method, (m.params as Record<string, unknown>) ?? {});
			} else if (m.type === 'resize' && typeof m.height === 'number') {
				height = Math.max(60, Math.min(4000, Math.ceil(m.height)));
			} else if (m.type === 'open') {
				open(m.target as { asset?: string; card?: string });
			} else if (m.type === 'notify' && typeof m.text === 'string' && m.text.trim()) {
				snackbar.show(`${panel.plugin.name}: ${m.text.slice(0, 300)}`);
			}
		};
		window.addEventListener('message', onMessage);
		// Theme changes (light/dark, a seed colour, another theme) reach the frame.
		const watch = new MutationObserver(() => post({ type: 'theme', theme: theme() }));
		watch.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-skin', 'style', 'class'] });
		return () => {
			window.removeEventListener('message', onMessage);
			watch.disconnect();
		};
	});

	// Another card or asset (the dialog moved on) is a fresh page: plugins read their context once.
	let place = $derived(`${panel.url}|${task?.id ?? ''}|${asset?.id ?? ''}`);
	$effect(() => {
		void place;
		loaded = false;
	});
</script>

<div class="plugin-frame relative {fill ? 'flex min-h-0 flex-1 flex-col' : ''}">
	{#key place}
	<iframe
		bind:this={frame}
		src={panel.url}
		title="{panel.title} ({panel.plugin.name})"
		sandbox="allow-scripts"
		referrerpolicy="no-referrer"
		loading="lazy"
		class="block w-full border-0 bg-transparent {fill ? 'min-h-0 flex-1' : ''}"
		style:height={fill ? undefined : `${height}px`}
	></iframe>
	{/key}
	{#if !loaded}
		<p class="pointer-events-none absolute inset-x-0 top-3 flex items-center justify-center gap-2 body-small text-on-surface-variant">
			<Icon name="extension" size={16} /> Loading {panel.plugin.name}…
		</p>
	{/if}
</div>
