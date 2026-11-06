<script lang="ts">
	import type { Agent, ArgErrors, Attachment, Column, Comment, CommandDefinition, Job, PlaybookRun, PlaybookSpec, Project, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { renderMarkdown } from '$lib/markdown';
	import { lightbox, openImagesIn } from '$lib/lightbox.svelte';
	import { PastedImages, type DraftTarget } from '$lib/pastedImages.svelte';
	import { relativeTime, absoluteTime, bytes, duration } from '$lib/format';
	import { Button, Chip, Dialog, Icon, IconButton, Menu, MenuItem, Select, Switch, Tabs, TextField, Avatar } from '$lib/m3';
	import StatusChip from './StatusChip.svelte';
	import JobConsole from './JobConsole.svelte';
	import JobResults from './JobResults.svelte';
	import ColumnGlyph from './ColumnGlyph.svelte';
	import PriorityGlyph, { priorityOptions } from './PriorityGlyph.svelte';
	import NameImageDialog from './NameImageDialog.svelte';
	import RunPanel from './RunPanel.svelte';
	import DuplicateDialog from './DuplicateDialog.svelte';
	import AlertRulesDialog from './AlertRulesDialog.svelte';
	import StepDecisions from './StepDecisions.svelte';
	import StepDecision from './StepDecision.svelte';
	import { aiStatus } from '$lib/ai.svelte';
	import RunPlaybookDialog from './RunPlaybookDialog.svelte';
	import AssigneePicker from './AssigneePicker.svelte';
	import MentionMenu from './MentionMenu.svelte';
	import RichText from './RichText.svelte';
	import { MentionSuggest } from '$lib/mentionSuggest.svelte';
	import { people } from '$lib/people.svelte';
	import { enter, leave, pop, glide } from '$lib/motion';
	import CommandArgsForm from './CommandArgsForm.svelte';
	import VarBindings from './VarBindings.svelte';
	import { tick } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { canEdit as roleCanEdit } from '$lib/session';
	import { isListRef, lists } from '$lib/lists.svelte';

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
	const argText = (v: unknown) =>
		isListRef(v)
			? `saved list: ${lists.find(v.list)?.set.name ?? '…'}`
			: v && typeof v === 'object' && 'assets' in v
				? `assets: ${(v as { assets: { type: string; where: string; field: string } }).assets.type}${(v as { assets: { where: string } }).assets.where ? ` where ${(v as { assets: { where: string } }).assets.where}` : ''} → ${(v as { assets: { field: string } }).assets.field}`
				: Array.isArray(v)
					? v.join(', ')
					: String(v ?? '');
	/** Viewers see the card read-only: no edit controls (the API refuses edits anyway). */
	const editable = $derived(roleCanEdit());
	$effect(() => {
		if (!editable && Object.values(task.command_args ?? {}).some(isListRef) && !lists.loaded) void lists.load();
	});
	let tab = $state<'run' | 'output' | 'comments' | 'attachments' | 'fields' | 'history'>('output');

	// ---- editable fields (debounced save) ----
	let title = $state('');
	let description = $state('');
	let command = $state('');
	let image = $state('');
	let containerFlagsText = $state('');
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
		dirty.clear();
		libArgs = { ...task.command_args };
		libErrors = {};
		libDirty = false;
		title = task.title;
		description = task.description;
		command = task.command;
		image = task.image ?? '';
		containerFlagsText = (task.container_flags ?? []).join(' ');
		agentId = task.agent_selector.agent_id ?? '';
		runOnMove = task.run_on_move;
		timeout = task.timeout_s ? String(task.timeout_s) : '';
		priority = String(task.priority);
		tagsText = task.tags.join(', ');
		editingDescription = false;
		// A notification about a comment (?comment=<id>) opens the card on it.
		// Search results link straight to a tab, a run and its results (?tab=&job=&view=). With
		// ?step= those are for the step's card on top of this run card, not for this one.
		const params = new URLSearchParams(page.url.searchParams);
		if (params.get('step') && task.run) for (const k of ['comment', 'tab', 'job', 'view']) params.delete(k);
		const linked = params.get('comment');
		const wantedTab = params.get('tab');
		tab = linked
			? 'comments'
			: wantedTab && ['run', 'output', 'comments', 'attachments', 'fields', 'history'].includes(wantedTab)
				? (wantedTab as typeof tab)
				: task.run
					? 'run'
					: task.kind === 'command'
					? 'output'
					: 'comments';
		selectedJobId = params.get('job') ?? task.latest_job?.id ?? null;
		wantedView = params.get('view') === 'results' ? 'results' : params.get('job') ? 'console' : null;
		shownLink = linked;
		void loadSide().then(() => {
			if (linked) void showComment(linked);
		});
	});

	// ---- library command ----
	let libraryDef = $state<CommandDefinition | null>(null);
	/** The card's library command always runs in a terminal. */
	let libTty = $derived(!!libraryDef?.spec.tty);
	let libArgs = $state<Record<string, unknown>>({});
	let libErrors = $state<Record<string, string>>({});
	let libDirty = $state(false);
	let savingArgs = $state(false);

	$effect(() => {
		const ref = task.command_ref;
		if (!ref) {
			libraryDef = null;
			return;
		}
		if (libraryDef?.id === ref) return;
		api()
			.commands.get(ref)
			.then((d) => (libraryDef = d))
			.catch(() => (libraryDef = null));
	});

	async function saveArgs() {
		savingArgs = true;
		try {
			const updated = await api().tasks.update(task.id, { command_args: libArgs });
			command = updated.command;
			onupdated(updated);
			libDirty = false;
			libErrors = {};
		} catch (err) {
			const detail = err instanceof ApiError ? (err.detail as ArgErrors | null) : null;
			if (detail?.errors) libErrors = detail.errors;
			else snackbar.error(err instanceof ApiError ? err.message : 'Could not save arguments');
		} finally {
			savingArgs = false;
		}
	}

	async function detachFromLibrary() {
		await save({ command_ref: null });
		command = task.command;
		snackbar.show('Detached from library');
	}

	// Fields save on blur only if the user actually typed in them for *this* card. Comparing the
	// field's text with the server's value is not enough: stale text (browser form restore after
	// a reload, leftover state) would then be written to whichever card is open.
	const dirty = new Set<string>();
	const edited = (field: string) => dirty.add(field);
	function commit(field: string, patch: Partial<Task>) {
		if (!dirty.has(field) || loadedFor !== task.id) return;
		dirty.delete(field);
		void save(patch);
	}

	async function save(patch: Partial<Task>) {
		try {
			const updated = await api().tasks.update(task.id, patch);
			if (!dirty.has('command')) command = updated.command;
			onupdated(updated);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Save failed');
		}
	}

	// ---- jobs & console ----
	let jobs = $state<Job[]>([]);
	let selectedJobId = $state<UUID | null>(null);
	let selectedJob = $derived(jobs.find((j) => j.id === selectedJobId) ?? (task.latest_job?.id === selectedJobId ? task.latest_job : null));

	// Console or parsed Results for the selected run. Jobs from before parsing existed have no
	// output_specs, but a library card can still parse them on demand.
	let outputView = $state<'console' | 'results'>('console');
	let resultCount = $state<number | null>(null);
	let hasResults = $derived(!!selectedJob && (!!selectedJob.output_specs?.length || !!task.command_ref));
	let viewFor: UUID | null = null;
	let wantedView: 'console' | 'results' | null = null;
	$effect(() => {
		const job = selectedJob;
		if (!job || job.id === viewFor) return;
		viewFor = job.id;
		resultCount = null;
		const done = ['succeeded', 'failed', 'lost'].includes(job.status);
		outputView = wantedView ?? (done && job.output_specs?.length ? 'results' : 'console');
		wantedView = null;
	});
	let activeJob = $derived(task.latest_job && ['queued', 'dispatched', 'running'].includes(task.latest_job.status) ? task.latest_job : null);

	// ---- comments / attachments / history ----
	let comments = $state<Comment[]>([]);
	/** Shown newest first, under the box, so a long thread doesn't need scrolling to reply. */
	let newestFirst = $derived([...comments].reverse());
	let attachments = $state<Attachment[]>([]);
	let history = $state<{ id: number; message: string; actor_name: string; created_at: string }[]>([]);
	let newComment = $state('');
	let posting = $state(false);
	let uploading = $state(false);
	let fileInput: HTMLInputElement | undefined = $state();

	/** Comments, files and runs animate only when they change live, not as the card loads. */
	let liveSide = $state(false);
	async function loadSide() {
		const id = task.id;
		liveSide = false;
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
		await tick();
		if (id === task.id) liveSide = true;
	}

	/** Scroll to a comment and flash it (opened from a notification). */
	let flashing = $state<string | null>(null);
	async function showComment(id: string) {
		if (!comments.some((c) => c.id === id)) return;
		flashing = id;
		await tick();
		document.getElementById(`comment-${id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
		setTimeout(() => flashing === id && (flashing = null), 3500);
	}
	// The card is already open when another notification links to one of its comments.
	let shownLink: string | null = null;
	$effect(() => {
		const linked = page.url.searchParams.get('comment');
		if (!linked || linked === shownLink || (page.url.searchParams.get('step') && task.run)) return;
		shownLink = linked;
		tab = 'comments';
		void showComment(linked);
	});
	let myId = $derived(page.data.user?.id as string | undefined);
	const mentionsMe = (c: Comment) => !!myId && (c.mentions ?? []).includes(myId) && c.author_id !== myId;

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
			} else if (e.name === 'comment.updated') {
				const c = e.data as Comment;
				if (c.task_id === task.id) comments = comments.map((x) => (x.id === c.id ? c : x));
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
			const patch: Partial<Task> = {};
			if (dirty.has('command') && command !== task.command) {
				dirty.delete('command');
				patch.command = command;
			}
			if (dirty.has('image')) {
				dirty.delete('image');
				patch.image = image.trim() || null;
			}
			if (dirty.has('timeout')) {
				dirty.delete('timeout');
				patch.timeout_s = timeout ? Number(timeout) : null;
			}
			if (Object.keys(patch).length > 0) {
				const updated = await api().tasks.update(task.id, patch);
				onupdated(updated);
			}
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
		if (!newComment.trim() || images.uploading > 0) return;
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

	// ---- editing comments ----
	let me = $derived(page.data.user);
	let editingId = $state<UUID | null>(null);
	let editDraft = $state('');
	let savingEdit = $state(false);
	const canEdit = (c: Comment) => !!me && (c.author_id === me.id || me.is_admin);

	async function startEdit(c: Comment) {
		editingId = c.id;
		editDraft = c.body;
		await tick();
		const box = document.getElementById(`comment-edit-${c.id}`) as HTMLTextAreaElement | null;
		box?.focus();
		box?.setSelectionRange(box.value.length, box.value.length);
	}

	async function removeComment(c: Comment) {
		const hasImages = /!\[[^\]]*\]\(\/api\/attachments\//.test(c.body);
		const ok = await confirmAction({
			title: 'Delete comment?',
			message:
				`${c.author_name}'s comment will be permanently deleted.` +
				(hasImages ? '\nImages in it stay in this card’s Attachments.' : ''),
			confirmLabel: 'Delete'
		});
		if (!ok) return;
		try {
			await api().tasks.removeComment(task.id, c.id);
			comments = comments.filter((x) => x.id !== c.id);
			if (editingId === c.id) cancelEdit();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not delete comment');
		}
	}

	function cancelEdit() {
		editingId = null;
		editDraft = '';
	}

	async function saveEdit(c: Comment) {
		if (!editDraft.trim() || images.uploading > 0 || savingEdit) return;
		if (editDraft === c.body) return cancelEdit();
		savingEdit = true;
		try {
			const updated = await api().tasks.updateComment(task.id, c.id, editDraft);
			comments = comments.map((x) => (x.id === updated.id ? updated : x));
			cancelEdit();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save comment');
		} finally {
			savingEdit = false;
		}
	}

	function onEditKey(c: Comment, e: KeyboardEvent) {
		if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
			e.preventDefault();
			void saveEdit(c);
		} else if (e.key === 'Escape') {
			// Cancel the edit, not the whole card.
			e.preventDefault();
			e.stopPropagation();
			cancelEdit();
		}
	}

	// ---- images pasted into the comment box: named, uploaded as card attachments, embedded ----
	const images = new PastedImages(async (file) => {
		const a = await api().tasks.upload(task.id, file, 'screenshot');
		if (!attachments.some((x) => x.id === a.id)) attachments = [...attachments, a];
		return api().attachments.url(a.id, true);
	});
	const newCommentTarget: DraftTarget = { get: () => newComment, set: (v) => (newComment = v) };
	const editTarget: DraftTarget = { get: () => editDraft, set: (v) => (editDraft = v) };
	// @mention suggestions: the people who can see this card's project.
	const newSuggest = new MentionSuggest(() => people.list(task.project_id), newCommentTarget);
	const editSuggest = new MentionSuggest(() => people.list(task.project_id), editTarget);

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
		if (!editable) return;
		const files = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith('image/'));
		if (files.length) {
			e.preventDefault();
			void upload(files.map((f, i) => new File([f], f.name === 'image.png' ? `screenshot-${Date.now()}-${i}.png` : f.name, { type: f.type })));
		}
	}

	async function removeAttachment(a: Attachment) {
		const embedded = comments.some((c) => c.body.includes(`/attachments/${a.id}/`));
		const ok = await confirmAction({
			title: 'Delete attachment?',
			message:
				`“${a.filename}” will be permanently deleted.` +
				(embedded ? '\nA comment on this card shows this image; it will show a broken image instead.' : ''),
			confirmLabel: 'Delete'
		});
		if (!ok) return;
		await api().attachments.remove(a.id);
		attachments = attachments.filter((x) => x.id !== a.id);
	}

	// ---- custom fields ----
	let newFieldKey = $state('');
	let newFieldValue = $state('');
	function setField(key: string, value: string) {
		void save({ custom_fields: { ...task.custom_fields, [key]: value } });
	}
	async function removeField(key: string) {
		const ok = await confirmAction({
			title: 'Remove field?',
			message: `“${key}” and its value will be removed from this card.`,
			confirmLabel: 'Remove'
		});
		if (!ok) return;
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

	/** A run card's step opens as its own card on top (?step=<its card>). */
	function openStep(taskId: UUID) {
		const url = new URL(page.url);
		url.searchParams.set('step', taskId);
		void goto(url, { noScroll: true, keepFocus: true });
	}

	// ---- duplicate / run again (3m) ----
	let duplicateOpen = $state(false);
	let alertsOpen = $state(false);
	// A step card: its run, for the decisions taken on the step (by people or the AI).
	let stepRun = $state<PlaybookRun | null>(null);
	$effect(() => {
		const runId = task.playbook_step_id ? task.playbook_run_id : null;
		if (!runId) return;
		void api().playbookRuns.get(runId).then((r) => (stepRun = r)).catch(() => {});
		return realtime.subscribe((e) => {
			if (e.name === 'playbook_run.updated' && (e.data as PlaybookRun).id === runId) stepRun = e.data as PlaybookRun;
		});
	});
	let stepSpec = $derived(stepRun?.spec_snapshot.steps.find((s) => s.id === task.playbook_step_id) ?? null);
	let stepResult = $derived(stepRun && task.playbook_step_id ? (stepRun.step_results[task.playbook_step_id] ?? null) : null);
	/** The decision that wrote the command as it is now, when that was the AI's change. */
	let aiEdit = $derived([...(stepResult?.history ?? [])].reverse().find((d) => d.command_after != null && d.command_after === task.command) ?? null);
	$effect(() => void aiStatus.load());
	let explaining = $state(false);
	let explanation = $state<{ jobId: UUID; text: string; model: string } | null>(null);
	async function explain(jobId: UUID) {
		explaining = true;
		try {
			const r = await api().ai.explain(jobId);
			explanation = { jobId, ...r };
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'The AI could not answer');
		} finally {
			explaining = false;
		}
	}
	/** "Notify me": an in-app (and desktop) notification when the card finishes: its run ends,
	 *  its playbook run ends, or (a manual card) it's moved to Done. */
	let notifyMe = $derived(!!myId && (task.alerts?.people ?? []).includes(myId));
	let othersNotified = $derived((task.alerts?.people ?? []).filter((p) => p !== myId).length);
	function toggleNotifyMe(on: boolean) {
		if (!myId) return;
		const current = task.alerts ?? { channels: [], on: 'failure' as const, long_minutes: null, decisions: true, people: [] };
		const people = on ? [...new Set([...(current.people ?? []), myId])] : (current.people ?? []).filter((p) => p !== myId);
		const next = { ...current, people };
		void save({ alerts: next.channels.length || people.length ? next : null });
	}
	const finished = ['succeeded', 'failed', 'cancelled', 'lost'];
	let duplicateCounts = $derived({
		comments: comments.length,
		files: attachments.filter((a) => !a.job_id).length,
		runs: jobs.filter((j) => finished.includes(j.status)).length
	});
	function openCard(id: UUID) {
		const url = new URL(page.url);
		for (const key of ['step', 'tab', 'comment']) url.searchParams.delete(key);
		url.searchParams.set('task', id);
		void goto(url, { noScroll: true, keepFocus: true });
	}
	/** A run card isn't copied: the playbook runs again with the same inputs, after a look. */
	let rerun = $state<{ spec: PlaybookSpec; project: Project; inputs: Record<string, unknown> } | null>(null);
	let rerunOpen = $state(false);
	async function runAgain() {
		if (!task.run) return;
		try {
			const [run, playbook, project] = await Promise.all([
				api().playbookRuns.get(task.run.run_id),
				api().playbooks.get(task.run.playbook_id),
				api().projects.get(task.project_id)
			]);
			rerun = { spec: playbook.spec, project, inputs: run.inputs };
			rerunOpen = true;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not load the playbook');
		}
	}

	async function remove() {
		const ok = await confirmAction({
			title: 'Delete card?',
			message: `“${task.title}” and its comments, attachments and job history will be permanently deleted.`,
			confirmLabel: 'Delete card'
		});
		if (!ok) return;
		await api().tasks.remove(task.id);
		ondeleted(task.id);
	}

	let column = $derived(columns.find((c) => c.id === task.column_id));
	let agentOptions = $derived([
		{ value: '', label: 'Any available agent' },
		...agents.map((a) => ({
			value: a.id,
			label: `${a.name}${a.connected ? '' : ' (offline)'}`,
			description: [a.hostname, a.os, a.tags.length ? a.tags.join(', ') : ''].filter(Boolean).join(' · '),
			keywords: a.tags.join(' ')
		}))
	]);
	let agentName = (id: UUID | null) => agents.find((a) => a.id === id)?.name ?? '—';
	let runFiles = $derived(attachments.filter((a) => a.job_id && a.job_id === selectedJob?.id));
	let screenshots = $derived(attachments.filter((a) => a.content_type.startsWith('image/')));
	let files = $derived(attachments.filter((a) => !a.content_type.startsWith('image/')));
	let tabs = $derived([
		...(task.run ? [{ id: 'run', label: 'Run', icon: 'account_tree', badge: undefined }] : []),
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
			<Icon name={task.run ? 'account_tree' : task.kind === 'command' ? 'terminal' : 'person'} class="mt-2 {task.run || task.kind === 'command' ? 'text-primary' : 'text-on-surface-variant'}" />
			<div class="min-w-0 flex-1">
				<input
					class="w-full bg-transparent headline-small text-on-surface outline-none focus:underline decoration-primary/40 underline-offset-4"
					bind:value={title}
					readonly={!editable}
					oninput={() => edited('title')}
					onblur={() => title.trim() && title !== task.title && commit('title', { title: title.trim() })}
					autocomplete="off"
					onkeydown={(e) => e.key === 'Enter' && (e.currentTarget as HTMLInputElement).blur()}
					aria-label="Title"
				/>
				<div class="mt-1 flex flex-wrap items-center gap-2 body-small text-on-surface-variant">
					<StatusChip job={task.latest_job} />
					<span>created {relativeTime(task.created_at)} · updated {relativeTime(task.updated_at)}</span>
				</div>
			</div>
			{#if task.kind === 'command' && editable}
				{#if activeJob}
					<Button variant="tonal" icon="stop_circle" onclick={() => cancel(activeJob!)}>Cancel</Button>
				{:else}
					<Button icon="play_arrow" onclick={run} disabled={!command.trim() || libDirty}>{task.latest_job?.status === 'failed' ? 'Re-run' : 'Run'}</Button>
				{/if}
			{/if}
			<Menu>
				{#snippet anchor()}<IconButton icon="more_vert" label="Card menu" />{/snippet}
				{#if editable && !task.run && !task.playbook_step_id}
					<MenuItem icon={task.kind === 'command' ? 'person' : 'terminal'} onclick={() => save({ kind: task.kind === 'command' ? 'manual' : 'command' })}>
						Convert to {task.kind === 'command' ? 'human task' : 'command'}
					</MenuItem>
					{#if task.command_ref}
						<MenuItem icon="link_off" onclick={detachFromLibrary}>Detach from library</MenuItem>
					{/if}
				{/if}
				{#if editable && !task.playbook_step_id}
					<MenuItem icon={task.alerts?.channels?.length ? 'notifications_active' : 'campaign'} onclick={() => (alertsOpen = true)}>{task.alerts?.channels?.length ? 'Chat alerts (on)…' : 'Chat alerts…'}</MenuItem>
					{#if task.run}
						<MenuItem icon="replay" onclick={runAgain}>Run again…</MenuItem>
					{:else}
						<MenuItem icon="content_copy" onclick={() => (duplicateOpen = true)}>Duplicate…</MenuItem>
					{/if}
				{/if}
				<MenuItem icon="link" onclick={() => { navigator.clipboard.writeText(location.href); snackbar.show('Link copied'); }}>Copy link</MenuItem>
				{#if editable}<MenuItem icon="delete" danger onclick={remove}>Delete card</MenuItem>{/if}
			</Menu>
			<IconButton icon="close" label="Close" onclick={() => (open = false)} />
		</div>
	{/snippet}

	<!-- minmax(0, …) and min-w-0 let the main column shrink: wide output scrolls inside the
	     console instead of pushing the side column out of the dialog. -->
	<div class="grid min-h-full grid-cols-[minmax(0,1fr)] gap-6 pt-2 pb-6 lg:grid-cols-[minmax(0,1fr)_300px]" onpaste={onPaste}>
		<!-- main column -->
		<div class="flex min-h-0 min-w-0 flex-col gap-4">
			{#if stepRun && stepSpec && stepRun.step_status[stepSpec.id] === 'attention'}
				<StepDecision run={stepRun} step={stepSpec} result={stepResult} onresolved={(r) => (stepRun = r)} />
			{/if}
			<StepDecisions result={stepResult} />
			{#if task.kind === 'command' && !editable}
				<div class="flex flex-col gap-3 rounded-md bg-surface-container p-4">
					{#if task.command_ref}
						<div class="flex items-center gap-2">
							<Icon name="menu_book" size={20} class="shrink-0 text-primary" />
							<span class="min-w-0 truncate title-small text-on-surface">{libraryDef?.name ?? task.command_ref}</span>
						</div>
						{#if Object.keys(task.command_args ?? {}).length}
							<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 body-small">
								{#each Object.entries(task.command_args) as [name, value] (name)}
									<dt class="font-mono text-on-surface-variant">{name}</dt>
									<dd class="truncate font-mono text-on-surface" title={argText(value)}>{argText(value)}</dd>
								{/each}
							</dl>
						{/if}
					{/if}
					<div>
						<p class="mb-1 label-medium text-on-surface-variant">Command</p>
						<pre class="overflow-auto rounded-sm bg-surface-container-highest p-3 font-mono text-[12.5px] whitespace-pre-wrap break-words text-on-surface">{task.command || '—'}</pre>
					</div>
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 body-small text-on-surface-variant">
						<dt>Runs in</dt><dd class="truncate font-mono text-on-surface">{task.tool ? `the ${task.tool} tool` : task.image || 'the agent’s shell'}{task.container_flags?.length ? ` ${task.container_flags.join(' ')}` : ''}</dd>
						<dt>Agent</dt><dd class="text-on-surface">{agentId ? agentName(agentId) : 'any available agent'}</dd>
						<dt>Timeout</dt><dd class="text-on-surface">{task.timeout_s ? `${task.timeout_s}s` : 'none'}</dd>
						<dt>On move</dt><dd class="text-on-surface">{task.run_on_move ? 'runs when moved to In Progress' : 'doesn’t run on move'}</dd>
					</dl>
				</div>
			{:else if task.kind === 'command'}
				<div class="flex flex-col gap-3 rounded-md bg-surface-container p-4">
					{#if task.command_ref}
						<div class="flex items-center gap-2">
							<Icon name="menu_book" size={20} class="shrink-0 text-primary" />
							<span class="min-w-0 truncate title-small text-on-surface" title={libraryDef?.name ?? task.command_ref}>{libraryDef?.name ?? task.command_ref}</span>
							{#if libraryDef}<span class="shrink-0 rounded-full bg-surface-container-highest px-2 py-0.5 whitespace-nowrap label-small text-on-surface-variant">{libraryDef.origin === 'builtin' ? 'built-in' : 'yours'}</span>{/if}
							<span class="flex-1"></span>
							{#if libDirty}
								<Button variant="text" onclick={() => { libArgs = { ...task.command_args }; libErrors = {}; libDirty = false; }}>Discard</Button>
								<Button icon="check" loading={savingArgs} onclick={saveArgs}>Save arguments</Button>
							{/if}
						</div>
						{#if libraryDef}
							{#key libraryDef.id}
								<CommandArgsForm spec={libraryDef.spec} bind:values={libArgs} bind:errors={libErrors} onedit={() => (libDirty = true)} projectId={task.project_id} />
							{/key}
							{#if libraryDef.spec.vars?.length}
								<VarBindings
									slots={libraryDef.spec.vars}
									bindings={task.var_bindings ?? {}}
									projectId={task.project_id}
									onchange={(var_bindings) => save({ var_bindings })}
								/>
							{/if}
						{/if}
					{/if}

					<TextField label="Command" bind:value={command} multiline rows={2} mono oninput={() => edited('command')} onblur={() => command !== task.command && commit('command', { command })} supporting={aiEdit ? `Changed by the AI: ${aiEdit.change ?? 'a corrected command'} (details at the top)` : ''} />
					<div class="grid gap-3 sm:grid-cols-2">
						{#if task.tool}
							<a href="/tools#{task.tool}" class="flex h-14 items-center gap-2 rounded-xs border border-outline-variant px-4 hover:border-outline" title="The command's tool decides where it runs; see Tools">
								<Icon name="handyman" size={20} class="text-primary" />
								<span class="min-w-0">
									<span class="block label-small text-on-surface-variant">Tool</span>
									<span class="block truncate font-mono body-medium text-on-surface">{task.tool}</span>
								</span>
							</a>
						{:else}
							<TextField label="Container image" bind:value={image} mono placeholder="run in agent shell" oninput={() => edited('image')} onblur={() => (image.trim() || null) !== task.image && commit('image', { image: image.trim() || null })} />
						{/if}
						<Select label="Agent" bind:value={agentId} options={agentOptions} onchange={(v) => save({ agent_selector: v ? { agent_id: v } : {} })} />
					</div>
					{#if image.trim()}
						<TextField
							label="Container flags (optional)"
							bind:value={containerFlagsText}
							mono
							placeholder="--privileged --network=bridge"
							supporting="Flags passed to docker run / podman run"
							oninput={() => edited('container_flags')}
							onblur={() => {
								const parsed = containerFlagsText.trim() ? containerFlagsText.split(/\s+/).filter(Boolean) : [];
								const current = task.container_flags ?? [];
								const changed = parsed.length !== current.length || parsed.some((v, i) => v !== current[i]);
								if (changed) commit('container_flags', { container_flags: parsed });
							}}
						/>
					{/if}
					<div class="flex flex-wrap items-center gap-4">
						<Switch bind:checked={runOnMove} label="Run when moved to In Progress" onchange={(v) => save({ run_on_move: v })} />
						<span title="A real terminal: you can type into it while it runs, full-screen programs (tmux, vim, htop) work, and a run that stops at a prompt tells people.">
							<Switch checked={!!task.tty || libTty} disabled={libTty} label={libTty ? 'Interactive terminal (its command asks for one)' : 'Interactive terminal'} onchange={(v) => save({ tty: v })} />
						</span>
						<TextField label="Timeout (s)" bind:value={timeout} type="number" class="w-40 [&_div]:h-10" oninput={() => edited('timeout')} onblur={() => (timeout ? Number(timeout) : null) !== task.timeout_s && commit('timeout', { timeout_s: timeout ? Number(timeout) : null })} />
					</div>
				</div>
			{/if}

			<div>
				<div class="mb-1 flex items-center justify-between">
					<h3 class="title-small text-on-surface">Description</h3>
					{#if editable}<Button variant="text" icon={editingDescription ? 'check' : 'edit'} onclick={() => { if (editingDescription && description !== task.description) commit('description', { description }); editingDescription = !editingDescription; }}>
						{editingDescription ? 'Done' : 'Edit'}
					</Button>{/if}
				</div>
				{#if editingDescription}
					<TextField label="Description (markdown)" bind:value={description} multiline rows={6} autofocus oninput={() => edited('description')} />
				{:else if task.description.trim()}
					<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
					<div class="prose-m3 body-medium text-on-surface" onclick={openImagesIn}>{@html renderMarkdown(task.description)}</div>
				{:else}
					<p class="body-medium text-on-surface-variant italic">No description.</p>
				{/if}
			</div>

			<Tabs {tabs} bind:value={tab} />

			{#if tab === 'run' && task.run}
				<RunPanel runId={task.run.run_id} {editable} onopenstep={openStep} />
			{:else if tab === 'output'}
				<div class="flex min-h-[320px] flex-1 flex-col gap-2">
					{#if jobs.length > 1}
						<div class="flex flex-wrap gap-1">
							{#each jobs as j (j.id)}
								<span class="inline-flex" in:pop={{ enabled: liveSide }} animate:glide={{ enabled: liveSide }}><Chip variant="filter" selected={selectedJobId === j.id} onclick={() => (selectedJobId = j.id)}>
									{relativeTime(j.queued_at)} · {j.status}{j.started_at ? ` · ${duration(j.started_at, j.finished_at)}` : ''}
								</Chip></span>
							{/each}
						</div>
					{/if}
					{#if selectedJob}
						<p class="flex flex-wrap items-center gap-x-2 body-small text-on-surface-variant">
							Agent {agentName(selectedJob.agent_id)}{selectedJob.image ? ` · ${selectedJob.image}` : ' · host shell'} · queued {absoluteTime(selectedJob.queued_at)}
							{#if aiStatus.enabled && (selectedJob.status === 'failed' || selectedJob.status === 'lost')}
								<button type="button" class="ml-auto flex items-center gap-1 label-large text-primary hover:underline disabled:opacity-60" disabled={explaining} onclick={() => explain(selectedJob!.id)}>
									<Icon name="auto_awesome" size={16} />{explaining ? 'Asking…' : 'Explain this failure'}
								</button>
							{/if}
						</p>
						{#if explanation && explanation.jobId === selectedJob.id}
							<div class="rounded-md border border-outline-variant bg-surface-container px-4 py-3">
								<div class="mb-1 flex items-center gap-1.5 label-large text-on-surface"><Icon name="auto_awesome" size={16} class="text-primary" />Why it failed<span class="ml-auto body-small text-on-surface-variant">{explanation.model}</span><IconButton icon="close" label="Hide" size="sm" onclick={() => (explanation = null)} /></div>
								<RichText text={explanation.text} projectId={task.project_id} class="body-medium text-on-surface" />
							</div>
						{/if}
						{#if hasResults}
							<div class="flex h-9 w-fit shrink-0 overflow-hidden rounded-full border border-outline" role="group" aria-label="Run view">
								{#each [{ id: 'console', label: 'Console', icon: 'terminal' }, { id: 'results', label: resultCount ? `Results · ${resultCount}` : 'Results', icon: 'table_view' }] as v, i (v.id)}
									<button
										type="button"
										aria-pressed={outputView === v.id}
										class="state-layer focus-ring inline-flex items-center gap-1.5 px-4 label-large transition-colors {i ? 'border-l border-outline' : ''} {outputView === v.id
											? 'bg-secondary-container text-on-secondary-container'
											: 'text-on-surface'}"
										onclick={() => (outputView = v.id as 'console' | 'results')}
									>
										<Icon name={outputView === v.id ? 'check' : v.icon} size={18} />{v.label}
									</button>
								{/each}
							</div>
						{/if}
						{#key selectedJob.id}
							{#if hasResults && outputView === 'results'}
								<JobResults job={selectedJob} onload={(o) => (resultCount = o.filter((x) => !x.error).length || null)} />
							{:else}
								<JobConsole job={selectedJob} oncancel={editable ? cancel : undefined} title={task.title} canInteract={editable} />
							{/if}
						{/key}
						{#if runFiles.length}
							<div class="flex flex-wrap items-center gap-2">
								<span class="label-medium text-on-surface-variant">Files from this run</span>
								{#each runFiles as f (f.id)}
									<a class="inline-flex h-8 items-center gap-1.5 rounded-sm border border-outline-variant px-2.5 label-large text-on-surface hover:bg-on-surface/8" href={api().attachments.url(f.id)} title="{f.filename} · {bytes(f.size)}">
										<Icon name="description" size={18} class="text-primary" />
										<span class="max-w-64 truncate font-mono text-[12px]">{f.filename}</span>
									</a>
								{/each}
							</div>
						{/if}
					{:else}
						<div class="flex flex-1 flex-col items-center justify-center gap-2 rounded-md bg-surface-container p-8 text-center">
							<Icon name="terminal" size={40} class="text-on-surface-variant" />
							<p class="body-medium text-on-surface-variant">{editable ? 'No runs yet. Press Run or drag the card into In Progress.' : 'No runs yet.'}</p>
						</div>
					{/if}
				</div>
			{:else if tab === 'comments'}
				<div class="flex flex-col gap-4">
					{#if editable}<div class="flex flex-col gap-2">
						<div class="relative">
							<TextField label="Add a comment" bind:value={newComment} multiline rows={3} supporting="Markdown supported · Ctrl+Enter to post · @ to mention someone · paste an image to add it" oninput={newSuggest.oninput} onkeydown={(e) => newSuggest.onkeydown(e) || ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && postComment())} onblur={newSuggest.close} onpaste={images.pasteInto(newCommentTarget)} />
							<MentionMenu suggest={newSuggest} />
						</div>
						<div class="flex items-center justify-end gap-3">
							{#if images.uploading > 0}<span class="body-small text-on-surface-variant">Uploading image…</span>{/if}
							<Button icon="send" loading={posting} onclick={postComment} disabled={!newComment.trim() || images.uploading > 0}>Comment</Button>
						</div>
					</div>{/if}
					{#each newestFirst as c (c.id)}
						{@const authorName = people.user(task.project_id, c.author_id ?? '')?.username}
						<div class="flex gap-3" id="comment-{c.id}" in:enter={{ enabled: liveSide }} out:leave={{ enabled: liveSide }}>
							{#if authorName}
								<a href="/people/{authorName}" class="shrink-0 self-start rounded-full" aria-label="{c.author_name}'s page"><Avatar name={c.author_name} src={c.author_avatar} tone="secondary" /></a>
							{:else}
								<Avatar name={c.author_name} src={c.author_avatar} tone="secondary" />
							{/if}
							<div class="min-w-0 flex-1 rounded-md bg-surface-container px-4 py-3" class:mentions-me={mentionsMe(c)} class:flash={flashing === c.id}>
								<div class="flex items-center gap-2 body-small text-on-surface-variant">
									{#if authorName}<a href="/people/{authorName}" class="label-large text-on-surface hover:underline">{c.author_name}</a>{:else}<span class="label-large text-on-surface">{c.author_name}</span>{/if}
									<span title={absoluteTime(c.created_at)}>{relativeTime(c.created_at)}</span>
									{#if mentionsMe(c)}<span class="flex items-center gap-0.5 rounded-full bg-primary-container px-1.5 py-0.5 label-small text-on-primary-container"><Icon name="alternate_email" size={12} />Mentions you</span>{/if}
									{#if c.edited_at}
										<span class="italic" title="Edited {absoluteTime(c.edited_at)}">· edited</span>
									{/if}
									<span class="flex-1"></span>
									{#if canEdit(c) && editingId !== c.id}
										<IconButton icon="edit" label="Edit comment" size="sm" onclick={() => startEdit(c)} />
									{/if}
									{#if canEdit(c)}<IconButton icon="delete" label="Delete comment" size="sm" onclick={() => removeComment(c)} />{/if}
								</div>
								{#if editingId === c.id}
									<div class="mt-2 flex flex-col gap-2">
										<div class="relative">
											<TextField
												id="comment-edit-{c.id}"
												label="Edit comment"
												bind:value={editDraft}
												multiline
												rows={4}
												supporting="Ctrl+Enter to save · Esc to cancel · @ to mention · paste an image to add it"
												oninput={editSuggest.oninput}
												onkeydown={(e) => editSuggest.onkeydown(e) || onEditKey(c, e)}
												onblur={editSuggest.close}
												onpaste={images.pasteInto(editTarget)}
											/>
											<MentionMenu suggest={editSuggest} />
										</div>
										<div class="flex items-center justify-end gap-2">
											{#if images.uploading > 0}<span class="body-small text-on-surface-variant">Uploading image…</span>{/if}
											<Button variant="text" onclick={cancelEdit}>Cancel</Button>
											<Button icon="check" loading={savingEdit} disabled={!editDraft.trim() || images.uploading > 0} onclick={() => saveEdit(c)}>Save</Button>
										</div>
									</div>
								{:else}
									<RichText text={c.body} projectId={task.project_id} class="mt-1 body-medium text-on-surface" />
								{/if}
							</div>
						</div>
					{:else}
						<p class="body-medium text-on-surface-variant">No comments yet.</p>
					{/each}
				</div>
			{:else if tab === 'attachments'}
				<div class="flex flex-col gap-4">
					{#if editable}
						<div class="flex items-center gap-2">
							<Button variant="tonal" icon="upload" loading={uploading} onclick={() => fileInput?.click()}>Upload</Button>
							<span class="body-small text-on-surface-variant">or paste a screenshot anywhere in this card</span>
							<input bind:this={fileInput} type="file" multiple class="hidden" onchange={(e) => upload((e.currentTarget as HTMLInputElement).files)} />
						</div>
					{/if}
					{#if screenshots.length}
						<div class="grid grid-cols-2 gap-3 md:grid-cols-3">
							{#each screenshots as a, i (a.id)}
								<figure class="group relative overflow-hidden rounded-md bg-surface-container" in:pop={{ enabled: liveSide }} out:pop={{ enabled: liveSide }} animate:glide={{ enabled: liveSide }}>
									<button type="button" class="block w-full cursor-zoom-in" aria-label="View {a.filename}" onclick={() => lightbox.open(screenshots.map((s) => ({ src: api().attachments.url(s.id, true), name: s.filename })), i)}>
										<img src={api().attachments.url(a.id, true)} alt={a.filename} class="aspect-video w-full object-cover" loading="lazy" />
									</button>
									<figcaption class="flex items-center gap-1 px-2 py-1 body-small text-on-surface-variant">
										<span class="flex-1 truncate" title={a.filename}>{a.filename}</span>
										<span>{bytes(a.size)}</span>
										{#if editable}<IconButton icon="delete" label="Delete" size="sm" onclick={() => removeAttachment(a)} />{/if}
									</figcaption>
								</figure>
							{/each}
						</div>
					{/if}
					{#if files.length}
						<ul class="divide-y divide-outline-variant rounded-md bg-surface-container">
							{#each files as a (a.id)}
								<li class="flex items-center gap-3 px-3 py-2" in:enter={{ enabled: liveSide }} out:leave={{ enabled: liveSide }}>
									<Icon name="draft" class="text-on-surface-variant" />
									<a class="min-w-0 flex-1 truncate body-medium text-primary underline-offset-2 hover:underline" href={api().attachments.url(a.id)}>{a.filename}</a>
									<span class="body-small text-on-surface-variant">{bytes(a.size)} · {relativeTime(a.created_at)}</span>
									{#if editable}<IconButton icon="delete" label="Delete" size="sm" onclick={() => removeAttachment(a)} />{/if}
								</li>
							{/each}
						</ul>
					{/if}
					{#if attachments.length === 0}<p class="body-medium text-on-surface-variant">No attachments.</p>{/if}
				</div>
			{:else if tab === 'fields' && !editable}
				{#if Object.keys(task.custom_fields).length}
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 body-medium">
						{#each Object.entries(task.custom_fields) as [key, value] (key)}
							<dt class="label-large text-on-surface">{key}</dt>
							<dd class="break-words text-on-surface">{String(value ?? '')}</dd>
						{/each}
					</dl>
				{:else}
					<p class="body-medium text-on-surface-variant">No custom fields.</p>
				{/if}
			{:else if tab === 'fields'}
				<div class="flex flex-col gap-3">
					{#each Object.entries(task.custom_fields) as [key, value] (key)}
						<div class="flex items-center gap-2">
							<span class="w-40 truncate label-large text-on-surface" title={key}>{key}</span>
							<TextField label="" value={String(value ?? '')} class="flex-1 [&_div]:h-10" oninput={() => edited(`field:${key}`)} onblur={(e) => { const v = (e.currentTarget as HTMLInputElement).value; if (dirty.has(`field:${key}`) && v !== String(value ?? '')) { dirty.delete(`field:${key}`); setField(key, v); } }} />
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
			{#if task.playbook_step_id}
				<!-- A step lives in its run's card, not in a column. -->
				<p class="flex items-center gap-2 rounded-md bg-surface-container px-3 py-2 body-small text-on-surface-variant"><Icon name="account_tree" size={18} />A step of a playbook run</p>
			{:else}
				<Select label="Column" value={task.column_id} disabled={!editable} options={columns.map((c) => ({ value: c.id, label: c.name }))} onchange={moveTo}>
					{#snippet leading(id)}<ColumnGlyph role={columns.find((c) => c.id === id)?.role} />{/snippet}
				</Select>
			{/if}
			<Select label="Priority" bind:value={priority} disabled={!editable} options={priorityOptions} onchange={(v) => save({ priority: Number(v) })}>
				{#snippet leading(level)}<PriorityGlyph {level} />{/snippet}
			</Select>
			<AssigneePicker projectId={task.project_id} value={task.assignees} {editable} onchange={(ids) => save({ assignees: ids })} />
			{#if !task.playbook_step_id}
				<div class="flex flex-col gap-1.5">
					<p class="label-large text-on-surface-variant">When it finishes</p>
					{#if editable && myId}
						<Switch checked={notifyMe} onchange={toggleNotifyMe} label="Notify me" />
					{/if}
					{#if othersNotified}<p class="body-small text-on-surface-variant">{othersNotified} other{othersNotified === 1 ? '' : 's'} will be notified</p>{/if}
					<p class="flex items-center gap-1.5 body-small text-on-surface-variant">
						<Icon name={task.alerts?.channels?.length ? 'notifications_active' : 'campaign'} size={16} />
						{task.alerts?.channels?.length ? `Chat alerts: ${task.alerts.channels.length} channel${task.alerts.channels.length === 1 ? '' : 's'}` : 'No chat alerts'}
						{#if editable}<button type="button" class="text-primary hover:underline" onclick={() => (alertsOpen = true)}>{task.alerts?.channels?.length ? 'Change' : 'Add'}</button>{/if}
					</p>
				</div>
			{/if}
			<TextField label="Tags" bind:value={tagsText} readonly={!editable} supporting={editable ? 'Comma separated' : ''} oninput={() => edited('tags')} onblur={() => { const tags = tagsText.split(',').map((t) => t.trim()).filter(Boolean); if (tags.join() !== task.tags.join()) commit('tags', { tags }); }} />
			<div class="rounded-md bg-surface-container p-4 body-small text-on-surface-variant">
				<p class="mb-2 title-small text-on-surface">Details</p>
				<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
					<dt>Kind</dt><dd class="text-on-surface">{task.run ? 'playbook run' : task.playbook_step_id ? 'playbook step' : task.kind}</dd>
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

{#if images.pending[0]}
	{#key images.pending[0]}
		<NameImageDialog
			file={images.pending[0].file}
			remaining={images.pending.length - 1}
			onconfirm={(name) => images.confirm(name)}
			oncancel={() => images.cancel()}
		/>
	{/key}
{/if}

<AlertRulesDialog bind:open={alertsOpen} {task} onsaved={onupdated} />
<DuplicateDialog
	bind:open={duplicateOpen}
	{task}
	counts={duplicateCounts}
	onduplicated={(copy) => { onupdated(copy); openCard(copy.id); }}
/>
{#if rerun}
	<RunPlaybookDialog
		bind:open={rerunOpen}
		spec={rerun.spec}
		projects={[rerun.project]}
		projectId={rerun.project.id}
		initial={rerun.inputs}
		onRun={(r) => r.card_id && openCard(r.card_id)}
	/>
{/if}
