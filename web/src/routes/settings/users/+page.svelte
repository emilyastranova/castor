<script lang="ts">
	import type { User, UserRole } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Avatar, Button, Dialog, Icon, IconButton, Select, Switch, TextField, TopAppBar } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { absoluteTime, relativeTime } from '$lib/format';
	import { me, ROLE_LABELS } from '$lib/session';
	import { invalidateAll } from '$app/navigation';
	import AvatarEditor from '$lib/components/AvatarEditor.svelte';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let users = $state<User[]>(data.users);
	// svelte-ignore state_referenced_locally
	let loaded = data.users;
	$effect(() => {
		if (data.users !== loaded) {
			loaded = data.users;
			users = data.users;
		}
	});

	const roleOptions = (Object.keys(ROLE_LABELS) as UserRole[]).map((r) => ({ value: r, label: `${ROLE_LABELS[r].label}: ${ROLE_LABELS[r].detail}` }));
	const roleTone: Record<UserRole, string> = {
		admin: 'bg-primary-container text-on-primary-container',
		operator: 'bg-secondary-container text-on-secondary-container',
		viewer: 'bg-surface-container-highest text-on-surface-variant'
	};

	// ---- add ----
	let addOpen = $state(false);
	let form = $state({ username: '', display_name: '', role: 'operator' as UserRole, password: '', all_projects: false });
	let saving = $state(false);
	let error = $state<string | null>(null);
	function openAdd() {
		form = { username: '', display_name: '', role: 'operator', password: '', all_projects: false };
		error = null;
		addOpen = true;
	}
	async function add() {
		saving = true;
		error = null;
		try {
			const user = await api().users.create(form);
			users = [...users, user].sort((a, b) => a.username.localeCompare(b.username));
			addOpen = false;
			snackbar.show(`Added ${user.username}. Give them their password; they can change it under their account menu.`);
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not add the user';
		} finally {
			saving = false;
		}
	}

	// ---- edit ----
	let editing = $state<User | null>(null);
	let edit = $state({ display_name: '', role: 'operator' as UserRole, disabled: false, password: '' });
	function openEdit(u: User) {
		editing = u;
		edit = { display_name: u.display_name, role: u.role, disabled: !!u.disabled_at, password: '' };
		error = null;
	}
	async function saveEdit() {
		if (!editing) return;
		saving = true;
		error = null;
		try {
			const updated = await api().users.update(editing.id, {
				display_name: edit.display_name,
				role: edit.role,
				disabled: edit.disabled,
				...(edit.password ? { password: edit.password } : {})
			});
			users = users.map((u) => (u.id === updated.id ? updated : u));
			editing = null;
		} catch (err) {
			error = err instanceof ApiError ? err.message : 'Could not save';
		} finally {
			saving = false;
		}
	}
	let self = $derived(me());
	/** Editing the only active admin: role and disabling stay locked, so nobody's locked out. */
	let lastAdmin = $derived(
		!!editing && editing.role === 'admin' && !editing.disabled_at && users.filter((u) => u.role === 'admin' && !u.disabled_at).length === 1
	);
</script>

<svelte:head><title>Users · Castor</title></svelte:head>

