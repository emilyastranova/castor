<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import type { Agent, Column, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { TopAppBar, IconButton, Button, Icon, Menu, MenuItem, Chip } from '$lib/m3';
	import { BoardStore } from '$lib/stores/board.svelte';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import BoardColumn from '$lib/components/BoardColumn.svelte';
	import NewTaskDialog from '$lib/components/NewTaskDialog.svelte';
	import TaskDialog from '$lib/components/TaskDialog.svelte';
	import ProjectDialog from '$lib/components/ProjectDialog.svelte';
	import MembersDialog from '$lib/components/MembersDialog.svelte';
	import ProjectViewSwitch from '$lib/components/ProjectViewSwitch.svelte';
	import { canEdit, isAdmin, me } from '$lib/session';
	import { people } from '$lib/people.svelte';
	import ActivityFeed from '$lib/components/ActivityFeed.svelte';

	let { data } = $props();

	// svelte-ignore state_referenced_locally
	const store = new BoardStore(data.board, data.activity);
	// `data` is re-merged on every navigation (including ?task= changes), but the board
	// object is only new when the page load actually re-ran. Only then do we reset the
	// store; otherwise realtime updates and optimistic moves would be thrown away.
	// svelte-ignore state_referenced_locally
	let loadedBoard = data.board;
	$effect(() => {
		if (data.board !== loadedBoard) {
			loadedBoard = data.board;
			store.load(data.board, data.activity);
		}
	});

	// svelte-ignore state_referenced_locally
	let agents = $state<Agent[]>(data.agents);
	// svelte-ignore state_referenced_locally
	let loadedAgents = data.agents;
	$effect(() => {
		if (data.agents !== loadedAgents) {
			loadedAgents = data.agents;
			agents = data.agents;
		}
	});

	$effect(() =>
		realtime.subscribe((e) => {
			store.apply(e);
			// The board ignores step cards; the one open on top still follows its changes.
			if ((e.name === 'task.updated' || e.name === 'task.moved') && stepTask && (e.data as Task).id === stepTask.id) stepTask = e.data as Task;
			// The open card was deleted (here or elsewhere): drop ?task= from the address. The
			// event can beat the delete request's own response, which unmounts the card first.
			if (e.name === 'task.deleted' && (e.data as { id: UUID }).id === selectedTaskId) closeTask();
			if (e.name === 'agent.updated') {
				const a = e.data as Agent;
				agents = [...agents.filter((x) => x.id !== a.id), a].sort((x, y) => x.name.localeCompare(y.name));
			}
		})
	);

	let newOpen = $state(false);
	let newColumn = $state<Column | null>(null);
	let editOpen = $state(false);
	let membersOpen = $state(false);
	let editable = $derived(canEdit());
	let activityOpen = $state(false);
	let filter = $state('');

	let selectedTaskId = $derived(page.url.searchParams.get('task'));
	let selectedTask = $derived(selectedTaskId ? (store.tasks.get(selectedTaskId) ?? null) : null);

	function openTask(task: Task) {
		const url = new URL(page.url);
		url.searchParams.set('task', task.id);
		for (const k of ['comment', 'tab', 'job', 'view', 'step']) url.searchParams.delete(k);
		void goto(url, { replaceState: false, noScroll: true, keepFocus: true });
	}
	function closeTask() {
		const url = new URL(page.url);
		url.searchParams.delete('task');
		for (const k of ['comment', 'tab', 'job', 'view', 'step']) url.searchParams.delete(k);
		void goto(url, { replaceState: true, noScroll: true });
	}

	// ---- a playbook run's steps (3h): hidden from the board, opened on top of the run card ----
	let stepTaskId = $derived(page.url.searchParams.get('step'));
	let stepTask = $state<Task | null>(null);
	$effect(() => {
		const id = stepTaskId;
		if (!id || !selectedTask) {
			stepTask = null;
			return;
		}
		if (stepTask?.id === id) return;
		api()
			.tasks.get(id)
			.then((t) => {
				if (page.url.searchParams.get('step') === id) stepTask = t;
			})
			.catch(() => closeStep());
	});
	function closeStep() {
		const url = new URL(page.url);
		for (const k of ['step', 'comment', 'tab', 'job', 'view']) url.searchParams.delete(k);
		void goto(url, { replaceState: true, noScroll: true, keepFocus: true });
	}
	// A link to a step's card (old links, search, notifications) opens its run card with the
	// step on top, keeping where it pointed (?comment=, ?tab=…).
	let redirecting: string | null = null;
	$effect(() => {
		const id = selectedTaskId;
		if (!id || store.tasks.has(id) || redirecting === id) return;
		redirecting = id;
		void (async () => {
			try {
				const t = await api().tasks.get(id);
				if (!t.playbook_step_id || !t.playbook_run_id) return;
				const run = await api().playbookRuns.get(t.playbook_run_id);
				if (!run.card_id) return;
				const url = new URL(page.url);
				url.searchParams.set('task', run.card_id);
				url.searchParams.set('step', t.id);
				void goto(url, { replaceState: true, noScroll: true, keepFocus: true });
			} catch {
				/* not a card we can show */
			}
		})();
	});

	async function move(taskId: UUID, columnId: UUID, position: number) {
		const before = store.tasks.get(taskId);
		store.moveLocal(taskId, columnId, position);
		try {
			const task = await api().tasks.move(taskId, columnId, position);
			store.upsertTask(task);
			const column = store.columns.find((c) => c.id === columnId);
			if (task.latest_job && column?.role === 'active' && task.kind === 'command' && before?.column_id !== columnId) {
				snackbar.show(`Queued “${task.title}”`, { action: { label: 'Open', run: () => openTask(task) } });
			}
		} catch (err) {
			if (before) store.upsertTask(before);
			snackbar.error(err instanceof ApiError ? err.message : 'Move failed');
		}
	}

	// Only the cards assigned to you; remembered per browser.
	let mineOnly = $state(false);
	$effect(() => {
		try {
			mineOnly = localStorage.getItem('castor.board.mine') === '1';
		} catch {
			/* storage unavailable */
		}
	});
	function toggleMine() {
		mineOnly = !mineOnly;
		try {
			localStorage.setItem('castor.board.mine', mineOnly ? '1' : '0');
		} catch {
			/* storage unavailable */
		}
	}
	// Load who's on the project (assignee avatars on the cards).
	$effect(() => void people.list(store.project.id));

	function filtered(column: Column): Task[] {
		let list = store.tasksIn(column.id);
		const myId = me()?.id;
		if (mineOnly && myId) list = list.filter((t) => t.assignees.includes(myId));
		if (!filter.trim()) return list;
		const q = filter.toLowerCase();
		return list.filter((t) => t.title.toLowerCase().includes(q) || t.command.toLowerCase().includes(q) || t.tags.some((x) => x.toLowerCase().includes(q)));
	}

	let online = $derived(agents.filter((a) => a.connected).length);
	let running = $derived(store.taskList.filter((t) => t.latest_job && ['queued', 'dispatched', 'running'].includes(t.latest_job.status)).length);

	async function deleteProject() {
		const ok = await confirmAction({
			title: `Delete project ${store.project.codename}?`,
			message: `“${store.project.name}” and all of its cards, comments, attachments and job history will be permanently deleted. This cannot be undone.`,
			confirmLabel: 'Delete project'
		});
		if (!ok) return;
		await api().projects.remove(store.project.id);
		await goto('/projects');
	}
</script>

<svelte:head><title>{store.project.codename} · Castor</title></svelte:head>

<TopAppBar>
	<div class="flex items-center gap-3">
		<IconButton icon="arrow_back" label="All projects" href="/projects" />
		<div class="min-w-0">
			<p class="label-medium text-primary">{store.project.codename}</p>
			<h1 class="truncate title-large text-on-surface">{store.project.name}</h1>
		</div>
	</div>
	{#snippet actions()}
		<ProjectViewSwitch projectId={store.project.id} current="board" />
		<div class="mr-2 hidden items-center gap-2 md:flex">
			{#if running > 0}
				<Chip variant="assist" icon="bolt">{running} running</Chip>
			{/if}
			<Chip variant="assist" icon={online ? 'dns' : 'cloud_off'} onclick={() => goto('/agents')}>
				{online} agent{online === 1 ? '' : 's'} online
			</Chip>
		</div>
		<Chip variant="filter" icon={mineOnly ? 'check' : 'person'} selected={mineOnly} class="mr-2 hidden md:inline-flex" onclick={toggleMine}>Assigned to me</Chip>
		<label class="hidden items-center gap-2 rounded-full bg-surface-container-high px-4 md:flex">
			<Icon name="search" size={20} class="text-on-surface-variant" />
			<input class="h-10 w-44 bg-transparent body-medium text-on-surface outline-none placeholder:text-on-surface-variant" placeholder="Filter cards" bind:value={filter} />
		</label>
		<IconButton icon="history" label="Activity" selected={activityOpen} onclick={() => (activityOpen = !activityOpen)} />
		<Menu>
			{#snippet anchor()}<IconButton icon="more_vert" label="Project menu" />{/snippet}
			<MenuItem icon="group" onclick={() => (membersOpen = true)}>Members</MenuItem>
			{#if editable}<MenuItem icon="edit" onclick={() => (editOpen = true)}>Edit project</MenuItem>{/if}
			<MenuItem icon="refresh" onclick={() => invalidateAll()}>Reload board</MenuItem>
			{#if isAdmin()}<MenuItem icon="delete" danger onclick={deleteProject}>Delete project</MenuItem>{/if}
		</Menu>
		{#if editable}
			<Button icon="add" class="ml-2" onclick={() => { newColumn = null; newOpen = true; }}>New card</Button>
		{:else}
			<Chip variant="assist" icon="visibility">View only</Chip>
		{/if}
	{/snippet}
</TopAppBar>

<div class="flex min-h-0 flex-1">
	<div class="flex min-h-0 flex-1 gap-3 overflow-x-auto px-4 pb-4">
		{#each store.columns as column (column.id)}
			<BoardColumn {column} tasks={filtered(column)} readonly={!editable} onopen={openTask} onadd={(c) => { newColumn = c; newOpen = true; }} onmove={move} />
		{/each}
	</div>
	{#if activityOpen}
		<aside class="hidden w-80 shrink-0 flex-col border-l border-outline-variant bg-surface lg:flex">
			<div class="flex h-12 items-center justify-between px-4">
				<h2 class="title-medium text-on-surface">Activity</h2>
				<IconButton icon="close" label="Close activity" size="sm" onclick={() => (activityOpen = false)} />
			</div>
			<ActivityFeed items={store.activity} onopentask={(id) => { const t = store.tasks.get(id); if (t) openTask(t); }} />
		</aside>
	{/if}
</div>

<NewTaskDialog bind:open={newOpen} projectId={store.project.id} columns={store.columns} column={newColumn} {agents} oncreated={(t) => { store.upsertTask(t); snackbar.show(`Created “${t.title}”`); }} />
<MembersDialog bind:open={membersOpen} projectId={store.project.id} projectName={store.project.name} />
<ProjectDialog bind:open={editOpen} clients={[]} project={store.project} onsaved={(p) => (store.project = p)} />

{#if selectedTask}
	<!-- Keyed so each card gets a fresh dialog: no field state can carry over between cards. -->
	{#key selectedTask.id}
		<TaskDialog task={selectedTask} columns={store.columns} {agents} onclose={closeTask} onupdated={(t) => store.upsertTask(t)} ondeleted={(id) => { store.tasks.delete(id); closeTask(); }} />
	{/key}
{/if}

{#if selectedTask && stepTask}
	<!-- The step's own card, on top of its run's card. -->
	{#key stepTask.id}
		<TaskDialog task={stepTask} columns={store.columns} {agents} onclose={closeStep} onupdated={(t) => (stepTask = t)} ondeleted={() => closeStep()} />
	{/key}
{/if}
