<script lang="ts">
	import type { Agent, Attachment, Column, Comment, Job, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { renderMarkdown } from '$lib/markdown';
	import { relativeTime, absoluteTime, bytes, duration } from '$lib/format';
	import { Button, Chip, Dialog, Icon, IconButton, Menu, MenuItem, Select, Switch, Tabs, TextField, Avatar } from '$lib/m3';
	import StatusChip from './StatusChip.svelte';
	import JobConsole from './JobConsole.svelte';
	import ColumnGlyph from './ColumnGlyph.svelte';
	import PriorityGlyph, { priorityOptions } from './PriorityGlyph.svelte';

	interface Props {
		task: Task;
		columns: Column[];
		agents: Agent[];
		onclose: () => void;
		onupdated: (task: Task) => void;
		ondeleted: (id: UUID) => void;
	}
	let { task, columns, agents, onclose, onupdated, ondeleted }: Props = $props();

	let open = $state(true);
	let tab = $state<'output' | 'comments' | 'attachments' | 'fields' | 'history'>('output');

	// ---- editable fields (debounced save) ----
	let title = $state('');
	let description = $state('');
	let command = $state('');
	let image = $state('');
	let agentId = $state('');
	let runOnMove = $state(true);
	let timeout = $state('');
	let priority = $state('0');
	let tagsText = $state('');
	let editingDescription = $state(false);
	let loadedFor = $state<UUID | null>(null);

	$effect(() => {
		if (loadedFor === task.id) return;
		loadedFor = task.id;
		title = task.title;
		description = task.description;
		command = task.command;
		image = task.image ?? '';
		agentId = task.agent_selector.agent_id ?? '';
		runOnMove = task.run_on_move;
		timeout = task.timeout_s ? String(task.timeout_s) : '';
		priority = String(task.priority);
		tagsText = task.tags.join(', ');
		editingDescription = false;
		tab = task.kind === 'command' ? 'output' : 'comments';
		selectedJobId = task.latest_job?.id ?? null;
		void loadSide();
	});

	async function save(patch: Partial<Task>) {
		try {
			onupdated(await api().tasks.update(task.id, patch));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Save failed');
		}
	}

	// ---- jobs & console ----
	let jobs = $state<Job[]>([]);
	let selectedJobId = $state<UUID | null>(null);
	let selectedJob = $derived(jobs.find((j) => j.id === selectedJobId) ?? (task.latest_job?.id === selectedJobId ? task.latest_job : null));
	let activeJob = $derived(task.latest_job && ['queued', 'dispatched', 'running'].includes(task.latest_job.status) ? task.latest_job : null);

	// ---- comments / attachments / history ----
	let comments = $state<Comment[]>([]);
	let attachments = $state<Attachment[]>([]);
	let history = $state<{ id: number; message: string; actor_name: string; created_at: string }[]>([]);
	let newComment = $state('');
	let posting = $state(false);
	let uploading = $state(false);
	let fileInput: HTMLInputElement | undefined = $state();

	async function loadSide() {
		const id = task.id;
		const client = api();
		const [j, c, a, h] = await Promise.all([
			client.tasks.jobs(id),
			client.tasks.comments(id),
			client.tasks.attachments(id),
			client.tasks.activity(id)
		]);
		if (id !== task.id) return;
		jobs = j;
		comments = c;
		attachments = a;
		history = h;
		if (!selectedJobId && j[0]) selectedJobId = j[0].id;
	}

	$effect(() =>
		realtime.subscribe((e) => {
			if (e.name === 'job.updated') {
				const job = e.data as Job;
				if (job.task_id !== task.id) return;
				const rest = jobs.filter((x) => x.id !== job.id);
				jobs = [job, ...rest].sort((a, b) => b.queued_at.localeCompare(a.queued_at));
				if (!selectedJobId || ['queued', 'dispatched'].includes(job.status)) selectedJobId = job.id;
			} else if (e.name === 'comment.created') {
				const c = e.data as Comment;
				if (c.task_id === task.id && !comments.some((x) => x.id === c.id)) comments = [...comments, c];
			} else if (e.name === 'comment.deleted') {
				const d = e.data as { id: UUID };
				comments = comments.filter((x) => x.id !== d.id);
			} else if (e.name === 'attachment.created') {
				const a = e.data as Attachment;
				if (a.task_id === task.id && !attachments.some((x) => x.id === a.id)) attachments = [...attachments, a];
			} else if (e.name === 'attachment.deleted') {
				const d = e.data as { id: UUID };
				attachments = attachments.filter((x) => x.id !== d.id);
			} else if (e.name === 'activity.created') {
				const a = e.data as { id: number; task_id: UUID | null; message: string; actor_name: string; created_at: string };
				if (a.task_id === task.id) history = [a, ...history];
			}
		})
	);

	async function run() {
		try {
			const job = await api().tasks.run(task.id);
			selectedJobId = job.id;
			tab = 'output';
			// Run also moves the card to In Progress; refresh it here in case the live
			// update is late or the realtime socket is reconnecting.
			onupdated(await api().tasks.get(task.id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not start job');
		}
	}
	async function cancel(job: Job) {
		try {
			await api().jobs.cancel(job.id);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not cancel');
		}
	}

	async function postComment() {
		if (!newComment.trim()) return;
		posting = true;
		try {
			const c = await api().tasks.addComment(task.id, newComment);
			if (!comments.some((x) => x.id === c.id)) comments = [...comments, c];
			newComment = '';
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not post comment');
		} finally {
			posting = false;
		}
	}

	async function upload(files: FileList | File[] | null) {
		if (!files || files.length === 0) return;
		uploading = true;
		try {
			for (const file of Array.from(files)) {
				const a = await api().tasks.upload(task.id, file);
				if (!attachments.some((x) => x.id === a.id)) attachments = [...attachments, a];
			}
			tab = 'attachments';
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Upload failed');
		} finally {
			uploading = false;
		}
	}

	function onPaste(e: ClipboardEvent) {
		const files = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith('image/'));
		if (files.length) {
			e.preventDefault();
			void upload(files.map((f, i) => new File([f], f.name === 'image.png' ? `screenshot-${Date.now()}-${i}.png` : f.name, { type: f.type })));
		}
	}

	async function removeAttachment(a: Attachment) {
		await api().attachments.remove(a.id);
		attachments = attachments.filter((x) => x.id !== a.id);
	}

	// ---- custom fields ----
	let newFieldKey = $state('');
	let newFieldValue = $state('');
	function setField(key: string, value: string) {
		void save({ custom_fields: { ...task.custom_fields, [key]: value } });
	}
	function removeField(key: string) {
		const next = { ...task.custom_fields };
		delete next[key];
		void save({ custom_fields: next });
	}

	async function moveTo(columnId: string) {
		const col = columns.find((c) => c.id === columnId);
		if (!col) return;
		try {
			onupdated(await api().tasks.move(task.id, columnId, Number.MAX_SAFE_INTEGER / 2));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Move failed');
		}
	}

	async function remove() {
		if (!confirm(`Delete “${task.title}”?`)) return;
		await api().tasks.remove(task.id);
		ondeleted(task.id);
	}

	let column = $derived(columns.find((c) => c.id === task.column_id));
	let agentOptions = $derived([{ value: '', label: 'Any available agent' }, ...agents.map((a) => ({ value: a.id, label: `${a.name}${a.connected ? '' : ' (offline)'}` }))]);
	let agentName = (id: UUID | null) => agents.find((a) => a.id === id)?.name ?? '—';
	let screenshots = $derived(attachments.filter((a) => a.content_type.startsWith('image/')));
	let files = $derived(attachments.filter((a) => !a.content_type.startsWith('image/')));
	let tabs = $derived([
		...(task.kind === 'command' ? [{ id: 'output', label: 'Output', icon: 'terminal', badge: jobs.length }] : []),
		{ id: 'comments', label: 'Comments', icon: 'chat_bubble', badge: comments.length },
		{ id: 'attachments', label: 'Attachments', icon: 'attach_file', badge: attachments.length },
		{ id: 'fields', label: 'Fields', icon: 'data_object', badge: Object.keys(task.custom_fields).length },
		{ id: 'history', label: 'History', icon: 'history' }
	]);
</script>

<Dialog bind:open size="lg" initialFocus="dialog" onclose={onclose}>
	{#snippet header()}
		<div class="flex items-start gap-3 px-6 pt-5 pb-3">
			<Icon name={task.kind === 'command' ? 'terminal' : 'person'} class="mt-2 {task.kind === 'command' ? 'text-primary' : 'text-on-surface-variant'}" />
			<div class="min-w-0 flex-1">
				<input
					class="w-full bg-transparent headline-small text-on-surface outline-none focus:underline decoration-primary/40 underline-offset-4"
					bind:value={title}
					onblur={() => title.trim() && title !== task.title && save({ title: title.trim() })}
					onkeydown={(e) => e.key === 'Enter' && (e.currentTarget as HTMLInputElement).blur()}
					aria-label="Title"
				/>
				<div class="mt-1 flex flex-wrap items-center gap-2 body-small text-on-surface-variant">
					<StatusChip job={task.latest_job} />
					<span>created {relativeTime(task.created_at)} · updated {relativeTime(task.updated_at)}</span>
				</div>
			</div>
			{#if task.kind === 'command'}
				{#if activeJob}
					<Button variant="tonal" icon="stop_circle" onclick={() => cancel(activeJob!)}>Cancel</Button>
				{:else}
					<Button icon="play_arrow" onclick={run} disabled={!command.trim()}>Run</Button>
				{/if}
			{/if}
			<Menu>
				{#snippet anchor()}<IconButton icon="more_vert" label="Card menu" />{/snippet}
				<MenuItem icon={task.kind === 'command' ? 'person' : 'terminal'} onclick={() => save({ kind: task.kind === 'command' ? 'manual' : 'command' })}>
					Convert to {task.kind === 'command' ? 'human task' : 'command'}
				</MenuItem>
				<MenuItem icon="link" onclick={() => { navigator.clipboard.writeText(location.href); snackbar.show('Link copied'); }}>Copy link</MenuItem>
				<MenuItem icon="delete" danger onclick={remove}>Delete card</MenuItem>
			</Menu>
			<IconButton icon="close" label="Close" onclick={() => (open = false)} />
		</div>
	{/snippet}

	<div class="grid min-h-full gap-6 pt-2 pb-6 lg:grid-cols-[1fr_300px]" onpaste={onPaste}>
		<!-- main column -->
		<div class="flex min-h-0 flex-col gap-4">
			{#if task.kind === 'command'}
				<div class="flex flex-col gap-3 rounded-md bg-surface-container p-4">
					<TextField label="Command" bind:value={command} multiline rows={2} mono onblur={() => command !== task.command && save({ command })} />
					<div class="grid gap-3 sm:grid-cols-2">
						<TextField label="Container image" bind:value={image} mono placeholder="run in agent shell" onblur={() => (image.trim() || null) !== task.image && save({ image: image.trim() || null })} />
						<Select label="Agent" bind:value={agentId} options={agentOptions} onchange={(v) => save({ agent_selector: v ? { agent_id: v } : {} })} />
					</div>
					<div class="flex flex-wrap items-center gap-4">
						<Switch bind:checked={runOnMove} label="Run when moved to In Progress" onchange={(v) => save({ run_on_move: v })} />
						<TextField label="Timeout (s)" bind:value={timeout} type="number" class="w-32 [&_div]:h-10" onblur={() => save({ timeout_s: timeout ? Number(timeout) : null })} />
					</div>
				</div>
			{/if}

			<div>
				<div class="mb-1 flex items-center justify-between">
					<h3 class="title-small text-on-surface">Description</h3>
					<Button variant="text" icon={editingDescription ? 'check' : 'edit'} onclick={() => { if (editingDescription && description !== task.description) void save({ description }); editingDescription = !editingDescription; }}>
						{editingDescription ? 'Done' : 'Edit'}
					</Button>
				</div>
				{#if editingDescription}
					<TextField label="Description (markdown)" bind:value={description} multiline rows={6} autofocus />
				{:else if task.description.trim()}
					<div class="prose-m3 body-medium text-on-surface">{@html renderMarkdown(task.description)}</div>
				{:else}
					<p class="body-medium text-on-surface-variant italic">No description.</p>
				{/if}
			</div>

			<Tabs {tabs} bind:value={tab} />

			{#if tab === 'output'}
				<div class="flex min-h-[320px] flex-1 flex-col gap-2">
					{#if jobs.length > 1}
						<div class="flex flex-wrap gap-1">
							{#each jobs as j (j.id)}
								<Chip variant="filter" selected={selectedJobId === j.id} onclick={() => (selectedJobId = j.id)}>
									{relativeTime(j.queued_at)} · {j.status}{j.started_at ? ` · ${duration(j.started_at, j.finished_at)}` : ''}
								</Chip>
							{/each}
						</div>
					{/if}
					{#if selectedJob}
						<p class="body-small text-on-surface-variant">
							Agent {agentName(selectedJob.agent_id)}{selectedJob.image ? ` · ${selectedJob.image}` : ' · host shell'} · queued {absoluteTime(selectedJob.queued_at)}
						</p>
						{#key selectedJob.id}
							<JobConsole job={selectedJob} oncancel={cancel} />
						{/key}
					{:else}
						<div class="flex flex-1 flex-col items-center justify-center gap-2 rounded-md bg-surface-container p-8 text-center">
							<Icon name="terminal" size={40} class="text-on-surface-variant" />
							<p class="body-medium text-on-surface-variant">No runs yet. Press Run or drag the card into In Progress.</p>
						</div>
					{/if}
				</div>
			{:else if tab === 'comments'}
				<div class="flex flex-col gap-4">
					{#each comments as c (c.id)}
						<div class="flex gap-3">
							<Avatar name={c.author_name} tone="secondary" />
							<div class="min-w-0 flex-1 rounded-md bg-surface-container px-4 py-3">
								<div class="flex items-center gap-2 body-small text-on-surface-variant">
									<span class="label-large text-on-surface">{c.author_name}</span>
									<span title={absoluteTime(c.created_at)}>{relativeTime(c.created_at)}</span>
									<span class="flex-1"></span>
									<IconButton icon="delete" label="Delete comment" size="sm" onclick={async () => { await api().tasks.removeComment(task.id, c.id); comments = comments.filter((x) => x.id !== c.id); }} />
								</div>
								<div class="prose-m3 mt-1 body-medium text-on-surface">{@html renderMarkdown(c.body)}</div>
							</div>
						</div>
					{:else}
						<p class="body-medium text-on-surface-variant">No comments yet.</p>
					{/each}
					<div class="flex flex-col gap-2">
						<TextField label="Add a comment" bind:value={newComment} multiline rows={3} supporting="Markdown supported · Ctrl+Enter to post · paste an image to attach a screenshot" onkeydown={(e) => (e.ctrlKey || e.metaKey) && e.key === 'Enter' && postComment()} />
						<div class="flex justify-end"><Button icon="send" loading={posting} onclick={postComment} disabled={!newComment.trim()}>Comment</Button></div>
					</div>
				</div>
			{:else if tab === 'attachments'}
				<div class="flex flex-col gap-4">
					<div class="flex items-center gap-2">
						<Button variant="tonal" icon="upload" loading={uploading} onclick={() => fileInput?.click()}>Upload</Button>
						<span class="body-small text-on-surface-variant">or paste a screenshot anywhere in this card</span>
						<input bind:this={fileInput} type="file" multiple class="hidden" onchange={(e) => upload((e.currentTarget as HTMLInputElement).files)} />
					</div>
					{#if screenshots.length}
						<div class="grid grid-cols-2 gap-3 md:grid-cols-3">
							{#each screenshots as a (a.id)}
								<figure class="group relative overflow-hidden rounded-md bg-surface-container">
									<a href={api().attachments.url(a.id, true)} target="_blank" rel="noopener">
										<img src={api().attachments.url(a.id, true)} alt={a.filename} class="aspect-video w-full object-cover" loading="lazy" />
									</a>
									<figcaption class="flex items-center gap-1 px-2 py-1 body-small text-on-surface-variant">
										<span class="flex-1 truncate" title={a.filename}>{a.filename}</span>
										<span>{bytes(a.size)}</span>
										<IconButton icon="delete" label="Delete" size="sm" onclick={() => removeAttachment(a)} />
									</figcaption>
								</figure>
							{/each}
						</div>
					{/if}
					{#if files.length}
						<ul class="divide-y divide-outline-variant rounded-md bg-surface-container">
							{#each files as a (a.id)}
								<li class="flex items-center gap-3 px-3 py-2">
									<Icon name="draft" class="text-on-surface-variant" />
									<a class="min-w-0 flex-1 truncate body-medium text-primary underline-offset-2 hover:underline" href={api().attachments.url(a.id)}>{a.filename}</a>
									<span class="body-small text-on-surface-variant">{bytes(a.size)} · {relativeTime(a.created_at)}</span>
									<IconButton icon="delete" label="Delete" size="sm" onclick={() => removeAttachment(a)} />
								</li>
							{/each}
						</ul>
					{/if}
					{#if attachments.length === 0}<p class="body-medium text-on-surface-variant">No attachments.</p>{/if}
				</div>
			{:else if tab === 'fields'}
				<div class="flex flex-col gap-3">
					{#each Object.entries(task.custom_fields) as [key, value] (key)}
						<div class="flex items-center gap-2">
							<span class="w-40 truncate label-large text-on-surface" title={key}>{key}</span>
							<TextField label="" value={String(value ?? '')} class="flex-1 [&_div]:h-10" onblur={(e) => setField(key, (e.currentTarget as HTMLInputElement).value)} />
							<IconButton icon="delete" label="Remove field" size="sm" onclick={() => removeField(key)} />
						</div>
					{:else}
						<p class="body-medium text-on-surface-variant">No custom fields. Add things like CVSS, asset owner, or ticket ID.</p>
					{/each}
					<form class="flex items-end gap-2" onsubmit={(e) => { e.preventDefault(); if (newFieldKey.trim()) { setField(newFieldKey.trim(), newFieldValue); newFieldKey = newFieldValue = ''; } }}>
						<TextField label="Field" bind:value={newFieldKey} class="w-40 [&_div]:h-10" />
						<TextField label="Value" bind:value={newFieldValue} class="flex-1 [&_div]:h-10" />
						<Button variant="tonal" type="submit" icon="add" disabled={!newFieldKey.trim()}>Add</Button>
					</form>
				</div>
			{:else if tab === 'history'}
				<ol class="flex flex-col gap-2">
					{#each history as h (h.id)}
						<li class="flex gap-3 body-medium">
							<span class="w-20 shrink-0 body-small text-on-surface-variant" title={absoluteTime(h.created_at)}>{relativeTime(h.created_at)}</span>
							<span class="text-on-surface">{h.message}</span>
							<span class="body-small text-on-surface-variant">· {h.actor_name}</span>
						</li>
					{:else}
						<li class="body-medium text-on-surface-variant">No history.</li>
					{/each}
				</ol>
			{/if}
		</div>

		<!-- side column -->
		<aside class="flex flex-col gap-4">
			<Select label="Column" value={task.column_id} options={columns.map((c) => ({ value: c.id, label: c.name }))} onchange={moveTo}>
				{#snippet leading(id)}<ColumnGlyph role={columns.find((c) => c.id === id)?.role} />{/snippet}
			</Select>
			<Select label="Priority" bind:value={priority} options={priorityOptions} onchange={(v) => save({ priority: Number(v) })}>
				{#snippet leading(level)}<PriorityGlyph {level} />{/snippet}
			</Select>
			<TextField label="Tags" bind:value={tagsText} supporting="Comma separated" onblur={() => { const tags = tagsText.split(',').map((t) => t.trim()).filter(Boolean); if (tags.join() !== task.tags.join()) void save({ tags }); }} />
			<div class="rounded-md bg-surface-container p-4 body-small text-on-surface-variant">
				<p class="mb-2 title-small text-on-surface">Details</p>
				<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
					<dt>Kind</dt><dd class="text-on-surface">{task.kind}</dd>
					{#if task.latest_job}
						<dt>Last run</dt><dd class="text-on-surface">{task.latest_job.status}{task.latest_job.started_at ? ` · ${duration(task.latest_job.started_at, task.latest_job.finished_at)}` : ''}</dd>
						<dt>Agent</dt><dd class="text-on-surface">{agentName(task.latest_job.agent_id)}</dd>
					{/if}
					<dt>ID</dt><dd class="truncate font-mono text-on-surface" title={task.id}>{task.id.slice(0, 8)}</dd>
				</dl>
			</div>
		</aside>
	</div>
</Dialog>
