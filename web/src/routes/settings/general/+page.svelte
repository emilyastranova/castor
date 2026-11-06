<script lang="ts">
	import type { AppSettings } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Card, Select, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { appSettings } from '$lib/appSettings.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let settings = $state<AppSettings>(data.settings);

	async function change(patch: Partial<AppSettings>) {
		const before = settings;
		settings = { ...settings, ...patch };
		try {
			settings = await api().settings.update(patch);
			appSettings.value = settings;
			snackbar.show('Saved');
		} catch (err) {
			settings = before;
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save');
		}
	}

	const NOTE_LIMITS = [4, 6, 8, 12].map((n) => ({
		value: String(n),
		label: n === 4 ? '4 (one row)' : `${n}`,
		description: n === 4 ? 'Fits on one row of a project dashboard' : 'More than 4 wrap onto further rows'
	}));
</script>

<svelte:head><title>General · Settings · Castor</title></svelte:head>

<TopAppBar title="General" subtitle="Settings for everyone on this Castor" />

<div class="flex-1 overflow-auto px-6 pb-10">
	<div class="flex max-w-3xl flex-col gap-4 pt-2">
		<Card variant="outlined" class="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:gap-6">
			<div class="min-w-0 flex-1">
				<h2 class="title-medium text-on-surface">Pinned notes per project</h2>
				<p class="body-medium text-on-surface-variant">
					How many notes a project's dashboard can hold. Projects that already have more keep them; they just can't add another until they're under the limit.
				</p>
			</div>
			<Select
				class="w-48 shrink-0"
				label="Notes"
				value={String(settings.notes_per_project)}
				options={NOTE_LIMITS.some((o) => o.value === String(settings.notes_per_project)) ? NOTE_LIMITS : [...NOTE_LIMITS, { value: String(settings.notes_per_project), label: String(settings.notes_per_project), description: '' }]}
				onchange={(v) => change({ notes_per_project: Number(v) })}
			/>
		</Card>
	</div>
</div>
