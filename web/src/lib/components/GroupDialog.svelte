<script lang="ts">
	import PersonName from '$lib/components/PersonName.svelte';
	import type { Group, UserBrief } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Avatar, Button, Dialog, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';

	/** Add or edit a group: its @handle, name, the sign-on group it follows, and its people. */
	interface Props {
		open: boolean;
		group?: Group | null;
		onsaved?: (g: Group) => void;
		onremoved?: (id: string) => void;
	}
	let { open = $bindable(false), group = null, onsaved, onremoved }: Props = $props();

	let name = $state('');
	let slug = $state('');
	let description = $state('');
	let ssoGroup = $state('');
	let everyone = $state<UserBrief[]>([]);
	let chosen = $state<Set<string>>(new Set());
	let filter = $state('');
	let saving = $state(false);
	let slugTouched = false;
	let openedFor = false;

	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (!open) return;
		name = group?.name ?? '';
		slug = group?.slug ?? '';
		description = group?.description ?? '';
		ssoGroup = group?.sso_group ?? '';
		chosen = new Set(group?.member_ids ?? []);
		filter = '';
		slugTouched = !!group;
		void api().users.directory().then((u) => (everyone = u));
	});
	function nameChanged() {
		if (!slugTouched) slug = name.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-._]+|[-._]+$/g, '').slice(0, 64);
	}
	function toggle(id: string) {
		const next = new Set(chosen);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		chosen = next;
	}
	let shown = $derived.by(() => {
		const q = filter.trim().toLowerCase();
		const list = q ? everyone.filter((u) => u.display_name.toLowerCase().includes(q) || u.username.toLowerCase().includes(q)) : everyone;
		// The group's people first, then everyone else.
		return [...list].sort((a, b) => Number(chosen.has(b.id)) - Number(chosen.has(a.id)) || a.display_name.localeCompare(b.display_name));
	});

	async function save() {
		saving = true;
		try {
			const body = { slug: slug.trim(), name: name.trim(), description: description.trim(), sso_group: ssoGroup.trim() };
			const saved = group ? await api().groups.update(group.id, body) : await api().groups.create(body);
			const withPeople = await api().groups.setMembers(saved.id, [...chosen]);
			onsaved?.(withPeople);
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save the group');
		} finally {
			saving = false;
		}
	}
	async function remove() {
		if (!group) return;
		const ok = await confirmAction({
			title: `Remove @${group.slug}?`,
			message: `Its ${group.member_count} ${group.member_count === 1 ? 'person keeps their account' : 'people keep their accounts'}, but loses the projects ${group.member_count === 1 ? 'they could' : 'they could'} see only through it${group.project_count ? ` (${group.project_count} shared with it)` : ''}. Mentions of @${group.slug} stop notifying anyone.`,
			confirmLabel: 'Remove group'
		});
		if (!ok) return;
		try {
			await api().groups.remove(group.id);
			onremoved?.(group.id);
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove the group');
		}
	}
	let canSave = $derived(!!name.trim() && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(slug.trim()) && !saving);
</script>

<Dialog bind:open title={group ? `Edit @${group.slug}` : 'Add a group'} icon="groups" size="md">
	<div class="flex flex-col gap-4 pt-1 pb-2">
		<div class="grid gap-4 sm:grid-cols-2">
			<TextField label="Name" bind:value={name} required placeholder="Red team" oninput={nameChanged} />
			<TextField label="Handle" bind:value={slug} required mono supporting="@{slug || 'handle'} mentions everyone in it" oninput={() => (slugTouched = true)} />
		</div>
		<TextField label="Description (optional)" bind:value={description} />
		<TextField
			label="Sign-on group (optional)"
			bind:value={ssoGroup}
			mono
			placeholder="castor-red-team"
			supporting="A group at your sign-on provider: at each single sign-on, people in it join this group and people who left it leave."
		/>
		<div class="flex items-center gap-2">
			<span class="flex-1 label-large text-on-surface">People <span class="text-on-surface-variant">({chosen.size})</span></span>
		</div>
		{#if everyone.length > 8}
			<TextField label="Find someone" bind:value={filter} leadingIcon="search" class="-mt-2" />
		{/if}
		<ul class="flex max-h-72 flex-col overflow-auto rounded-md border border-outline-variant">
			{#each shown as u (u.id)}
				<li class="border-b border-outline-variant/50 last:border-0">
					<label class="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-surface-container">
						<input type="checkbox" class="size-4 accent-[var(--md-sys-color-primary)]" checked={chosen.has(u.id)} onchange={() => toggle(u.id)} />
						<Avatar name={u.display_name} src={u.avatar_url} size={28} />
						<span class="min-w-0 flex-1">
							<span class="block truncate body-medium text-on-surface"><PersonName name={u.display_name} /></span>
							<span class="block body-small text-on-surface-variant">@{u.username}</span>
						</span>
					</label>
				</li>
			{:else}
				<li class="px-4 py-6 text-center body-medium text-on-surface-variant">Nobody matches “{filter}”.</li>
			{/each}
		</ul>
	</div>
	{#snippet actions()}
		{#if group}<Button variant="text" class="mr-auto text-error" onclick={remove}>Remove</Button>{/if}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button icon={group ? 'save' : 'add'} loading={saving} disabled={!canSave} onclick={save}>{group ? 'Save' : 'Add group'}</Button>
	{/snippet}
</Dialog>
