<script lang="ts">
	import type { UserBrief } from '$lib/api';
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
			const [users, members] = await Promise.all([api().users.directory(), api().projects.members(projectId)]);
			everyone = users;
			chosen = new Set(members.map((u) => u.id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load members');
		} finally {
			loading = false;
		}
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
			Members see the project's board, cards, jobs and playbook runs. Viewers only look; operators work in it. Admins see every project.
		</p>
		{#if loading}
			<p class="py-6 text-center body-medium text-on-surface-variant">Loading…</p>
		{:else}
			<ul class="flex max-h-[24rem] flex-col overflow-auto rounded-md border border-outline-variant">
				{#each everyone as u (u.id)}
					{@const admin = u.role === 'admin'}
					{@const checked = admin || chosen.has(u.id)}
					{@const locked = admin || !editable || (u.id === self?.id && !self?.is_admin)}
					<li class="border-b border-outline-variant/50 last:border-0">
						<label class="flex items-center gap-3 px-3 py-2 {locked ? '' : 'cursor-pointer hover:bg-surface-container'}">
							<input type="checkbox" class="size-4 accent-[var(--md-sys-color-primary)]" {checked} disabled={locked} onchange={() => toggle(u.id)} />
							<Avatar name={u.display_name} src={u.avatar_url} size={28} />
							<span class="min-w-0 flex-1">
								<span class="block truncate body-medium text-on-surface">{u.display_name}{u.id === self?.id ? ' (you)' : ''}</span>
								<span class="block body-small text-on-surface-variant">@{u.username}</span>
							</span>
							<span class="flex items-center gap-1 label-medium text-on-surface-variant">
								<Icon name={ROLE_LABELS[u.role].icon} size={16} />{ROLE_LABELS[u.role].label}{admin ? ' · sees every project' : ''}
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
