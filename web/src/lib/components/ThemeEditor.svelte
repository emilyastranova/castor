<script lang="ts">
	import type { Theme, ThemeContrast } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Icon, TextField } from '$lib/m3';

	/** Write or change a custom theme (docs/THEME_SPEC.md) as YAML. "Try it" checks it and
	 *  dresses the whole app in it until you save, stop or close; built-ins open read-only. */
	interface Props {
		open: boolean;
		/** The theme to change (null: a new one from `draft`). */
		theme: Theme | null;
		draft?: string;
		readonly?: boolean;
		/** Dress the app in a compiled theme while trying it; null puts back the person's own. */
		ontry: (t: Theme | null) => void;
		onsaved: (t: Theme) => void;
	}
	let { open = $bindable(false), theme, draft = '', readonly = false, ontry, onsaved }: Props = $props();

	let yaml = $state('');
	let errors = $state<string[]>([]);
	/** Text colours below 4.5:1 on their surfaces: a warning, not a reason to refuse it. */
	let contrast = $state<ThemeContrast[]>([]);
	let showAll = $state(false);
	let trying = $state(false);
	let busy = $state(false);
	let openedFor = false;
	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (open) {
			yaml = theme?.source_yaml ?? draft;
			errors = [];
			contrast = theme?.contrast ?? [];
			showAll = false;
			trying = false;
		} else if (trying) {
			trying = false;
			ontry(null);
		}
	});

	async function check(): Promise<Theme | null> {
		const r = await api().themes.validate(yaml);
		errors = r.errors;
		contrast = r.theme?.contrast ?? [];
		return r.ok ? r.theme : null;
	}
	async function tryIt() {
		busy = true;
		try {
			const t = await check();
			if (t) {
				trying = true;
				ontry(t);
			}
		} finally {
			busy = false;
		}
	}
	function stop() {
		trying = false;
		ontry(null);
	}
	async function save() {
		busy = true;
		try {
			if (!(await check())) return;
			const saved = theme ? await api().themes.update(theme.id, yaml) : await api().themes.create(yaml);
			open = false;
			onsaved(saved);
		} catch (err) {
			errors = [err instanceof ApiError ? err.message : 'Could not save the theme'];
		} finally {
			busy = false;
		}
	}
</script>

<Dialog bind:open title={readonly ? (theme?.name ?? 'Theme') : theme ? `Edit ${theme.name}` : 'New theme'} icon="palette" size="lg">
	<div class="flex flex-col gap-3 pb-2">
		<p class="body-small text-on-surface-variant">
			{#if readonly}Built-in themes come from the server's <code class="font-mono">themes/</code> folder and can't be changed here; duplicate one to make your own.{:else}YAML in the
				<a class="text-primary underline" href="https://github.com/emilyastranova/castor/wiki/Themes" target="_blank" rel="noopener">theme spec</a>: colours for light and dark, fonts, corners, and any CSS you like, kept to this theme. <b class="font-medium text-on-surface">Try it</b> dresses the whole app in it before you save.{/if}
		</p>
		<TextField label="Theme (YAML)" bind:value={yaml} multiline rows={20} mono readonly={readonly} oninput={() => (errors = [])} />
		{#if !readonly && !contrast.length && !errors.length}<p class="-mt-1 body-small text-on-surface-variant">Try it or Save checks the text colours against their surfaces (4.5:1).</p>{/if}
		{#if contrast.length && !errors.length}
			<div class="flex flex-col gap-1 rounded-md border border-outline-variant bg-surface-container px-3 py-2 body-small text-on-surface" role="status">
				<p class="flex items-center gap-2 font-medium"><Icon name="contrast" size={16} class="text-error" />{contrast.length} text colour{contrast.length === 1 ? ' is' : 's are'} hard to read: below 4.5:1 on {contrast.length === 1 ? 'its surface' : 'their surfaces'}</p>
				<ul class="flex flex-col gap-0.5 pl-6 text-on-surface-variant">
					{#each showAll ? contrast : contrast.slice(0, 5) as c (c.message)}
						<li class="flex items-center gap-2">
							<span class="shrink-0 rounded-xs bg-error-container px-1 font-mono text-[11px] text-on-error-container">{c.ratio.toFixed(1)}:1</span>
							<span><b class="font-medium text-on-surface">{c.mode}</b>: <code class="font-mono">{c.text}</code> on <code class="font-mono">{c.surface}</code></span>
						</li>
					{/each}
				</ul>
				{#if contrast.length > 5}<button type="button" class="self-start pl-6 text-primary hover:underline" onclick={() => (showAll = !showAll)}>{showAll ? 'Show fewer' : `Show all ${contrast.length}`}</button>{/if}
				<p class="pl-6 text-on-surface-variant">You can still save it; roles you leave out are Castor's own.</p>
			</div>
		{/if}
		{#if errors.length}
			<div class="flex flex-col gap-1 rounded-md bg-error-container px-3 py-2 body-small text-on-error-container" role="alert">
				{#each errors as e (e)}<span class="flex gap-2"><Icon name="error" size={16} class="mt-px shrink-0" /><span class="break-words">{e}</span></span>{/each}
			</div>
		{/if}
	</div>
	{#snippet actions()}
		{#if trying}
			<span class="mr-auto flex items-center gap-1.5 body-small text-on-surface-variant"><Icon name="visibility" size={16} />Trying it on the whole app</span>
			<Button variant="text" onclick={stop}>Stop trying</Button>
		{/if}
		<Button variant="text" onclick={() => (open = false)}>{readonly ? 'Close' : 'Cancel'}</Button>
		<Button variant="tonal" icon="visibility" loading={busy && !trying} onclick={tryIt}>{trying ? 'Try again' : 'Try it'}</Button>
		{#if !readonly}<Button icon="check" loading={busy} onclick={save}>Save</Button>{/if}
	{/snippet}
</Dialog>
