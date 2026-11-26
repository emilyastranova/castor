<script lang="ts">
	import type { RepoPlugin } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon } from '$lib/m3';
	import PluginIcon from './PluginIcon.svelte';

	/** Settings → Plugins → Browse: the plugins in the plugin repositories, as last fetched. Shows
	 *  nothing at all while there are no repositories. */
	interface Props {
		oninstall: (p: RepoPlugin) => void;
		/** Bumped by the page after an install or a change of repositories. */
		version?: number;
		/** The Browse search, shared with the directory. */
		query?: string;
		onmanage?: () => void;
	}
	let { oninstall, version = 0, query = '', onmanage }: Props = $props();

	let plugins = $state<RepoPlugin[]>([]);
	let errors = $state<{ repository: string; name: string; error: string }[]>([]);
	let loaded = $state(false);
	let error = $state<string | null>(null);

	$effect(() => {
		void version;
		api()
			.plugins.repositoryPlugins()
			.then((r) => {
				plugins = r.plugins;
				errors = r.errors;
				error = null;
			})
			.catch((err) => (error = err instanceof ApiError ? err.message : 'Could not read the plugin repositories'))
			.finally(() => (loaded = true));
	});

	let good = $derived(plugins.filter((p) => !p.problem));
	let broken = $derived(plugins.filter((p) => p.problem));
	let shown = $derived.by(() => {
		const q = query.trim().toLowerCase();
		return good.filter((p) => !q || [p.name, p.id, p.description, p.publisher, p.repository.name, p.path].some((s) => s.toLowerCase().includes(q)));
	});
</script>

{#if loaded && (plugins.length || errors.length || error)}
	<section class="flex flex-col gap-3" aria-labelledby="repo-plugins">
		<div class="flex items-center gap-2">
			<h2 id="repo-plugins" class="flex-1 title-medium text-on-surface">From your repositories</h2>
			{#if onmanage}<Button variant="text" icon="source" onclick={onmanage}>Repositories</Button>{/if}
		</div>
		{#each errors as e (e.repository)}
			<p class="flex items-start gap-2 rounded-md bg-error-container/60 px-3 py-2 body-small text-on-error-container" role="alert">
				<Icon name="error" size={16} class="mt-px shrink-0" /><span class="min-w-0"><b class="font-medium">{e.name}:</b> {e.error.split('\n').at(-1)}</span>
			</p>
		{/each}
		{#if error}<p class="body-small text-error" role="alert">{error}</p>{/if}
		<div class="grid gap-3 sm:grid-cols-2">
			{#each shown as p (p.source)}
				<Card variant="outlined" class="flex flex-col gap-2 px-4 py-3.5">
					<div class="flex items-start gap-3">
						<PluginIcon src={p.icon_url} size={40} />
						<div class="min-w-0 flex-1">
							<p class="title-medium text-on-surface">{p.name}</p>
							<p class="body-small text-on-surface-variant">
								{p.publisher ? `by ${p.publisher} · ` : ''}{p.version}
							</p>
						</div>
						{#if p.update}
							<span class="flex items-center gap-1 rounded-full bg-tertiary-container px-2 py-0.5 label-medium text-on-tertiary-container"><Icon name="upgrade" size={14} />Update</span>
						{:else if p.installed}
							<span class="flex items-center gap-1 rounded-full bg-primary-container px-2 py-0.5 label-medium text-on-primary-container"><Icon name="check" size={14} />Installed</span>
						{/if}
					</div>
					{#if p.description}<p class="line-clamp-3 body-medium text-on-surface">{p.description}</p>{/if}
					<p class="flex min-w-0 items-center gap-1 body-small text-on-surface-variant">
						<Icon name="source" size={14} class="shrink-0" /><span class="truncate">{p.repository.name}{p.path ? ` · ${p.path}` : ''}</span>
					</p>
					{#if p.taken}
						<p class="flex items-center gap-1.5 body-small text-on-surface-variant"><Icon name="block" size={16} />Another plugin here has the id <code class="font-mono">{p.id}</code>.</p>
					{:else if p.installed && !p.installed_here && !p.update}
						<p class="flex items-center gap-1.5 body-small text-on-surface-variant"><Icon name="info" size={16} />Installed from somewhere else.</p>
					{/if}
					<div class="mt-auto -mb-1 flex justify-end gap-1">
						{#if p.homepage && /^https?:\/\//.test(p.homepage)}
							<a href={p.homepage} target="_blank" rel="noopener noreferrer" class="state-layer self-center rounded-full px-3 py-2 label-large text-primary">Homepage</a>
						{/if}
						{#if !p.taken && (!p.installed || p.update || !p.installed_here)}
							<Button variant="tonal" icon={p.update ? 'upgrade' : 'download'} onclick={() => oninstall(p)}>
								{p.update ? `Update to ${p.version}` : p.installed ? 'Install from here' : 'Install'}
							</Button>
						{/if}
					</div>
				</Card>
			{:else}
				<p class="py-4 body-medium text-on-surface-variant sm:col-span-2">{good.length ? 'Nothing matches.' : 'No plugins in them yet.'}</p>
			{/each}
		</div>
		{#if broken.length}
			<details class="body-small text-on-surface-variant">
				<summary class="cursor-pointer">{broken.length} folder{broken.length === 1 ? '' : 's'} with a castor-plugin.yaml Castor can't read</summary>
				<ul class="mt-1 flex flex-col gap-1 pl-4">
					{#each broken as b (b.source)}<li class="font-mono text-[12px] break-words">{b.repository.name}: {b.problem}</li>{/each}
				</ul>
			</details>
		{/if}
	</section>
{/if}
