<script lang="ts">
	import PersonName from '$lib/components/PersonName.svelte';
	import type { Group, UserBrief } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Avatar, Button, Dialog, Icon } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';
	import { canEdit, me, ROLE_LABELS } from '$lib/session';

	/** Who can see a project. Admins see every project, so they're shown but can't be removed. */
	interface Props {
		open: boolean;
		projectId: string;
		projectName: string;
	}
	let { open = $bindable(false), projectId, projectName }: Props = $props();

	let everyone = $state<UserBrief[]>([]);
	let chosen = $state<Set<string>>(new Set());
	let groups = $state<Group[]>([]);
	let chosenGroups = $state<Set<string>>(new Set());
	/** People who can see it through a chosen group: their first such group's handle. */
	let viaGroup = $derived.by(() => {
		const via = new Map<string, string>();
		for (const g of groups) if (chosenGroups.has(g.id)) for (const id of g.member_ids) if (!via.has(id)) via.set(id, g.slug);
		return via;
	});
	let loading = $state(false);
	let saving = $state(false);
	let openedFor = false;

	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (open) void load();
	});

	async function load() {
		loading = true;
		try {
			const [users, members, all, shared] = await Promise.all([
				api().users.directory(), api().projects.members(projectId), api().groups.list(), api().projects.groups(projectId)
			]);
			everyone = users;
			chosen = new Set(members.map((u) => u.id));
			groups = all;
			chosenGroups = new Set(shared.map((g) => g.id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load members');
		} finally {
			loading = false;
		}
	}

	function toggleGroup(id: string) {
		const next = new Set(chosenGroups);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		chosenGroups = next;
	}

	function toggle(id: string) {
		const next = new Set(chosen);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		chosen = next;
	}

	async function save() {
		saving = true;
		try {
			await api().projects.setMembers(projectId, [...chosen]);
			await api().projects.setGroups(projectId, [...chosenGroups]);
			snackbar.show('Members saved');
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save members');
		} finally {
			saving = false;
		}
	}

	let editable = $derived(canEdit());
	let self = $derived(me());
</script>

<Dialog bind:open title="Who can see {projectName}" icon="group" size="md">
	<div class="flex flex-col gap-3 pt-2 pb-2">
		<p class="body-small text-on-surface-variant">
			Members see the project's board, cards, jobs and playbook runs, and so does everyone in a group it's shared with. Viewers only look; operators work in it. Admins see every project.
		</p>
		{#if loading}
			<p class="py-6 text-center body-medium text-on-surface-variant">Loading…</p>
		{:else}
			{#if groups.length}
				<h3 class="label-large text-on-surface">Groups</h3>
				<ul class="-mt-1 flex max-h-48 flex-col overflow-auto rounded-md border border-outline-variant">
					{#each groups as g (g.id)}
						<li class="border-b border-outline-variant/50 last:border-0">
							<label class="flex items-center gap-3 px-3 py-2 {editable ? 'cursor-pointer hover:bg-surface-container' : ''}">
								<input type="checkbox" class="size-4 accent-[var(--md-sys-color-primary)]" checked={chosenGroups.has(g.id)} disabled={!editable} onchange={() => toggleGroup(g.id)} />
								<span class="flex size-7 items-center justify-center rounded-full bg-tertiary-container text-on-tertiary-container"><Icon name="groups" size={16} /></span>
								<span class="min-w-0 flex-1">
									<span class="block truncate body-medium text-on-surface">{g.name}</span>
									<span class="block body-small text-on-surface-variant">@{g.slug}</span>
								</span>
								<span class="label-medium text-on-surface-variant">{g.member_count} {g.member_count === 1 ? 'person' : 'people'}</span>
							</label>
						</li>
					{/each}
				</ul>
				<h3 class="label-large text-on-surface">People</h3>
			{/if}
			<ul class="flex max-h-[24rem] flex-col overflow-auto rounded-md border border-outline-variant">
				{#each everyone as u (u.id)}
					{@const admin = u.role === 'admin'}
					{@const via = viaGroup.get(u.id)}
					{@const checked = admin || chosen.has(u.id)}
					{@const locked = admin || !editable || (u.id === self?.id && !self?.is_admin)}
					<li class="border-b border-outline-variant/50 last:border-0">
						<label class="flex items-center gap-3 px-3 py-2 {locked ? '' : 'cursor-pointer hover:bg-surface-container'}">
							<input type="checkbox" class="size-4 accent-[var(--md-sys-color-primary)]" {checked} disabled={locked} onchange={() => toggle(u.id)} />
							<Avatar name={u.display_name} src={u.avatar_url} size={28} />
							<span class="min-w-0 flex-1">
								<span class="block truncate body-medium text-on-surface"><PersonName name={u.display_name} />{u.id === self?.id ? ' (you)' : ''}</span>
								<span class="block body-small text-on-surface-variant">@{u.username}</span>
							</span>
							<span class="flex items-center gap-1 label-medium text-on-surface-variant">
								<Icon name={ROLE_LABELS[u.role].icon} size={16} />{ROLE_LABELS[u.role].label}{admin ? ' · sees every project' : !chosen.has(u.id) && via ? ` · through @${via}` : ''}
							</span>
						</label>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>{editable ? 'Cancel' : 'Close'}</Button>
		{#if editable}<Button icon="check" loading={saving} onclick={save}>Save</Button>{/if}
	{/snippet}
</Dialog>