<TopAppBar title="Users" subtitle="{users.filter((u) => !u.disabled_at).length} active">
	{#snippet actions()}
		<Button icon="person_add" onclick={openAdd}>Add user</Button>
	{/snippet}
</TopAppBar>

<div class="flex-1 overflow-auto px-6 pb-8">
	<div class="mb-4 grid gap-3 sm:grid-cols-3">
		{#each Object.entries(ROLE_LABELS) as [role, meta] (role)}
			<div class="flex items-start gap-3 rounded-md bg-surface-container-low p-3">
				<Icon name={meta.icon} class="text-on-surface-variant" />
				<div>
					<p class="title-small text-on-surface">{meta.label}</p>
					<p class="body-small text-on-surface-variant">{meta.detail}</p>
				</div>
			</div>
		{/each}
	</div>
	<p class="mb-4 body-small text-on-surface-variant">
		Admins see every project. Everyone else sees the projects they're a member of: set that under a project's <strong>Members</strong>.
	</p>
	<div class="overflow-hidden rounded-md bg-surface-container-low">
		<table class="w-full body-medium">
			<thead class="label-medium text-on-surface-variant">
				<tr class="border-b border-outline-variant text-left">
					<th class="px-4 py-2 font-medium">User</th><th class="px-4 py-2 font-medium">Role</th><th class="px-4 py-2 font-medium">Status</th><th class="px-4 py-2 font-medium">Added</th><th></th>
				</tr>
			</thead>
			<tbody>
				{#each users as u (u.id)}
					<tr class="border-b border-outline-variant/50 last:border-0 {u.disabled_at ? 'opacity-60' : ''}">
						<td class="px-4 py-2">
							<span class="flex items-center gap-3">
								<Avatar name={u.display_name} src={u.avatar_url} size={32} />
								<span>
									<a href="/people/{u.username}" class="block text-on-surface hover:underline">{u.display_name}{u.id === self?.id ? ' (you)' : ''}</a>
									<span class="block body-small text-on-surface-variant">@{u.username}</span>
								</span>
							</span>
						</td>
						<td class="px-4 py-2"><span class="rounded-full px-2.5 py-0.5 label-medium {roleTone[u.role]}">{ROLE_LABELS[u.role].label}</span></td>
						<td class="px-4 py-2 text-on-surface-variant">{u.disabled_at ? `Disabled ${relativeTime(u.disabled_at)}` : 'Active'}</td>
						<td class="px-4 py-2 text-on-surface-variant" title={absoluteTime(u.created_at)}>{relativeTime(u.created_at)}</td>
						<td class="px-4 py-2 text-right"><IconButton icon="edit" label="Edit {u.username}" size="sm" onclick={() => openEdit(u)} /></td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>

<Dialog bind:open={addOpen} title="Add a user" icon="person_add" size="md">
	<div class="flex flex-col gap-4 pt-2 pb-2">
		<div class="grid gap-3 sm:grid-cols-2">
			<TextField label="Username" bind:value={form.username} required autocomplete="off" supporting="letters, digits, . _ -" />
			<TextField label="Display name" bind:value={form.display_name} placeholder={form.username} />
		</div>
		<Select label="Role" bind:value={form.role} options={roleOptions} />
		<TextField label="Password" type="password" bind:value={form.password} required autocomplete="new-password" supporting="At least 8 characters. They can change it after signing in." />
		<Switch bind:checked={form.all_projects} label="Make them a member of every existing project" />
		{#if error}<p class="body-small text-error">{error}</p>{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (addOpen = false)}>Cancel</Button>
		<Button icon="check" loading={saving} disabled={!form.username || form.password.length < 8} onclick={add}>Add user</Button>
	{/snippet}
</Dialog>

<Dialog open={editing !== null} title="Edit {editing?.username ?? ''}" icon="manage_accounts" size="md" onclose={() => (editing = null)}>
	<div class="flex flex-col gap-4 pt-2 pb-2">
		{#if editing}
			<AvatarEditor
				user={editing}
				onchange={(u) => {
					users = users.map((x) => (x.id === u.id ? u : x));
					editing = u;
					if (u.id === self?.id) void invalidateAll();
				}}
			/>
		{/if}
		<TextField label="Display name" bind:value={edit.display_name} />
		<Select label="Role" bind:value={edit.role} options={roleOptions} disabled={lastAdmin} />
		<TextField label="New password (optional)" type="password" bind:value={edit.password} autocomplete="new-password" supporting="Leave empty to keep their password." />
		<Switch
			checked={!edit.disabled}
			disabled={lastAdmin}
			label={edit.disabled ? 'Disabled: can’t sign in, and signed-in sessions stop working' : 'Can sign in'}
			onchange={(on) => (edit.disabled = !on)}
		/>
		{#if lastAdmin}
			<p class="flex items-start gap-2 rounded-md bg-surface-container px-3 py-2 body-small text-on-surface-variant">
				<Icon name="lock" size={16} class="mt-px shrink-0" />
				<span>This is the only active admin, so it stays an active admin: make someone else an admin first. If a password is ever lost, run <code class="font-mono">castor-admin reset-password &lt;username&gt;</code> on the server.</span>
			</p>
		{/if}
		{#if error}<p class="body-small text-error">{error}</p>{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (editing = null)}>Cancel</Button>
		<Button icon="check" loading={saving} disabled={!!edit.password && edit.password.length < 8} onclick={saveEdit}>Save</Button>
	{/snippet}
</Dialog>
