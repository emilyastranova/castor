<script lang="ts">
	import type { ExportRequest } from '$lib/api';
	import { ApiError, downloadExport } from '$lib/api';
	import { Button, Dialog, Switch, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { canEdit } from '$lib/session';

	/** Export what `request` names as a .castor.zip: optionally job history and activity (for
	 *  projects), and secrets encrypted under a passphrase (left out otherwise). */
	interface Props {
		open: boolean;
		title: string;
		request: ExportRequest;
		/** Offer job history and activity (projects and everything). */
		history?: boolean;
		/** Offer secrets (variable sets, credentials). */
		secrets?: boolean;
		summary?: string;
		/** After the file is saved. */
		ondone?: () => void;
	}
	let { open = $bindable(false), title, request, history = false, secrets = true, summary = '', ondone }: Props = $props();

	let jobs = $state(false);
	let activity = $state(true);
	let withSecrets = $state(false);
	let passphrase = $state('');
	let again = $state('');
	let busy = $state(false);
	$effect(() => {
		if (!open) return;
		withSecrets = false;
		passphrase = again = '';
	});

	let problem = $derived(
		!withSecrets ? '' : passphrase.length < 8 ? 'Use at least 8 characters' : passphrase !== again ? "The two don't match" : ''
	);

	async function run() {
		busy = true;
		try {
			const name = await downloadExport({ ...request, jobs: history && jobs, activity: history && activity, passphrase: withSecrets ? passphrase : undefined });
			snackbar.show(`Saved ${name}`);
			open = false;
			ondone?.();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not export');
		} finally {
			busy = false;
		}
	}
</script>

<Dialog bind:open {title} icon="download" size="sm">
	<div class="flex flex-col gap-4 pt-2 pb-2">
		{#if summary}<p class="body-medium text-on-surface-variant">{summary}</p>{/if}
		{#if history}
			<div class="flex flex-col gap-2">
				<Switch bind:checked={activity} label="Activity timeline" />
				<Switch bind:checked={jobs} label="Job history (runs, logs and parsed results; can be large)" />
				<p class="px-1 body-small text-on-surface-variant">Cards, comments, files, notes, playbook runs and assets are always included.</p>
			</div>
		{/if}
		{#if secrets && canEdit()}
			<div class="flex flex-col gap-2">
				<Switch bind:checked={withSecrets} label="Include secrets" />
				{#if withSecrets}
					<TextField label="Passphrase" type="password" bind:value={passphrase} autocomplete="new-password" supporting="Encrypts the secrets in the file. Whoever imports it needs this passphrase." />
					<TextField label="Passphrase again" type="password" bind:value={again} autocomplete="new-password" error={again ? problem || null : null} />
				{:else}
					<p class="px-1 body-small text-on-surface-variant">Passwords, hashes, keys and credential secrets are left out; they come in empty.</p>
				{/if}
			</div>
		{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon="download" loading={busy} disabled={!!problem} onclick={run}>Export</Button>
	{/snippet}
</Dialog>
