<script lang="ts">
	import { page } from '$app/state';
	import type { Theme } from '$lib/api';
	import { api, ApiError, saveBlob } from '$lib/api';
	import { Button, Card, Icon, IconButton, Menu, MenuItem, TopAppBar } from '$lib/m3';
	import { applyTheme } from '$lib/m3/theme';
	import { themes } from '$lib/themes.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import ThemeEditor from '$lib/components/ThemeEditor.svelte';
	import ThemePreview from '$lib/components/ThemePreview.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let list = $state<Theme[]>(data.themes);
	// svelte-ignore state_referenced_locally
	let loaded = data.themes;
	$effect(() => {
		if (data.themes !== loaded) {
			loaded = data.themes;
			list = data.themes;
		}
	});

	let editorOpen = $state(false);
	let editing = $state<Theme | null>(null);
	let draft = $state('');
	let readonly = $state(false);

	const STARTER = `# A Castor theme. The format: the Themes page in the wiki (docs/THEME_SPEC.md).
id: my-theme
name: My theme
description: What it looks like, in a sentence.
modes: dark              # both, dark or light
colour: accent           # what someone's theme colour changes: palette, accent or fixed
default_colour: "#7c5cff"
shape:
  corners: { small: 4px, medium: 6px, large: 8px, extra-large: 12px }
elevation: hairline      # shadow, hairline or none
colours:
  dark:
    primary: "#7c5cff"
    on-primary: "#ffffff"
    primary-container: "#2b2160"
    on-primary-container: "#e4dcff"
    secondary-container: "#2a2933"
    on-secondary-container: "#e8e6f0"
    surface: "#101014"
    on-surface: "#e8e6f0"
    on-surface-variant: "#a6a2b5"
    surface-container-lowest: "#0b0b0e"
    surface-container-low: "#141419"
    surface-container: "#18181e"
    surface-container-high: "#1d1d24"
    surface-container-highest: "#24242c"
    outline: "#77738a"
    outline-variant: "#2e2d36"
css: |
  /* Anything else, kept to this theme. Hook classes: .m3-btn, .m3-btn-filled, .m3-tab, .m3-switch… */
`;
	const duplicate = (t: Theme) =>
		t.source_yaml.replace(/^id:\s*.+$/m, `id: ${t.id}-custom`).replace(/^name:\s*(.+)$/m, 'name: $1 (custom)');

	function open(t: Theme | null, opts: { draft?: string; readonly?: boolean } = {}) {
		editing = t;
		draft = opts.draft ?? '';
		readonly = opts.readonly ?? false;
		editorOpen = true;
	}
	// Trying a theme dresses the app in it; stopping puts back yours.
	function tryTheme(t: Theme | null) {
		if (t) applyTheme(t);
		else applyTheme(themes.byId(page.data.user?.theme) ?? themes.byId('')!);
	}
	function saved(t: Theme) {
		list = [...list.filter((x) => x.id !== t.id), t].sort((a, b) => a.name.localeCompare(b.name));
		void themes.load(true);
		snackbar.show(`Saved ${t.name}. People can pick it in their account.`);
	}
	async function remove(t: Theme) {
		const who = t.used_by ? ` ${t.used_by === 1 ? 'One person uses' : `${t.used_by} people use`} it; they go back to Castor's own look.` : '';
		if (!(await confirmAction({ title: `Delete ${t.name}?`, message: `The theme goes for everyone.${who}`, confirmLabel: 'Delete' }))) return;
		try {
			await api().themes.remove(t.id);
			list = list.filter((x) => x.id !== t.id);
			void themes.load(true);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete the theme');
		}
	}
	const people = (n: number) => (n ? `${n} ${n === 1 ? 'person' : 'people'}` : 'Nobody yet');
	const modes = { both: 'Light and dark', dark: 'Dark only', light: 'Light only' };
</script>

<svelte:head><title>Themes · Settings · Castor</title></svelte:head>

<TopAppBar title="Themes" subtitle="Looks people can pick in their account">
	{#snippet actions()}
		<Button icon="add" onclick={() => open(null, { draft: STARTER })}>New theme</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-4xl flex-col gap-3 pt-2">
		<p class="max-w-[70ch] body-small text-on-surface-variant">
			Everyone starts on Castor's own look and picks a theme under <b class="font-medium text-on-surface">Account → Theme</b>. A theme is YAML in the
			<a class="text-primary underline" href="https://github.com/emilyastranova/castor/wiki/Themes" target="_blank" rel="noopener">theme spec</a>; built-ins come from the server's
			<code class="font-mono">themes/</code> folder, and the ones you add here are stored with Castor.
		</p>
		<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
			{#each list as t (t.id)}
				<Card variant="outlined" class="flex flex-col">
					<ThemePreview theme={t} class="rounded-t-md" />
					<div class="flex items-start gap-2 px-3 pt-2.5 pb-2">
						<div class="min-w-0 flex-1">
							<p class="flex flex-wrap items-center gap-x-2 title-small text-on-surface">
								{t.name}
								<span class="rounded-xs border border-outline-variant px-1.5 label-small text-on-surface-variant">{t.origin === 'builtin' ? 'Built-in' : 'Custom'}</span>
							</p>
							<p class="mt-0.5 body-small text-on-surface-variant">{modes[t.modes]} · {people(t.used_by)} · <code class="font-mono">{t.id}</code></p>
							{#if t.description}<p class="mt-1 body-small text-on-surface-variant">{t.description}</p>{/if}
						</div>
						<Menu>
							{#snippet anchor()}<IconButton icon="more_vert" label="More for {t.name}" />{/snippet}
							<MenuItem icon="content_copy" onclick={() => open(null, { draft: duplicate(t) })}>Duplicate</MenuItem>
							<MenuItem icon="download" onclick={() => saveBlob(t.source_yaml, `${t.id}.yaml`)}>Download YAML</MenuItem>
							{#if t.origin === 'user'}<MenuItem icon="delete" danger onclick={() => remove(t)}>Delete</MenuItem>{/if}
						</Menu>
					</div>
					<div class="mt-auto flex justify-end gap-1 px-2 pb-2">
						{#if t.origin === 'user'}
							<Button variant="text" icon="edit" onclick={() => open(t)}>Edit</Button>
						{:else}
							<Button variant="text" icon="code" onclick={() => open(t, { readonly: true })}>View YAML</Button>
						{/if}
					</div>
				</Card>
			{:else}
				<p class="body-medium text-on-surface-variant">No themes yet besides Castor's own. Add one, or put YAML files in the server's <code class="font-mono">themes/</code> folder.</p>
			{/each}
		</div>
	</div>
</div>

<ThemeEditor bind:open={editorOpen} theme={editing} {draft} {readonly} ontry={tryTheme} onsaved={saved} />
