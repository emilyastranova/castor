<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import type { User } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, Switch, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { ROLE_LABELS } from '$lib/session';
	import AvatarEditor from './AvatarEditor.svelte';
	import ThemeColorPicker from './ThemeColorPicker.svelte';
	import ThemePicker from './ThemePicker.svelte';
	import { themeById } from '$lib/m3/themes';
	import CompanionSetup from './CompanionSetup.svelte';

	/** The signed-in user's own account: display name and password. */
	interface Props {
		open: boolean;
		user: User;
	}
	let { open = $bindable(false), user }: Props = $props();

	// The Companion: saved as it's switched, like the theme colour.
	// svelte-ignore state_referenced_locally
	let companion = $state(!!user.companion);
	async function setCompanion(on: boolean) {
		companion = on;
		try {
			await api().auth.updateMe({ companion: on });
			await invalidateAll();
		} catch (err) {
			companion = !on;
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save that');
		}
	}

	let name = $state('');
	let current = $state('');
	let next = $state('');
	let repeat = $state('');
	let saving = $state(false);
	let error = $state<string | null>(null);
	let openedFor = false;
	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (open) {
			name = user.display_name;
			current = next = repeat = '';
			error = null;
		}
	});

	let mismatch = $derived(!!repeat && next !== repeat);

	async function save() {
		saving = true;
		error = null;
		try {
			if (name.trim() && name.trim() !== user.display_name) await api().auth.updateMe({ display_name: name.trim() });
			if (next) await api().auth.changePassword(current, next);
			snackbar.show(next ? 'Password changed' : 'Saved');
			open = false;
			await invalidateAll();
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}
</script>

<Dialog bind:open title="Your account" icon="account_circle" size="sm">
	<div class="flex flex-col gap-4 pt-2 pb-2">
		<p class="body-medium text-on-surface-variant">
			@{user.username} · {ROLE_LABELS[user.role].label}: {ROLE_LABELS[user.role].detail.toLowerCase()}
		</p>
		<p class="label-large text-on-surface">Picture</p>
		<AvatarEditor {user} onchange={() => invalidateAll()} />
		<TextField label="Display name" bind:value={name} />
		<div>
			<p class="mb-2 label-large text-on-surface">Theme</p>
			<ThemePicker {user} />
		</div>
		<div>
			<p class="mb-2 label-large text-on-surface">Theme colour</p>
			<ThemeColorPicker {user} defaultColour={themeById(user.theme).defaultColour} />
			<p class="mt-1.5 body-small text-on-surface-variant">
				{themeById(user.theme).seed === 'accent'
					? `It takes the place of ${themeById(user.theme).name}'s accent: primary actions, focus and selection. Saved as you pick; only you see it.`
					: 'The whole interface is coloured from it, in light and dark. Saved as you pick; only you see it.'}
			</p>
		</div>
		<div class="flex flex-col gap-3 rounded-md bg-surface-container px-3 py-2.5">
			<div class="flex items-start gap-3">
				<img src={user.companion_url ?? '/companion/sitting.webp'} alt="" aria-hidden="true" class="h-14 w-auto shrink-0" />
				<div class="min-w-0 flex-1">
					<Switch checked={companion} label="Companion" onchange={setCompanion} />
					<p class="mt-0.5 body-small text-on-surface-variant">Someone to keep you company: they sit on the newest comment in a project's timeline. Bring your own if you like. Only you see them.</p>
				</div>
			</div>
			{#if companion}<CompanionSetup {user} />{/if}
		</div>
		<p class="mt-2 label-large text-on-surface">Change password</p>
		<TextField label="Current password" type="password" bind:value={current} autocomplete="current-password" />
		<TextField label="New password" type="password" bind:value={next} autocomplete="new-password" supporting="At least 8 characters" />
		<TextField label="New password again" type="password" bind:value={repeat} autocomplete="new-password" error={mismatch ? 'doesn’t match' : null} />
		{#if error}<p class="body-small text-error">{error}</p>{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button
			icon="check"
			loading={saving}
			disabled={!name.trim() || (!!next && (next.length < 8 || next !== repeat || !current))}
			onclick={save}>Save</Button
		>
	{/snippet}
</Dialog>
