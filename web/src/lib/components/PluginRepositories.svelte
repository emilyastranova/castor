<script lang="ts">
	import type { PluginRepository } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Card, Icon, IconButton, Menu, MenuItem } from '$lib/m3';
	import { confirmAction } from '$lib/confirm.svelte';
	import { relativeTime } from '$lib/format';
	import { snackbar } from '$lib/snackbar.svelte';
	import PluginRepositoryDialog from './PluginRepositoryDialog.svelte';

	/** Settings → Plugins → Repositories: the Git repositories plugins are installed from. */
	interface Props {
		/** Called when the list changes, so Browse shows what's in them now. */
		onchanged?: () => void;
	}
	let { onchanged }: Props = $props();

	let repos = $state<PluginRepository[] | null>(null);
	let error = $state<string | null>(null);
	let fetching = $state<Record<string, boolean>>({});
	let dialogOpen = $state(false);
	let editing = $state<PluginRepository | null>(null);

	async function load() {
		try {
			repos = await api().plugins.repositories();
			error = null;
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not load the repositories';
		}
	}
	$effect(() => void load());

	function put(r: PluginRepository) {
		const list = repos ?? [];
		const i = list.findIndex((x) => x.id === r.id);
		repos = i >= 0 ? list.map((x) => (x.id === r.id ? r : x)) : [...list, r].sort((a, b) => a.name.localeCompare(b.name));
		onchanged?.();
	}

	async function fetchNow(r: PluginRepository) {
		fetching = { ...fetching, [r.id]: true };
		try {
			const out = await api().plugins.fetchRepository(r.id);
			put(out);
			if (out.error) snackbar.error(`${out.name}: the fetch failed`);
			else snackbar.show(`${out.name}: ${out.plugins ?? 0} plugin${out.plugins === 1 ? '' : 's'}`);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not fetch it');
		} finally {
			fetching = { ...fetching, [r.id]: false };
		}
	}

	async function remove(r: PluginRepository) {
		const ok = await confirmAction({
			title: `Remove ${r.name}?`,
			message: 'Plugins installed from it stay installed, but Castor stops looking there for their updates.',
			confirmLabel: 'Remove',
			danger: true
		});
		if (!ok) return;
		try {
			await api().plugins.removeRepository(r.id);
			repos = (repos ?? []).filter((x) => x.id !== r.id);
			onchanged?.();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove it');
		}
	}

	const AUTH_WORDS: Record<PluginRepository['auth'], { icon: string; label: string }> = {
		none: { icon: 'public', label: 'Public' },
		https: { icon: 'password', label: 'Token' },
		ssh: { icon: 'key', label: 'SSH key' }
	};
	const edit = (r: PluginRepository | null) => {
		editing = r;
		dialogOpen = true;
	};
</script>

<div class="flex flex-col gap-3">
	<div class="flex flex-wrap items-start gap-3">
		<p class="max-w-[70ch] flex-1 body-small text-on-surface-variant">
			Your own sources of plugins: Git repositories, public or private, that Castor's server fetches with git (over HTTPS, SSH or anything else git speaks). Each folder in
			one with a <code class="font-mono">castor-plugin.yaml</code> shows under Browse, and the daily update check looks there for newer versions. Nothing in them is
			vetted: installing shows what a plugin asks for, as always.
		</p>
		<Button icon="add" onclick={() => edit(null)}>Add a repository</Button>
	</div>
	{#if error}
		<p class="flex items-start gap-2 rounded-md bg-error-container/60 px-3 py-2 body-small text-on-error-container" role="alert">
			<Icon name="error" size={16} class="mt-px shrink-0" />{error}
		</p>
	{/if}
	{#if repos === null && !error}
		<p class="flex items-center gap-2 py-6 body-medium text-on-surface-variant"><Icon name="progress_activity" class="animate-spin" />Loading…</p>
	{:else if repos}
		{#each repos as r (r.id)}
			<Card variant="outlined" class="flex items-start gap-3 px-4 py-3.5">
				<span class="grid size-10 shrink-0 place-items-center rounded-md bg-secondary-container text-on-secondary-container"><Icon name="source" /></span>
				<div class="min-w-0 flex-1">
					<p class="flex flex-wrap items-center gap-2">
						<button type="button" class="title-medium text-on-surface hover:underline" onclick={() => edit(r)}>{r.name}</button>
						<span class="flex items-center gap-1 rounded-full bg-surface-container-high px-2 py-0.5 label-small text-on-surface-variant">
							<Icon name={AUTH_WORDS[r.auth].icon} size={13} />{AUTH_WORDS[r.auth].label}
						</span>
						{#if !r.enabled}<span class="rounded-full bg-surface-container-high px-2 py-0.5 label-small text-on-surface-variant">Off</span>{/if}
					</p>
					<p class="truncate font-mono text-[12.5px] text-on-surface-variant" title={r.url}>{r.url}{r.ref ? ` @ ${r.ref}` : ''}</p>
					<p class="mt-0.5 body-small text-on-surface-variant">
						{#if r.commit}{r.plugins ?? 0} plugin{r.plugins === 1 ? '' : 's'} · {r.commit.slice(0, 10)} ·
						{/if}{r.fetched_at ? `fetched ${relativeTime(r.fetched_at)}` : 'not fetched yet'}
					</p>
					{#if r.error}
						<p class="mt-1.5 flex items-start gap-1.5 rounded-sm bg-error-container/60 px-2 py-1 body-small text-on-error-container">
							<Icon name="error" size={16} class="mt-px shrink-0" /><span class="min-w-0 font-mono text-[12px] break-words whitespace-pre-wrap">{r.error}</span>
						</p>
					{/if}
				</div>
				<Button variant="text" icon="sync" loading={fetching[r.id]} onclick={() => fetchNow(r)}>Fetch</Button>
				<Menu>
					{#snippet anchor()}<IconButton icon="more_vert" label="More for {r.name}" />{/snippet}
					<MenuItem icon="edit" onclick={() => edit(r)}>Edit</MenuItem>
					<MenuItem icon="delete" onclick={() => remove(r)}>Remove</MenuItem>
				</Menu>
			</Card>
		{:else}
			<div class="flex flex-col items-center gap-2 rounded-lg border border-dashed border-outline-variant px-6 py-10 text-center">
				<Icon name="source" size={32} class="text-on-surface-variant" />
				<p class="title-small text-on-surface">No repositories yet</p>
				<p class="max-w-md body-medium text-on-surface-variant">
					Add your team's plugin repository (on GitHub, GitLab, Gitea, Bitbucket or your own server) to install and update its plugins from here.
				</p>
				<Button icon="add" onclick={() => edit(null)}>Add a repository</Button>
			</div>
		{/each}
	{/if}
</div>

<PluginRepositoryDialog bind:open={dialogOpen} repo={editing} onsaved={put} />
