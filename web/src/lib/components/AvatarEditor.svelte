<script lang="ts">
	import type { AvatarKind, User } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Avatar, Button, Icon, TextField } from '$lib/m3';

	/** Choose a user's picture: initials, an uploaded file, an image URL or Gravatar.
	 *  Each choice is saved as soon as it's applied. */
	interface Props {
		user: User;
		onchange: (user: User) => void;
	}
	let { user, onchange }: Props = $props();

	const KINDS: { kind: AvatarKind; label: string; icon: string }[] = [
		{ kind: 'initials', label: 'Initials', icon: 'abc' },
		{ kind: 'upload', label: 'Upload', icon: 'upload' },
		{ kind: 'url', label: 'Image URL', icon: 'link' },
		{ kind: 'gravatar', label: 'Gravatar', icon: 'alternate_email' }
	];

	// svelte-ignore state_referenced_locally
	let tab = $state<AvatarKind>(user.avatar_kind);
	// svelte-ignore state_referenced_locally
	let url = $state(user.avatar_link);
	// svelte-ignore state_referenced_locally
	let email = $state(user.gravatar_email);
	let busy = $state(false);
	let error = $state<string | null>(null);
	let dragging = $state(false);

	async function apply(fn: () => Promise<User>) {
		busy = true;
		error = null;
		try {
			onchange(await fn());
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not change the picture';
		} finally {
			busy = false;
		}
	}

	const choose = (kind: AvatarKind, extra: { url?: string; gravatar_email?: string } = {}) =>
		apply(() => api().users.setAvatar(user.id, { kind, ...extra }));

	function upload(file: File | undefined) {
		if (!file) return;
		if (file.size > 2 * 1024 * 1024) {
			error = 'The picture is larger than 2 MB';
			return;
		}
		void apply(() => api().users.uploadAvatar(user.id, file));
	}

	function selectTab(kind: AvatarKind) {
		tab = kind;
		error = null;
		if (kind === 'initials' && user.avatar_kind !== 'initials') void choose('initials');
		if (kind === 'upload' && user.has_upload && user.avatar_kind !== 'upload') void choose('upload');
	}
</script>

<div class="flex flex-col gap-3">
	<div class="flex items-center gap-4">
		<Avatar name={user.display_name} src={user.avatar_url} size={64} />
		<div class="flex flex-wrap gap-1.5">
			{#each KINDS as k (k.kind)}
				<button
					type="button"
					class="flex items-center gap-1 rounded-full border px-3 py-1.5 label-large transition-colors {tab === k.kind
						? 'border-transparent bg-secondary-container text-on-secondary-container'
						: 'border-outline-variant text-on-surface-variant hover:bg-surface-container-high'}"
					aria-pressed={tab === k.kind}
					onclick={() => selectTab(k.kind)}
				>
					<Icon name={user.avatar_kind === k.kind ? 'check' : k.icon} size={16} />{k.label}
				</button>
			{/each}
		</div>
	</div>

	{#if tab === 'upload'}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<label
			class="flex cursor-pointer items-center gap-3 rounded-md border-2 border-dashed px-4 py-3 transition-colors {dragging ? 'border-primary bg-primary-container/30' : 'border-outline-variant hover:border-outline'}"
			ondragover={(e) => {
				e.preventDefault();
				dragging = true;
			}}
			ondragleave={() => (dragging = false)}
			ondrop={(e) => {
				e.preventDefault();
				dragging = false;
				upload(e.dataTransfer?.files?.[0]);
			}}
		>
			<Icon name="add_photo_alternate" class="text-primary" />
			<span class="flex-1">
				<span class="block body-medium text-on-surface">{user.has_upload ? 'Replace the picture' : 'Choose a picture'} or drop it here</span>
				<span class="block body-small text-on-surface-variant">PNG, JPEG, WebP or GIF, up to 2 MB</span>
			</span>
			<input
				type="file"
				class="hidden"
				accept="image/png,image/jpeg,image/webp,image/gif"
				onchange={(e) => {
					const input = e.currentTarget as HTMLInputElement;
					upload(input.files?.[0]);
					input.value = '';
				}}
			/>
		</label>
	{:else if tab === 'url'}
		<div class="flex items-start gap-2">
			<div class="min-w-0 flex-1"><TextField label="Image URL" bind:value={url} placeholder="https://…" supporting="An https:// link to an image" /></div>
			<Button variant="tonal" class="mt-2" loading={busy} disabled={!url.trim()} onclick={() => choose('url', { url })}>Use</Button>
		</div>
	{:else if tab === 'gravatar'}
		<div class="flex items-start gap-2">
			<div class="min-w-0 flex-1">
				<TextField label="Gravatar email" type="email" bind:value={email} placeholder="you@example.com" supporting="The picture set up for this email at gravatar.com" />
			</div>
			<Button variant="tonal" class="mt-2" loading={busy} disabled={!email.trim()} onclick={() => choose('gravatar', { gravatar_email: email })}>Use</Button>
		</div>
	{/if}
	{#if error}<p class="body-small text-error">{error}</p>{/if}
</div>
