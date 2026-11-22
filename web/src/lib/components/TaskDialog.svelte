<script lang="ts">
	import Spoilers from './Spoilers.svelte';
	import RouteChoice from './routes/RouteChoice.svelte';
	import Reactions from './reactions/Reactions.svelte';
	import { METHOD_WORDS } from '$lib/routes';
	import type { ManualSource, NetworkRoute, RouteMethod } from '$lib/api';
	import { agentsForRoutes, routeAgentsWords } from '$lib/routes';
	import { hasSpoilers } from '$lib/spoilers';
	import { originLabel } from '$lib/origin';
	import { copyableCode } from '$lib/copyCode';
	import PersonName from '$lib/components/PersonName.svelte';
	import type { Agent, ArgErrors, Attachment, Column, Comment, CommandDefinition, Job, PlaybookRun, PlaybookSpec, Project, Task, UUID } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { loadTags, type TagSuggestion } from '$lib/tags';
	import { realtime } from '$lib/realtime.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { confirmAction } from '$lib/confirm.svelte';
	import { renderMarkdown } from '$lib/markdown';
	import { lightbox, openImagesIn } from '$lib/lightbox.svelte';
	import { PastedImages } from '$lib/pastedImages.svelte';
	import { relativeTime, absoluteTime, bytes, duration } from '$lib/format';
	import PluginActions from './PluginActions.svelte';
	import PluginFrame from './PluginFrame.svelte';
	import { pluginPanels } from '$lib/plugins.svelte';
	import { Button, Chip, Dialog, Icon, IconButton, Menu, MenuItem, Select, Switch, Tabs, TextField, Avatar, TagInput } from '$lib/m3';
	import StatusChip from './StatusChip.svelte';
	import JobConsole from './JobConsole.svelte';
	import SnapshotDialog from './SnapshotDialog.svelte';
	import JobResults from './JobResults.svelte';
	import JobChanges from './JobChanges.svelte';
	import ColumnGlyph from './ColumnGlyph.svelte';
	import PriorityGlyph, { priorityOptions } from './PriorityGlyph.svelte';
	import NameImageDialog from './NameImageDialog.svelte';
	import MoveWritingDialog from './MoveWritingDialog.svelte';
	import ParseOutputDialog from './ParseOutputDialog.svelte';
	import RunPanel from './RunPanel.svelte';
	import SubtaskPanel from './SubtaskPanel.svelte';
	import ServicePanel from './ServicePanel.svelte';
	import GroupPicker from './GroupPicker.svelte';
	import { kindIcon } from '$lib/taskStatus';
	import AiSummary from './AiSummary.svelte';
	import DuplicateDialog from './DuplicateDialog.svelte';
	import DueDateField from './DueDateField.svelte';
	import CardDependencies from './CardDependencies.svelte';
	import { startAnyway } from '$lib/blocked';
	import AlertRulesDialog from './AlertRulesDialog.svelte';
	import StepDecisions from './StepDecisions.svelte';
	import StepDecision from './StepDecision.svelte';
	import { aiStatus } from '$lib/ai.svelte';
	import RunPlaybookDialog from './RunPlaybookDialog.svelte';
	import AssigneePicker from './AssigneePicker.svelte';
	import MarkdownEditor from './MarkdownEditor.svelte';
	import RichText from './RichText.svelte';
	import { people } from '$lib/people.svelte';
	import { appSettings } from '$lib/appSettings.svelte';
	import { enter, leave, pop, glide } from '$lib/motion';
	import CommandArgsForm from './CommandArgsForm.svelte';
	import VarBindings from './VarBindings.svelte';
	import { onMount, tick } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { canEdit as roleCanEdit } from '$lib/session';
	import { isListRef, lists } from '$lib/lists.svelte';
	import { agentsFor, describeSelector, describeSender, httpSenderOptions, selectorFrom, selectorOptions, selectorValue, usableBy } from '$lib/agentSelector';

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
	// Built-in tabs, or a plugin's (`plugin:<plugin id>:<panel id>`, manifest `ui` slot card.tab).
	let tab = $state<'run' | 'subtasks' | 'output' | 'comments' | 'attachments' | 'fields' | 'history' | `plugin:${string}`>('output');

	// ---- editable fields (debounced save) ----
	let title = $state('');
	let description = $state('');
	let command = $state('');
	let image = $state('');
	let containerFlagsText = $state('');
	let agentId = $state('');
	let routeId = $state('');
	let runOnMove = $state(true);
	let timeout = $state('');
	let rateLimit = $state('');
	let priority = $state('0');
	let tags = $state<string[]>([]);
	let tagSuggestions = $state<TagSuggestion[]>([]);
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
		agentId = selectorValue(task.agent_selector);
		routeId = task.route ?? '';
		runOnMove = task.run_on_move;
		timeout = task.timeout_s ? String(task.timeout_s) : '';
		rateLimit = task.rate_limit ?? '';
		priority = String(task.priority);
		tags = [...task.tags];
		void loadTags('cards', task.project_id).then((t) => (tagSuggestions = t));
		editingDescription = false;
		// A notification about a comment (?comment=<id>) opens the card on it.
		// Search results link straight to a tab, a run and its results (?tab=&job=&view=). With
		// ?step= those are for the step's card on top of this run card, not for this one.
		const params = new URLSearchParams(page.url.searchParams);
		if (params.get('step') && (task.run || task.subtasks?.total)) for (const k of ['comment', 'tab', 'job', 'view']) params.delete(k);
		const linked = params.get('comment');
		const wantedTab = params.get('tab');
		tab = linked
			? 'comments'
			: wantedTab && (['run', 'subtasks', 'output', 'comments', 'attachments', 'fields', 'history'].includes(wantedTab) || wantedTab.startsWith('plugin:'))
				? (wantedTab as typeof tab)
				: task.run
					? 'run'
					: task.subtasks?.total
					? 'subtasks'
					: task.kind === 'command'
					? 'output'
					: 'comments';
		selectedJobId = params.get('job') ?? task.latest_job?.id ?? null;
		// A shell someone just opened (New card → Shell): its terminal is theirs as soon as it runs.
		if (task.shell && params.get('control')) controlOnStart = params.get('control');
		const view = params.get('view');
		wantedView = view === 'results' || view === 'changes' ? view : params.get('job') ? 'console' : null;
		shownLink = linked;
		void loadSide().then(() => {
			if (linked) void showComment(linked);
		});
	});

	// ---- library command ----
	let libraryDef = $state<CommandDefinition | null>(null);
	/** The card's library command always runs in a terminal. */
	let libTty = $derived(!!libraryDef?.spec.tty);
	/** Runs until stopped: its library command's `service:`, or the card's own (typed commands). */
	let libService = $derived(libraryDef?.spec.service ?? null);
	let service = $derived(libService ?? task.service ?? null);
	const SIGNALS = ['TERM', 'INT', 'HUP', 'QUIT', 'USR1', 'USR2'] as const;
	/** An HTTP action (docs/ACTIONS.md): the Castor server sends its request; the card only
	 *  fills in arguments, variables and a rate limit. */
	let isHttp = $derived(libraryDef?.spec.kind === 'http');
	/** An MCP action: a tool on an MCP server; where it runs is the server's connection's choice. */
	let isMcp = $derived(libraryDef?.spec.kind === 'mcp');
	/** A plugin's library action: its plugin runs it. */
	let isPluginAction = $derived(libraryDef?.spec.kind === 'plugin');
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

	async function saveDescription(text: string) {
		if (text !== task.description) {
			try {
				onupdated(await api().tasks.update(task.id, { description: text }));
			} catch (err) {
				snackbar.error(err instanceof ApiError ? err.message : 'Could not save the description');
				throw err; // the editor keeps the text
			}
		}
		editingDescription = false;
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
	// …or what changed in them since an earlier run (Changes).
	type OutputView = 'console' | 'results' | 'changes';
	let outputView = $state<OutputView>('console');
	let resultCount = $state<number | null>(null);
	let changeCount = $state<number | null>(null);
	let hasResults = $derived(!!selectedJob && (!!selectedJob.output_specs?.length || !!task.command_ref));
	let viewFor: UUID | null = null;
	let wantedView: OutputView | null = null;
	$effect(() => {
		const job = selectedJob;
		if (!job || job.id === viewFor) return;
		viewFor = job.id;
		resultCount = changeCount = null;
		const done = ['succeeded', 'failed', 'lost'].includes(job.status);
		outputView = wantedView ?? (done && job.output_specs?.length ? 'results' : 'console');
		wantedView = null;
	});
	let activeJob = $derived(task.latest_job && ['approval', 'queued', 'dispatched', 'running', 'waiting'].includes(task.latest_job.status) ? task.latest_job : null);

	// ---- comments / attachments / history ----
	let comments = $state<Comment[]>([]);
	/** Shown newest first, under the box, so a long thread doesn't need scrolling to reply. */
	let newestFirst = $derived([...comments].reverse());
	let attachments = $state<Attachment[]>([]);
	let history = $state<{ id: number; message: string; actor_name: string; created_at: string; payload?: { via?: string } }[]>([]);
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
			} else if (e.name === 'reactions.changed') {
				const d = e.data as { comment_id: UUID | null; reactions: Comment['reactions'] };
				if (d.comment_id) comments = comments.map((x) => (x.id === d.comment_id ? { ...x, reactions: d.reactions } : x));
			} else if (e.name === 'attachment.created') {
				const a = e.data as Attachment;
				if (a.task_id === task.id && !attachments.some((x) => x.id === a.id)) attachments = [...attachments, a];
			} else if (e.name === 'attachment.updated') {
				const a = e.data as Attachment;
				if (a.task_id === task.id) attachments = attachments.map((x) => (x.id === a.id ? a : x));
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
			const job = await startAnyway(task.title, (force) => api().tasks.run(task.id, force));
			if (!job) return; // they chose not to start it while it waits on other cards
			selectedJobId = job.id;
			if (task.shell) controlOnStart = job.id;
			tab = 'output';
			// Run also moves the card to In Progress; refresh it here in case the live
			// update is late or the realtime socket is reconnecting.
			onupdated(await api().tasks.get(task.id));
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not start job');
		}
	}
	// ---- a shell card: its terminal opens as soon as the shell runs ----
	let controlOnStart = $state<UUID | null>(null);

	// ---- output brought in to be parsed (a comment's, a run's, or pasted) ----
	let parseSource = $state<ManualSource | null>(null);
	let parseOpen = $state(false);
	function parseFrom(source: ManualSource) {
		parseSource = source;
		parseOpen = true;
	}
	function parsed(job: Job) {
		if (!jobs.some((j) => j.id === job.id)) jobs = [job, ...jobs];
		wantedView = 'results';
		selectedJobId = job.id;
		tab = 'output';
	}
	const ORIGIN_WORDS: Record<string, string> = { paste: 'pasted', comment: 'from a comment', post: 'from a timeline post', job: 'from a run' };

	// ---- a run waiting for approval ----
	let rejectReason = $state('');
	let deciding = $state(false);
	/** Who started the waiting run, by name (from the project's people). */
	let startedBy = $derived(activeJob?.created_by ? (people.list(task.project_id).find((u) => u.id === activeJob!.created_by)?.display_name ?? 'someone') : 'Castor');
	let mineToApprove = $derived(!!activeJob && activeJob.created_by === myId);
	async function decide(approve: boolean) {
		if (!activeJob) return;
		deciding = true;
		try {
			if (approve) await api().jobs.approve(activeJob.id);
			else await api().jobs.reject(activeJob.id, rejectReason);
			rejectReason = '';
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not decide');
		} finally {
			deciding = false;
		}
	}

	// ---- a preview, and retrying what failed ----
	let previewLabel = $derived(libraryDef?.spec.preview?.label ?? 'Preview');
	let retryPlan = $state<{ jobId: UUID; arg: string; values: string[] } | null>(null);
	let retrying = $state(false);
	let retryKey = $derived(selectedJob && libraryDef?.spec.retry_failed && ['failed', 'lost'].includes(selectedJob.status) && selectedJob.purpose !== 'preview' ? selectedJob.id : null);
	$effect(() => {
		const id = retryKey;
		if (!id) return;
		let gone = false;
		// Its results are parsed just after it ends: ask again for a little while if they weren't there yet.
		const ask = (left: number) =>
			api().jobs.retryPlan(id).then(
				(p) => { if (!gone) retryPlan = { jobId: id, ...p }; },
				() => { if (!gone && left > 0) timer = setTimeout(() => ask(left - 1), 2000); }
			);
		let timer: ReturnType<typeof setTimeout> | undefined;
		void ask(8);
		return () => { gone = true; clearTimeout(timer); };
	});
	async function retryFailed() {
		if (!retryPlan) return;
		retrying = true;
		try {
			const job = await api().jobs.retryFailed(retryPlan.jobId);
			if (!jobs.some((j) => j.id === job.id)) jobs = [job, ...jobs];
			selectedJobId = job.id;
			retryPlan = null;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not retry it');
		} finally {
			retrying = false;
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
			throw err; // the editor keeps the text
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

	// ---- moving a comment to another card, or out to the timeline ----
	let movingComment = $state<Comment | null>(null);
	async function moveComment(toTaskId: UUID | null) {
		const c = movingComment;
		if (!c) return;
		const r = await api().tasks.moveComment(task.id, c.id, toTaskId);
		comments = comments.filter((x) => x.id !== c.id);
		const where = toTaskId ? 'that card' : 'the timeline';
		snackbar.show(`Moved the comment to ${where}`, {
			action: { label: 'Open', run: () => void goto(toTaskId ? `/projects/${task.project_id}?task=${toTaskId}&comment=${r.comment?.id}` : `/projects/${task.project_id}/dashboard?post=${r.post?.id}`) }
		});
	}

	function startEdit(c: Comment) {
		editingId = c.id;
		editDraft = c.body;
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
			throw err;
		} finally {
			savingEdit = false;
		}
	}


	// ---- images pasted into the comment box: named, uploaded as card attachments, embedded ----
	const images = new PastedImages(async (file) => {
		const a = await api().tasks.upload(task.id, file, 'screenshot');
		if (!attachments.some((x) => x.id === a.id)) attachments = [...attachments, a];
		return api().attachments.url(a.id, true);
	});

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

	// Bumped when a move doesn't happen, so the Column field shows where the card still is.
	let columnPick = $state(0);
	async function moveTo(columnId: string) {
		const col = columns.find((c) => c.id === columnId);
		if (!col) return;
		try {
			const moved = await startAnyway(task.title, (force) => api().tasks.move(task.id, columnId, Number.MAX_SAFE_INTEGER / 2, force));
			if (moved) onupdated(moved);
			else columnPick++;
		} catch (err) {
			columnPick++;
			snackbar.error(err instanceof ApiError ? err.message : 'Move failed');
		}
	}

	// ---- a title with ||spoilers|| shows them hidden until you edit it ----
	let titleEditing = $state(false);
	let titleInput = $state<HTMLInputElement>();
	async function editTitle() {
		titleEditing = true;
		await tick();
		titleInput?.focus();
	}

	// ---- a subtask: its activity card ----
	let parentTitle = $state<string | null>(null);
	$effect(() => {
		const id = task.parent_id;
		parentTitle = null;
		if (id) void api().tasks.get(id).then((p) => (parentTitle = p.title)).catch(() => {});
	});
	let parentHref = $derived.by(() => {
		const url = new URL(page.url);
		for (const k of ['step', 'comment', 'tab', 'job', 'view']) url.searchParams.delete(k);
		if (task.parent_id) url.searchParams.set('task', task.parent_id);
		return url.pathname + url.search;
	});
	// ---- moving this card into another one, as its subtask ----
	let intoOpen = $state(false);
	/** Not a run's card or step, not a subtask already, and not an activity itself (one level deep). */
	let canMoveInto = $derived(!task.run && !task.playbook_step_id && !task.playbook_run_id && !task.parent_id && !task.subtasks?.total);
	async function moveInto(parentId: UUID | null) {
		if (!parentId) return;
		await api().tasks.addSubtasks(parentId, [task.id]); // a failure keeps the picker open, saying why
		snackbar.show(`“${task.title}” is a subtask now`);
		// It's off the board: show it on top of the card it went into.
		const url = new URL(page.url);
		for (const k of ['comment', 'tab', 'job', 'view']) url.searchParams.delete(k);
		url.searchParams.set('task', parentId);
		url.searchParams.set('step', task.id);
		void goto(url, { replaceState: true, noScroll: true, keepFocus: true });
	}

	async function release() {
		try {
			const t = await api().tasks.release(task.id);
			onupdated(t);
			snackbar.show(`“${t.title}” is its own card on the board again`);
			const url = new URL(page.url);
			for (const k of ['step', 'comment', 'tab', 'job', 'view']) url.searchParams.delete(k);
			url.searchParams.set('task', t.id);
			void goto(url, { replaceState: true, noScroll: true, keepFocus: true });
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not take it out');
		}
	}

	/** A run card's step (or an activity's subtask) opens as its own card on top (?step=<its card>). */
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
		void save({ alerts: next.channels.length || people.length || next.groups?.length ? next : null });
	}
	function setNotifiedGroups(groups: string[]) {
		const current = task.alerts ?? { channels: [], on: 'failure' as const, long_minutes: null, decisions: true, people: [] };
		const next = { ...current, groups };
		void save({ alerts: next.channels.length || next.people?.length || groups.length ? next : null });
	}
	/** Asks before each run: who may let a run go (the card's groups, else the project's). */
	let asks = $derived(task.kind === 'command' && (!!task.confirm || !!libraryDef?.spec.confirm));
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

	/** Off the board (it keeps everything); the dialog closes, with an Undo. */
	async function archive() {
		try {
			const t = await api().tasks.archive(task.id);
			open = false;
			onupdated(t);
			snackbar.show(`Archived “${t.title}”`, {
				action: { label: 'Undo', run: () => void api().tasks.restore(t.id).then(onupdated).catch(() => snackbar.error('Could not restore it')) }
			});
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not archive the card');
		}
	}
	async function restore() {
		try {
			onupdated(await api().tasks.restore(task.id));
			snackbar.show('Back on the board');
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not restore the card');
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
	// Phones: a command card that has run opens with its form folded, so its output is in view.
	let formOpen = $state(true);
	onMount(() => {
		if (task.kind === 'command' && task.latest_job && matchMedia('(max-width: 639px)').matches) formOpen = false;
	});
	let columnMenu = $state(false);
	// A playbook step runs where its run does: one agent per run, never a batch.
	let agentName = (id: UUID | null) => agents.find((a) => a.id === id)?.name ?? '—';
	// The card's route (its own or the project's default): when it only works from some agents, only they're offered.
	let cardRoute = $state<NetworkRoute | null>(null);
	let routedAgents = $derived.by(() => {
		const able = agentsForRoutes(agentsFor(agents, task.project_id, me), [cardRoute]);
		// The agent it's set to stays in the list (with the warning below), so the choice still shows.
		const pinned = agents.find((a) => a.id === task.agent_selector?.agent_id);
		return pinned && !able.includes(pinned) ? [...able, pinned] : able;
	});
	let routeMismatch = $derived.by(() => {
		const s = task.agent_selector;
		if (!cardRoute?.agents || !s) return false;
		const able = agentsForRoutes(agents, [cardRoute]);
		if (s.agent_id) return !able.some((a) => a.id === s.agent_id);
		return !!s.tags?.length && !able.some((a) => s.tags!.every((t) => a.tags.includes(t)));
	});
	let routeAgentHint = $derived(
		!cardRoute?.agents
			? ''
			: routeMismatch
				? `“${cardRoute.name}” only works from ${routeAgentsWords(cardRoute, agentName)}: this isn’t one, so it won’t run`
				: `Only the agents “${cardRoute.name}” works from`
	);
	// The agent it's pinned to is only for some people, and not you: your runs of it wait.
	let notMine = $derived.by(() => {
		const pinned = agents.find((a) => a.id === task.agent_selector?.agent_id);
		return pinned && !usableBy(pinned, me) ? pinned : null;
	});
	let agentProblem = $derived(
		routeMismatch ? routeAgentHint : notMine ? `${notMine.name} is only for some people, not you: when you run this card, it waits` : null
	);
	let agentHint = $derived(agentProblem ?? routeAgentHint);
	let agentOptions = $derived(selectorOptions(routedAgents, task.agent_selector, !task.playbook_step_id));
	let senderOptions = $derived(httpSenderOptions(routedAgents, task.agent_selector));
	let runFiles = $derived(attachments.filter((a) => a.job_id && a.job_id === selectedJob?.id));
	let screenshots = $derived(attachments.filter((a) => a.content_type.startsWith('image/')));
	/** An image's address; a snapshot's changes in place, so its size keeps the browser from showing an old copy. */
	const imageUrl = (a: Attachment) => `${api().attachments.url(a.id, true)}${a.snapshot ? `&v=${a.size}` : ''}`;

	// ---- terminal snapshots ----
	let snapshot = $state<{ jobId: UUID | null; range: { from: number; to: number } | null; attachment: Attachment | null } | null>(null);
	function savedSnapshot(a: Attachment) {
		attachments = attachments.some((x) => x.id === a.id) ? attachments.map((x) => (x.id === a.id ? a : x)) : [...attachments, a];
	}
	let files = $derived(attachments.filter((a) => !a.content_type.startsWith('image/')));
	$effect(() => void pluginPanels.load(task.project_id));
	let cardPanels = $derived(task.playbook_step_id ? [] : pluginPanels.for(task.project_id, 'card.tab'));
	let openPanel = $derived(tab.startsWith('plugin:') ? cardPanels.find((p) => tab === `plugin:${p.plugin.id}:${p.id}`) : undefined);
	/** Cards inside this one (an activity); not for playbook runs, their steps, or a subtask. */
	let canHaveSubtasks = $derived(!task.run && !task.playbook_step_id && !task.playbook_run_id && !task.parent_id);
	let tabs = $derived([
		...(task.run ? [{ id: 'run', label: 'Run', icon: 'account_tree', badge: undefined }] : []),
		...(canHaveSubtasks ? [{ id: 'subtasks', label: 'Subtasks', icon: 'checklist', badge: task.subtasks?.total || undefined }] : []),
		...(task.kind === 'command' || jobs.length ? [{ id: 'output', label: 'Output', icon: 'terminal', badge: jobs.length }] : []),
		{ id: 'comments', label: 'Comments', icon: 'chat_bubble', badge: comments.length },
		{ id: 'attachments', label: 'Attachments', icon: 'attach_file', badge: attachments.length },
		{ id: 'fields', label: 'Fields', icon: 'data_object', badge: Object.keys(task.custom_fields).length },
		{ id: 'history', label: 'History', icon: 'history' },
		...cardPanels.map((p) => ({ id: `plugin:${p.plugin.id}:${p.id}`, label: p.title, icon: p.icon, badge: undefined }))
	]);
</script>

<Dialog bind:open size="lg" initialFocus="dialog" onclose={onclose}>
	{#snippet header()}
		<div class="flex items-start gap-2 px-4 pt-4 pb-3 sm:gap-3 sm:px-6 sm:pt-5">
			<Icon name={kindIcon(task)} class="mt-2 {task.run || task.kind === 'command' ? 'text-primary' : 'text-on-surface-variant'}" />
			<div class="min-w-0 flex-1">
				{#if hasSpoilers(title) && !titleEditing}
					<!-- A title with a ||spoiler||: shown hidden, edited as written (click it, or the pencil). -->
					<div class="flex min-w-0 items-start gap-1">
						<h2 class="min-w-0 headline-small text-on-surface {editable ? 'cursor-text' : ''}">
							{#if editable}<button type="button" class="text-left" onclick={editTitle}><Spoilers text={title} /></button>{:else}<Spoilers text={title} />{/if}
						</h2>
						{#if editable}<IconButton icon="edit" label="Edit the title" size="sm" onclick={editTitle} />{/if}
					</div>
				{:else}
				<input
					bind:this={titleInput}
					class="w-full bg-transparent headline-small text-on-surface outline-none focus:underline decoration-primary/40 underline-offset-4"
					bind:value={title}
					readonly={!editable}
					oninput={() => edited('title')}
					onblur={() => {
						titleEditing = false;
						if (title.trim() && title !== task.title) commit('title', { title: title.trim() });
					}}
					autocomplete="off"
					onkeydown={(e) => e.key === 'Enter' && (e.currentTarget as HTMLInputElement).blur()}
					aria-label="Title"
				/>
				{/if}
				<div class="mt-1 flex flex-wrap items-center gap-2 body-small text-on-surface-variant">
					<StatusChip job={task.latest_job} batch={task.batch} />
					<span><span class="hidden sm:inline">created {relativeTime(task.created_at)} · </span>updated {relativeTime(task.updated_at)}</span>
				</div>
				<!-- Phones and narrow windows: where it is and who has it, in one row (the side
				     column with the full controls is at the bottom there). -->
				{#if !task.playbook_step_id}
					<div class="mt-2 flex flex-wrap items-center gap-2 lg:hidden">
						<Menu bind:open={columnMenu}>
							{#snippet anchor()}
								<button type="button" class="state-layer flex h-8 items-center gap-1.5 rounded-sm border border-outline-variant px-2 label-large text-on-surface" disabled={!editable} aria-haspopup="menu" aria-expanded={columnMenu}>
									<ColumnGlyph role={column?.role} size={18} />{column?.name ?? 'Column'}{#if editable}<Icon name="arrow_drop_down" size={18} />{/if}
								</button>
							{/snippet}
							{#each columns as c (c.id)}
								<MenuItem onclick={() => moveTo(c.id)}><span class="flex items-center gap-2"><ColumnGlyph role={c.role} size={18} />{c.name}</span></MenuItem>
							{/each}
						</Menu>
						{#if task.priority > 0}<span class="flex h-8 items-center gap-1 rounded-sm border border-outline-variant px-2 label-large text-on-surface-variant"><PriorityGlyph level={task.priority} />{priorityOptions[task.priority]?.label}</span>{/if}
						{#if task.assignees.length || task.group_assignees?.length}<span class="flex h-8 items-center gap-1 label-large text-on-surface-variant"><Icon name={task.group_assignees?.length ? 'groups' : 'person'} size={18} />{task.assignees.length + (task.group_assignees?.length ?? 0)}</span>{/if}
					</div>
				{/if}
			</div>
			{#if task.kind === 'command' && editable}
				<!-- On phones Run is in a bar at the bottom instead. -->
				<span class="hidden sm:contents">
					{#if activeJob?.service && activeJob.status !== 'approval'}
						<!-- A service: Stop asks it to quit gracefully (the panel below has Restart and Force quit). -->
						<Button variant="tonal" icon="stop_circle" disabled={!!activeJob.progress?.stopping || activeJob.status === 'queued'} onclick={() => api().jobs.stop(activeJob!.id).catch((err) => snackbar.error(err instanceof ApiError ? err.message : 'Could not stop it'))}>Stop</Button>
					{:else if activeJob}
						<Button variant="tonal" icon="stop_circle" onclick={() => cancel(activeJob!)}>Cancel</Button>
					{:else}
						<Button icon={task.shell ? 'keyboard' : 'play_arrow'} onclick={run} disabled={!command.trim() || libDirty}>{task.shell ? 'Open a shell' : task.latest_job?.status === 'failed' ? 'Re-run' : 'Run'}</Button>
					{/if}
				</span>
			{/if}
			<Menu>
				{#snippet anchor()}<IconButton icon="more_vert" label="Card menu" />{/snippet}
				{#if editable && !task.run && !task.playbook_step_id}
					<MenuItem icon={task.kind === 'command' ? 'person' : 'terminal'} onclick={() => save({ kind: task.kind === 'command' ? 'manual' : 'command' })}>
						Convert to {task.kind === 'command' ? 'human task' : 'command'}
					</MenuItem>
					{#if !task.command_ref && !isHttp}
						<MenuItem icon={task.shell ? 'terminal' : 'keyboard'} onclick={() => save({ shell: !task.shell })}>{task.shell ? 'Make it a command again' : 'Make it a shell'}</MenuItem>
					{/if}
					{#if task.command_ref}
						<MenuItem icon="link_off" onclick={detachFromLibrary}>Detach from library</MenuItem>
					{/if}
				{/if}
				{#if editable && !task.run && !task.playbook_step_id}
					<MenuItem icon="data_object" onclick={() => parseFrom({})}>Parse output…</MenuItem>
				{/if}
				{#if editable && !task.playbook_step_id}
					<MenuItem icon={task.alerts?.channels?.length ? 'notifications_active' : 'campaign'} onclick={() => (alertsOpen = true)}>{task.alerts?.channels?.length ? 'Chat alerts (on)…' : 'Chat alerts…'}</MenuItem>
					{#if task.run}
						<MenuItem icon="replay" onclick={runAgain}>Run again…</MenuItem>
					{:else}
						<MenuItem icon="content_copy" onclick={() => (duplicateOpen = true)}>Duplicate…</MenuItem>
					{/if}
				{/if}
				{#if editable && canMoveInto}
					<MenuItem icon="subdirectory_arrow_right" onclick={() => (intoOpen = true)}>Move into a card…</MenuItem>
				{/if}
				{#if editable && task.parent_id}
					<MenuItem icon="move_up" onclick={release}>Make it its own card</MenuItem>
				{/if}
				{#if editable && !task.playbook_step_id}<PluginActions projectId={task.project_id} place="card" taskId={task.id} as="menu" />{/if}
				<MenuItem icon="link" onclick={() => { navigator.clipboard.writeText(location.href); snackbar.show('Link copied'); }}>Copy link</MenuItem>
				{#if editable && !task.playbook_step_id}
					{#if task.archived_at}
						<MenuItem icon="unarchive" onclick={restore}>Restore to the board</MenuItem>
					{:else}
						<MenuItem icon="inventory_2" onclick={archive}>Archive</MenuItem>
					{/if}
				{/if}
				{#if editable}<MenuItem icon="delete" danger onclick={remove}>Delete card</MenuItem>{/if}
			</Menu>
			<IconButton icon="close" label="Close" onclick={() => (open = false)} />
		</div>
	{/snippet}

	{#if task.parent_id}
		<div class="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-surface-container-high px-3 py-2">
			<Icon name="checklist" size={20} class="shrink-0 text-on-surface-variant" />
			<span class="min-w-0 flex-1 body-medium text-on-surface">
				A subtask of <a class="text-primary underline-offset-2 hover:underline" href={parentHref}>“<Spoilers text={parentTitle ?? '…'} />”</a>: it's not on the board by itself.
			</span>
			{#if editable}<Button variant="text" icon="move_up" onclick={release}>Make it its own card</Button>{/if}
		</div>
	{/if}
	{#if task.archived_at}
		<div class="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-surface-container-high px-3 py-2">
			<Icon name="inventory_2" size={20} class="shrink-0 text-on-surface-variant" />
			<span class="min-w-0 flex-1 body-medium text-on-surface" title={absoluteTime(task.archived_at)}>
				Archived {relativeTime(task.archived_at)}: it's not on the board.
			</span>
			{#if editable}<Button variant="tonal" icon="unarchive" onclick={restore}>Restore</Button>{/if}
		</div>
	{/if}
	<!-- minmax(0, …) and min-w-0 let the main column shrink: wide output scrolls inside the
	     console instead of pushing the side column out of the dialog. -->
	<div class="grid min-h-full grid-cols-[minmax(0,1fr)] gap-6 pt-2 pb-6 lg:grid-cols-[minmax(0,1fr)_300px]" onpaste={onPaste}>
		<!-- main column -->
		<div class="flex min-h-0 min-w-0 flex-col gap-4">
			{#if activeJob?.service && activeJob.status !== 'approval'}
				<ServicePanel job={activeJob} {editable} />
			{/if}
			{#if activeJob?.status === 'approval'}
				<div class="flex flex-col gap-3 rounded-md bg-tertiary-container p-4 text-on-tertiary-container">
					<p class="flex items-center gap-2 title-small"><Icon name="front_hand" size={20} />This run waits for approval</p>
					<p class="body-medium">
						{#if activeJob.follows_job_id}
							Its {previewLabel.toLowerCase()} is ready: look at what it would do, then let the real run go.
						{:else}
							{libraryDef?.spec.confirm ? `“${libraryDef.name}” asks before each run` : 'This card asks before each run'}: it can change things outside Castor.
						{/if}
						Started by {startedBy} {relativeTime(activeJob.queued_at)}.
						{#if task.approvers?.length}Only {task.approvers.map((id) => `@${people.group(id)?.slug ?? 'a group'}`).join(', ')} (or an admin) can let it go.{/if}
					</p>
					<code class="block max-h-32 overflow-auto rounded-sm bg-surface-container-highest p-2 font-mono text-[12.5px] break-all whitespace-pre-wrap text-on-surface">{activeJob.command}</code>
					{#if activeJob.follows_job_id}
						<Button variant="outlined" icon="preview" class="w-fit" onclick={() => { selectedJobId = activeJob!.follows_job_id!; wantedView = 'results'; tab = 'output'; }}>Look at the {previewLabel.toLowerCase()}</Button>
					{/if}
					{#if editable}
						<div class="flex flex-wrap items-end gap-2">
							<TextField label="Why not (if you reject it)" bind:value={rejectReason} class="min-w-48 flex-1" />
							<Button variant="text" loading={deciding} onclick={() => decide(false)}>Reject</Button>
							<Button icon="check" loading={deciding} onclick={() => decide(true)} title={mineToApprove && appSettings.value?.approval_by_someone_else ? 'Someone other than you has to approve it' : undefined} disabled={mineToApprove && !!appSettings.value?.approval_by_someone_else}>Approve and run</Button>
						</div>
					{/if}
				</div>
			{/if}
			{#if stepRun && stepSpec && stepRun.step_status[stepSpec.id] === 'attention'}
				<StepDecision run={stepRun} step={stepSpec} result={stepResult} onresolved={(r) => (stepRun = r)} />
			{/if}
			<StepDecisions result={stepResult} />
			{#if task.kind === 'command' && !formOpen}
				<button type="button" class="state-layer flex w-full items-center gap-3 rounded-md bg-surface-container p-3 text-left" onclick={() => (formOpen = true)}>
					<Icon name={isHttp ? 'api' : isMcp ? 'hub' : isPluginAction ? 'extension' : task.command_ref ? 'menu_book' : 'terminal'} size={20} class="shrink-0 text-primary" />
					<span class="min-w-0 flex-1">
						<span class="block label-medium text-on-surface-variant">{task.command_ref ? (libraryDef?.name ?? task.command_ref) : 'Command'}</span>
						<code class="block truncate font-mono text-[12.5px] text-on-surface">{task.command || '—'}</code>
					</span>
					<span class="shrink-0 label-large text-primary">{editable ? 'Edit' : 'Show'}</span>
				</button>
			{:else if task.kind === 'command' && !editable}
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
						<p class="mb-1 label-medium text-on-surface-variant">{isHttp ? 'Request' : isMcp ? 'Tool call' : isPluginAction ? 'Plugin call' : 'Command'}</p>
						<pre class="overflow-auto rounded-sm bg-surface-container-highest p-3 font-mono text-[12.5px] whitespace-pre-wrap break-words text-on-surface">{task.command || '—'}</pre>
					</div>
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 body-small text-on-surface-variant">
						{#if isPluginAction}
							<dt>Runs in</dt><dd class="text-on-surface">the plugin {libraryDef?.spec.plugin?.id} (its action {libraryDef?.spec.plugin?.action})</dd>
							<dt>Rate limit</dt><dd class="text-on-surface">{task.rate_limit ?? (libraryDef?.spec.rate_limit ? `${libraryDef.spec.rate_limit} (the action’s)` : 'none of its own')}</dd>
						{:else if isMcp}
							<dt>Calls</dt><dd class="text-on-surface">{libraryDef?.spec.mcp?.tool} on the MCP server {libraryDef?.spec.mcp?.server}</dd>
							<dt>Rate limit</dt><dd class="text-on-surface">{task.rate_limit ?? (libraryDef?.spec.rate_limit ? `${libraryDef.spec.rate_limit} (the action’s)` : 'none of its own')}</dd>
						{:else if isHttp}
							<dt>Sent by</dt><dd class="text-on-surface">{describeSender(task.agent_selector, agents)}</dd>
							<dt>Rate limit</dt><dd class="text-on-surface">{task.rate_limit ?? (libraryDef?.spec.rate_limit ? `${libraryDef.spec.rate_limit} (the action’s)` : 'none of its own')}</dd>
						{:else}
						<dt>Runs in</dt><dd class="truncate font-mono text-on-surface">{task.tool ? `the ${task.tool} tool` : task.image || 'the agent’s shell'}{task.container_flags?.length ? ` ${task.container_flags.join(' ')}` : ''}</dd>
						<dt>Agent</dt><dd class="text-on-surface">{describeSelector(selectorFrom(agentId), agents)}</dd>
						{/if}
						{#if !isMcp && !isPluginAction}
							<dt>Route</dt><dd class="text-on-surface">{task.route === 'none' ? 'direct (no route)' : task.route ? 'its own network route (see its runs)' : 'the project’s default'}</dd>
						{/if}
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
							{#if libraryDef}<span class="shrink-0 rounded-full bg-surface-container-highest px-2 py-0.5 whitespace-nowrap label-small text-on-surface-variant">{originLabel(libraryDef.origin)}</span>{/if}
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

					{#if isHttp || isMcp || isPluginAction}
						<div class="grid gap-3 sm:grid-cols-2">
							{#if isPluginAction}
								<a href="/settings/plugins" class="flex h-14 items-center gap-2 rounded-xs border border-outline-variant px-4 hover:border-outline" title="The plugin runs it, in its sandbox">
									<Icon name="extension" size={20} class="text-primary" />
									<span class="min-w-0">
										<span class="block label-small text-on-surface-variant">Plugin</span>
										<span class="block truncate font-mono body-medium text-on-surface">{libraryDef?.spec.plugin?.id}</span>
									</span>
								</a>
							{:else if isMcp}
								<a href="/settings/mcp" class="flex h-14 items-center gap-2 rounded-xs border border-outline-variant px-4 hover:border-outline" title="The MCP server's settings decide where it runs">
									<Icon name="hub" size={20} class="text-primary" />
									<span class="min-w-0">
										<span class="block label-small text-on-surface-variant">MCP server</span>
										<span class="block truncate font-mono body-medium text-on-surface">{libraryDef?.spec.mcp?.server}</span>
									</span>
								</a>
							{:else}
							<Select
								label="Sent from"
								bind:value={agentId}
								options={senderOptions}
								supporting={agentHint || 'The server makes it; an agent only sends it from its network'}
								error={agentProblem}
								onchange={(v) => save({ agent_selector: selectorFrom(v) })}
							/>
							{/if}
							{#if isHttp}
								<RouteChoice projectId={task.project_id} bind:value={routeId} bind:route={cardRoute} taskId={task.id} recheck={task.updated_at} onchange={(v) => save({ route: v })} />
							{/if}
							<TextField
								label="Rate limit (optional)"
								bind:value={rateLimit}
								mono
								placeholder={libraryDef?.spec.rate_limit ? `the action’s: ${libraryDef.spec.rate_limit}` : '60/minute'}
								supporting="This card’s requests, on top of its variable sets’ limits"
								oninput={() => edited('rate_limit')}
								onblur={() => (rateLimit.trim() || null) !== (task.rate_limit ?? null) && commit('rate_limit', { rate_limit: rateLimit.trim() || null })}
							/>
						</div>
					{:else}
					{#if task.shell}
						<div class="flex items-start gap-3">
							<Icon name="keyboard" size={20} class="mt-0.5 shrink-0 text-primary" />
							<div class="min-w-0">
								<p class="title-small text-on-surface">A shell to work in</p>
								<p class="body-small text-on-surface-variant">
									Open it and you’re typing in a shell on the agent (bash where there is one), through the card’s route if it has one. It runs until you type
									<code class="font-mono">exit</code> or stop it; files you leave in <code class="font-mono">artifacts/</code> come back with the run, and what it printed can be parsed afterwards.
								</p>
							</div>
						</div>
					{:else}
					<TextField label="Command" bind:value={command} multiline rows={2} mono oninput={() => edited('command')} onblur={() => command !== task.command && commit('command', { command })} supporting={aiEdit ? `Changed by the AI: ${aiEdit.change ?? 'a corrected command'} (details at the top)` : ''} />
					{/if}
					<div class="grid gap-3 sm:grid-cols-2">
						{#if task.tool}
							<a href="/library?t={task.tool}" class="flex h-14 items-center gap-2 rounded-xs border border-outline-variant px-4 hover:border-outline" title="The command's tool decides where it runs; see the Library">
								<Icon name="handyman" size={20} class="text-primary" />
								<span class="min-w-0">
									<span class="block label-small text-on-surface-variant">Tool</span>
									<span class="block truncate font-mono body-medium text-on-surface">{task.tool}</span>
								</span>
							</a>
						{:else}
							<TextField label="Container image" bind:value={image} mono placeholder="run in agent shell" oninput={() => edited('image')} onblur={() => (image.trim() || null) !== task.image && commit('image', { image: image.trim() || null })} />
						{/if}
						<Select label="Agent" bind:value={agentId} options={agentOptions} supporting={agentHint} error={agentProblem}
							onchange={(v) => save({ agent_selector: selectorFrom(v) })} />
					</div>
					<RouteChoice projectId={task.project_id} bind:value={routeId} bind:route={cardRoute} taskId={task.id} recheck={`${task.updated_at}${task.image}${task.command}`}
						onchange={(v) => save({ route: v })} />
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
					{/if}
					<div class="flex flex-wrap items-center gap-4">
						<Switch bind:checked={runOnMove} label="Run when moved to In Progress" onchange={(v) => save({ run_on_move: v })} />
						{#if libraryDef?.spec.preview}
							<span title={`Each run starts with a ${previewLabel.toLowerCase()} (a dry run); a person approves the real run after looking at it`}>
								<Switch checked={task.preview ?? libraryDef.spec.preview.default ?? true} label={`${previewLabel} first`} onchange={(v) => save({ preview: v === (libraryDef?.spec.preview?.default ?? true) ? null : v })} />
							</span>
						{/if}
						<span title="A person approves each run before it starts (for runs that change things outside Castor)">
							<Switch checked={!!task.confirm || !!libraryDef?.spec.confirm} disabled={!!libraryDef?.spec.confirm} label={libraryDef?.spec.confirm ? 'Ask before each run (its definition asks)' : 'Ask before each run'} onchange={(v) => save({ confirm: v })} />
						</span>
						{#if !isHttp && !isMcp && !isPluginAction && !task.shell}
						<span title="A real terminal: you can type into it while it runs, full-screen programs (tmux, vim, htop) work, and a run that stops at a prompt tells people.">
							<Switch checked={!!task.tty || libTty} disabled={libTty} label={libTty ? 'Interactive terminal (its command asks for one)' : 'Interactive terminal'} onchange={(v) => save({ tty: v })} />
						</span>
						<span title="A listener, relay, capture or log tail: no timeout, its own slots on the agent, and Stop asks it to quit the way it's built to (its signal, then a wait for it to free its ports).">
							<Switch checked={!!service} disabled={!!libService} label={libService ? 'Runs until stopped (its command is a service)' : 'Runs until stopped'} onchange={(v) => save({ service: v ? { stop_signal: 'TERM', stop_timeout: 10 } : null })} />
						</span>
						{/if}
						{#if !service && !task.shell}
						<TextField label="Timeout (s)" bind:value={timeout} type="number" class="w-40 [&_div]:h-10" oninput={() => edited('timeout')} onblur={() => (timeout ? Number(timeout) : null) !== task.timeout_s && commit('timeout', { timeout_s: timeout ? Number(timeout) : null })} />
						{/if}
					</div>
					{#if service && !libService && task.service}
						{@const own = task.service}
						<div class="flex flex-wrap items-center gap-3">
							<Select label="Stop signal" value={own.stop_signal} class="w-40" options={SIGNALS.map((s) => ({ value: s, label: `SIG${s}` }))} onchange={(v) => save({ service: { ...own, stop_signal: v as typeof own.stop_signal } })} />
							<TextField label="Time to exit (s)" type="number" value={String(own.stop_timeout)} class="w-40 [&_div]:h-10" onblur={(e: FocusEvent) => { const n = Math.max(1, Math.min(300, Number((e.currentTarget as HTMLInputElement).value) || 10)); if (n !== own.stop_timeout) save({ service: { ...own, stop_timeout: n } }); }} supporting="Before it's killed" />
						</div>
					{/if}
				</div>
			{/if}

			<div>
				<div class="mb-1 flex items-center justify-between">
					<h3 class="title-small text-on-surface">Description</h3>
					{#if editable && !editingDescription}<Button variant="text" icon="edit" onclick={() => { description = task.description; editingDescription = true; }}>Edit</Button>{/if}
				</div>
				{#if editingDescription}
					<MarkdownEditor
						bind:value={description}
						label="Description"
						placeholder="What this card is for, what to check, links…"
						projectId={task.project_id}
						mentionables={() => people.mentionables(task.project_id)}
						{images}
						submitLabel="Save"
						submitIcon="check"
						onsubmit={saveDescription}
						oncancel={() => { description = task.description; editingDescription = false; }}
						autofocus
						minRows={6}
					/>
				{:else if task.description.trim()}
					<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
					<div class="prose-m3 body-medium text-on-surface" use:copyableCode onclick={openImagesIn}>{@html renderMarkdown(task.description)}</div>
				{:else}
					<p class="body-medium text-on-surface-variant italic">No description.</p>
				{/if}
			</div>

			{#if task.kind === 'manual' && !task.run && !task.playbook_step_id}
				<AiSummary taskId={task.id} finished projectId={task.project_id} {editable} />
			{/if}

			<Tabs {tabs} bind:value={tab} />

			{#if tab === 'run' && task.run}
				<RunPanel runId={task.run.run_id} {editable} onopenstep={openStep} />
			{:else if tab === 'subtasks' && canHaveSubtasks}
				<SubtaskPanel {task} {columns} {editable} onopen={openStep} />
			{:else if tab === 'output'}
				<div class="flex min-h-[320px] flex-1 flex-col gap-2">
					{#if jobs.length > 1}
						<div class="flex flex-wrap items-center gap-1">
							{#each jobs as j (j.id)}
								<span class="inline-flex" in:pop={{ enabled: liveSide }} animate:glide={{ enabled: liveSide }}><Chip variant="filter" selected={selectedJobId === j.id} onclick={() => (selectedJobId = j.id)}>
									{j.purpose === 'preview' ? `${previewLabel} · ` : j.purpose === 'retry' ? 'Retry · ' : ''}{j.batch_id ? `${agentName(j.target_agent_id ?? j.agent_id)} · ` : ''}{relativeTime(j.queued_at)} · {j.status}{j.started_at ? ` · ${duration(j.started_at, j.finished_at)}` : ''}
								</Chip></span>
							{/each}
							{#if selectedJob?.batch_id}
								<a href="/projects/{task.project_id}/assets?tab=reachability&batch={selectedJob.batch_id}" class="ml-1 inline-flex items-center gap-1 rounded-full px-2 py-1 label-large text-primary hover:underline" title="This fan-out's results side by side, by each agent's network segment">
									<Icon name="lan" size={16} />Compare by segment
								</a>
							{/if}
						</div>
					{/if}
					{#if selectedJob}
						<p class="flex flex-wrap items-center gap-x-2 body-small text-on-surface-variant">
							{#if selectedJob.runner === 'manual'}
								<span class="inline-flex items-center gap-1"><Icon name="data_object" size={14} />Output {ORIGIN_WORDS[selectedJob.origin?.from ?? 'paste']}{selectedJob.origin?.edited ? ' (part of it)' : ''}, brought in {absoluteTime(selectedJob.queued_at)}</span>
							{:else if selectedJob.kind === 'plugin'}
								Plugin action run by the Castor server · queued {absoluteTime(selectedJob.queued_at)}
							{:else if selectedJob.kind === 'mcp'}
								MCP tool call from the Castor server · queued {absoluteTime(selectedJob.queued_at)}
							{:else if selectedJob.kind === 'http'}
								HTTP request from the Castor server{selectedJob.via_agent_id ? `, through ${agentName(selectedJob.via_agent_id)}` : ''} · queued {absoluteTime(selectedJob.queued_at)}{selectedJob.status === 'waiting' && selectedJob.resume_at ? ` · carries on at ${absoluteTime(selectedJob.resume_at)}` : ''}
								{#if selectedJob.progress?.ai_moves?.length}
									<span class="inline-flex items-center gap-1 text-tertiary" title={selectedJob.progress.ai_moves.map((mv) => `${mv.move.replace('_', ' ')}: ${mv.reason}`).join('\n')}>
										<Icon name="auto_awesome" size={14} />The AI stepped in {selectedJob.progress.ai_moves.length === 1 ? 'once' : `${selectedJob.progress.ai_moves.length} times`}
									</span>
								{/if}
								{#if selectedJob.progress?.skipped?.length}
									<span title={selectedJob.progress.skipped.map(String).join(', ')}>· {selectedJob.progress.skipped.length} value{selectedJob.progress.skipped.length === 1 ? '' : 's'} skipped</span>
								{/if}
							{:else}
							Agent {agentName(selectedJob.agent_id)}{selectedJob.image ? ` · ${selectedJob.image}` : ' · host shell'} · queued {absoluteTime(selectedJob.queued_at)}
							{/if}
							{#if selectedJob.route_info}
								{@const ri = selectedJob.route_info}
								<a href={`/routes?r=${ri.id}`} class="inline-flex items-center gap-1 rounded-sm bg-secondary-container px-1.5 text-on-secondary-container hover:underline"
									title={`${ri.label}${ri.method ? `\nBy ${METHOD_WORDS[ri.method as RouteMethod]?.label ?? ri.method}` : ''}${ri.dns === 'local' ? '' : '\nNames resolved at the far end'}`}>
									<Icon name="alt_route" size={14} />through {ri.name}{ri.method ? ` · ${METHOD_WORDS[ri.method as RouteMethod]?.label ?? ri.method}` : ''}
								</a>
							{/if}
							{#if selectedJob.purpose === 'preview'}
								<span class="inline-flex items-center gap-1 rounded-sm bg-tertiary-container px-1.5 text-on-tertiary-container" title="A dry run: what the real run would do, for a person to look at before approving it">
									<Icon name="preview" size={14} />{previewLabel}
								</span>
							{:else if selectedJob.purpose === 'retry'}
								<span class="inline-flex items-center gap-1 rounded-sm bg-secondary-container px-1.5 text-on-secondary-container">
									<Icon name="replay" size={14} />Retry of what failed{selectedJob.args_override ? `: ${Object.values(selectedJob.args_override).flat().join(', ')}` : ''}
								</span>
							{:else if selectedJob.follows_job_id && jobs.some((j) => j.id === selectedJob!.follows_job_id)}
								<button type="button" class="inline-flex items-center gap-1 text-primary hover:underline" onclick={() => (selectedJobId = selectedJob!.follows_job_id!)}>
									<Icon name="preview" size={14} />After its {previewLabel.toLowerCase()}
								</button>
							{/if}
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
						{#if retryPlan && retryPlan.jobId === selectedJob.id && retryPlan.values.length && editable && !activeJob}
							<div class="flex flex-wrap items-center gap-3 rounded-md bg-surface-container px-4 py-3">
								<Icon name="replay" size={20} class="text-primary" />
								<span class="min-w-0 flex-1 body-medium text-on-surface">
									{retryPlan.values.length === 1 ? '1 thing' : `${retryPlan.values.length} things`} failed in this run:
									<span class="font-mono text-[13px]">{retryPlan.values.slice(0, 8).join(', ')}{retryPlan.values.length > 8 ? ` and ${retryPlan.values.length - 8} more` : ''}</span>
									<span class="block body-small text-on-surface-variant">Retrying runs it again with {retryPlan.arg} set to only these; the card keeps its own arguments.</span>
								</span>
								<Button variant="tonal" icon="replay" loading={retrying} onclick={retryFailed}>Retry what failed</Button>
							</div>
						{/if}
						{#if selectedJob.inputs && Object.keys(selectedJob.inputs).length}
							<!-- What the run was given (an Ansible inventory, a playbook), as it was sent. -->
							<details class="group rounded-md border border-outline-variant/70">
								<summary class="state-layer flex cursor-pointer list-none items-center gap-2 rounded-md px-3 py-2 label-large text-on-surface">
									<Icon name="description" size={18} class="text-on-surface-variant" />Input files
									<span class="body-small text-on-surface-variant">{Object.values(selectedJob.inputs).map((i) => i.file).join(', ')}</span>
									<Icon name="expand_more" size={18} class="ml-auto text-on-surface-variant transition-transform group-open:rotate-180" />
								</summary>
								<div class="flex flex-col gap-3 px-3 pb-3">
									{#each Object.entries(selectedJob.inputs) as [name, input] (name)}
										<div class="flex flex-col gap-1">
											<span class="font-mono label-medium text-on-surface-variant">{input.file}</span>
											<pre class="max-h-64 overflow-auto rounded-sm bg-surface-container px-3 py-2 font-mono text-[12px] text-on-surface">{input.content || '(empty)'}</pre>
										</div>
									{/each}
								</div>
							</details>
						{/if}
						<AiSummary jobId={selectedJob.id} finished={['succeeded', 'failed', 'lost'].includes(selectedJob.status)} projectId={task.project_id} {editable} />
						{#if hasResults}
							<div class="flex h-9 w-fit shrink-0 overflow-hidden rounded-full border border-outline" role="group" aria-label="Run view">
								{#each [{ id: 'console', label: 'Console', icon: 'terminal' }, { id: 'results', label: resultCount ? `Results · ${resultCount}` : 'Results', icon: 'table_view' }, { id: 'changes', label: changeCount ? `Changes · ${changeCount}` : 'Changes', icon: 'difference' }] as v, i (v.id)}
									<button
										type="button"
										aria-pressed={outputView === v.id}
										class="state-layer focus-ring inline-flex items-center gap-1.5 px-3 label-large sm:px-4 transition-colors {i ? 'border-l border-outline' : ''} {outputView === v.id
											? 'bg-secondary-container text-on-secondary-container'
											: 'text-on-surface'}"
										onclick={() => (outputView = v.id as OutputView)}
									>
										<Icon name={outputView === v.id ? 'check' : v.icon} size={18} />{v.label}
									</button>
								{/each}
							</div>
						{/if}
						{#key selectedJob.id}
							{#if hasResults && outputView === 'results'}
								<JobResults job={selectedJob} onload={(o) => (resultCount = o.filter((x) => !x.error).length || null)} />
							{:else if hasResults && outputView === 'changes'}
								<JobChanges job={selectedJob} onload={(c) => (changeCount = c.base ? c.totals.added + c.totals.removed + c.totals.changed || null : null)} />
							{:else}
								<JobConsole job={selectedJob} oncancel={editable ? cancel : undefined} title={task.title} canInteract={editable} onsnapshot={(range) => selectedJob && (snapshot = { jobId: selectedJob.id, range, attachment: null })}
									autoControl={controlOnStart === selectedJob.id}
									onparse={editable && selectedJob.runner !== 'manual' ? (selection) => selectedJob && parseFrom(selection ? { job_id: selectedJob.id, text: selection } : { job_id: selectedJob.id }) : undefined} />
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
						<MarkdownEditor
							bind:value={newComment}
							label="Add a comment"
							projectId={task.project_id}
							mentionables={() => people.mentionables(task.project_id)}
							{images}
							submitLabel="Comment"
							onsubmit={postComment}
							busy={posting}
							draftKey="comment:{task.id}"
						/>
					</div>{/if}
					{#each newestFirst as c (c.id)}
						{@const authorName = people.user(task.project_id, c.author_id ?? '')?.username}
						<div class="flex gap-3" id="comment-{c.id}" in:enter={{ enabled: liveSide }} out:leave={{ enabled: liveSide }}>
							{#if authorName}
								<a href="/people/{authorName}" class="shrink-0 self-start rounded-full" aria-label="{c.author_name}'s page"><Avatar name={c.author_name} src={c.author_avatar} tone="secondary" /></a>
							{:else}
								<Avatar name={c.author_name} src={c.author_avatar} tone="secondary" />
							{/if}
							<div class="reacts-host min-w-0 flex-1 rounded-md bg-surface-container px-4 py-3" class:mentions-me={mentionsMe(c)} class:flash={flashing === c.id}>
								<div class="flex items-center gap-2 body-small text-on-surface-variant">
									{#if authorName}<a href="/people/{authorName}" class="label-large text-on-surface hover:underline"><PersonName name={c.author_name} /></a>{:else}<span class="label-large text-on-surface"><PersonName name={c.author_name} /></span>{/if}
									<span title={absoluteTime(c.created_at)}>{relativeTime(c.created_at)}</span>
									{#if mentionsMe(c)}<span class="flex items-center gap-0.5 rounded-full bg-primary-container px-1.5 py-0.5 label-small text-on-primary-container"><Icon name="alternate_email" size={12} />Mentions you</span>{/if}
									{#if c.via === 'mcp'}<span title="Written through an AI assistant connected over MCP">· via MCP</span>{/if}
									{#if c.edited_at}
										<span class="italic" title="Edited {absoluteTime(c.edited_at)}">· edited</span>
									{/if}
									<span class="flex-1"></span>
									{#if canEdit(c) && editingId !== c.id}
										<IconButton icon="edit" label="Edit comment" size="sm" onclick={() => startEdit(c)} />
									{/if}
									{#if editable && editingId !== c.id}
										<IconButton icon="data_object" label="Parse the output in this comment" size="sm" onclick={() => parseFrom({ comment_id: c.id })} />
									{/if}
									{#if canEdit(c) && editingId !== c.id}
										<IconButton icon="drive_file_move" label="Move comment to another card or the timeline" size="sm" onclick={() => (movingComment = c)} />
									{/if}
									{#if canEdit(c)}<IconButton icon="delete" label="Delete comment" size="sm" onclick={() => removeComment(c)} />{/if}
								</div>
								{#if editingId === c.id}
									<div class="mt-2">
										<MarkdownEditor
											bind:value={editDraft}
											label="Edit comment"
											projectId={task.project_id}
											mentionables={() => people.mentionables(task.project_id)}
											{images}
											submitLabel="Save"
											submitIcon="check"
											onsubmit={() => saveEdit(c)}
											oncancel={cancelEdit}
											busy={savingEdit}
											autofocus
											minRows={4}
										/>
									</div>
								{:else}
									<RichText text={c.body} projectId={task.project_id} class="mt-1 body-medium text-on-surface" />
									<Reactions target={{ comment: c.id }} reactions={c.reactions ?? []} {editable}
										onchange={(r) => (comments = comments.map((x) => (x.id === c.id ? { ...x, reactions: r } : x)))} />
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
							<Button variant="text" icon="photo_camera" onclick={() => (snapshot = { jobId: null, range: null, attachment: null })}>New snapshot</Button>
							<span class="body-small text-on-surface-variant">or paste a screenshot anywhere in this card</span>
							<input bind:this={fileInput} type="file" multiple class="hidden" onchange={(e) => upload((e.currentTarget as HTMLInputElement).files)} />
						</div>
					{/if}
					{#if attachments.length && !task.playbook_step_id}
						<a class="-mt-2 inline-flex items-center gap-1 self-start body-small text-primary hover:underline" href="/projects/{task.project_id}/files?card={task.id}">
							<Icon name="folder_open" size={16} />In the project’s Files, with every other file
						</a>
					{/if}
					{#if screenshots.length}
						<div class="grid grid-cols-2 gap-3 md:grid-cols-3">
							{#each screenshots as a, i (a.id)}
								<figure class="group relative overflow-hidden rounded-md bg-surface-container" in:pop={{ enabled: liveSide }} out:pop={{ enabled: liveSide }} animate:glide={{ enabled: liveSide }}>
									<button type="button" class="block w-full cursor-zoom-in" aria-label="View {a.filename}" onclick={() => lightbox.open(screenshots.map((s) => ({ src: imageUrl(s), name: s.filename })), i)}>
										<img src={imageUrl(a)} alt={a.filename} class="aspect-video w-full object-cover" loading="lazy" />
									</button>
									<figcaption class="flex items-center gap-1 px-2 py-1 body-small text-on-surface-variant">
										<span class="flex-1 truncate" title={a.filename}>{a.filename}</span>
										<span>{bytes(a.size)}</span>
										{#if a.snapshot}<IconButton icon="photo_camera" label={editable ? 'Change the snapshot' : 'Open the snapshot'} size="sm" onclick={() => (snapshot = { jobId: a.job_id, range: null, attachment: a })} />{/if}
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
							<span class="body-small text-on-surface-variant">· {h.actor_name}{#if h.payload?.via === 'mcp'}{' '}<span title="Done through an AI assistant connected over MCP">via MCP</span>{/if}</span>
						</li>
					{:else}
						<li class="body-medium text-on-surface-variant">No history.</li>
					{/each}
				</ol>
			{:else if openPanel}
				{#key openPanel.url}
					<PluginFrame panel={openPanel} projectId={task.project_id} task={{ id: task.id, title: task.title }} />
				{/key}
				<p class="mt-2 body-small text-on-surface-variant">From the {openPanel.plugin.name} plugin.</p>
			{/if}
		</div>

		<!-- side column -->
		<aside class="flex flex-col gap-4">
			{#if task.playbook_step_id}
				<!-- A step lives in its run's card, not in a column. -->
				<p class="flex items-center gap-2 rounded-md bg-surface-container px-3 py-2 body-small text-on-surface-variant"><Icon name="account_tree" size={18} />A step of a playbook run</p>
			{:else}
				{#key `${task.column_id}:${columnPick}`}
					<Select label="Column" value={task.column_id} disabled={!editable} options={columns.map((c) => ({ value: c.id, label: c.name }))} onchange={moveTo}>
						{#snippet leading(id)}<ColumnGlyph role={columns.find((c) => c.id === id)?.role} />{/snippet}
					</Select>
				{/key}
			{/if}
			<Select label="Priority" bind:value={priority} disabled={!editable} options={priorityOptions} onchange={(v) => save({ priority: Number(v) })}>
				{#snippet leading(level)}<PriorityGlyph {level} />{/snippet}
			</Select>
			<AssigneePicker projectId={task.project_id} value={task.assignees} groups={task.group_assignees ?? []} {editable} onchange={(ids) => save({ assignees: ids })} ongroupschange={(ids) => save({ group_assignees: ids })} />
			{#if !task.playbook_step_id}
				<DueDateField value={task.due_at} {editable} finished={column?.role === 'done' || column?.role === 'reported'} onchange={(iso) => void save({ due_at: iso })} />
				<CardDependencies {task} {editable} onchange={async (ids) => { onupdated(await api().tasks.update(task.id, { waits_on: ids })); }} onopen={openCard} />
			{/if}
			{#if asks && !task.playbook_step_id}
				<GroupPicker
					label="Who approves its runs"
					value={task.approvers ?? []}
					{editable}
					empty="The project's approvers (anyone on it, if it has none)"
					supporting={task.approvers?.length ? 'Only people in these groups, and admins, can approve or reject its runs.' : ''}
					onchange={(ids) => void save({ approvers: ids })}
				/>
			{/if}
			{#if !task.playbook_step_id}
				<div class="flex flex-col gap-1.5">
					<p class="label-large text-on-surface-variant">When it finishes</p>
					{#if editable && myId}
						<Switch checked={notifyMe} onchange={toggleNotifyMe} label="Notify me" />
					{/if}
					{#if othersNotified}<p class="body-small text-on-surface-variant">{othersNotified} other{othersNotified === 1 ? '' : 's'} will be notified</p>{/if}
					{#if editable || task.alerts?.groups?.length}
						<GroupPicker label="Groups told" value={task.alerts?.groups ?? []} {editable} empty="No groups" onchange={setNotifiedGroups} />
					{/if}
					<p class="flex items-center gap-1.5 body-small text-on-surface-variant">
						<Icon name={task.alerts?.channels?.length ? 'notifications_active' : 'campaign'} size={16} />
						{task.alerts?.channels?.length ? `Chat alerts: ${task.alerts.channels.length} channel${task.alerts.channels.length === 1 ? '' : 's'}` : 'No chat alerts'}
						{#if editable}<button type="button" class="text-primary hover:underline" onclick={() => (alertsOpen = true)}>{task.alerts?.channels?.length ? 'Change' : 'Add'}</button>{/if}
					</p>
				</div>
			{/if}
			<TagInput label="Tags" bind:value={tags} suggestions={tagSuggestions} noun="cards" readonly={!editable} placeholder={editable ? 'Add a tag' : ''} onchange={(next) => void save({ tags: next })} />
			<div class="rounded-md bg-surface-container p-4 body-small text-on-surface-variant">
				<p class="mb-2 title-small text-on-surface">Details</p>
				<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
					<dt>Kind</dt><dd class="text-on-surface">{task.run ? 'playbook run' : task.playbook_step_id ? 'playbook step' : task.kind}</dd>
					{#if task.latest_job}
						<dt>Last run</dt><dd class="text-on-surface">{task.latest_job.status}{task.latest_job.started_at ? ` · ${duration(task.latest_job.started_at, task.latest_job.finished_at)}` : ''}</dd>
						<dt>{task.latest_job.kind && task.latest_job.kind !== 'command' ? 'Sent by' : 'Agent'}</dt><dd class="text-on-surface">{task.latest_job.kind && task.latest_job.kind !== 'command' ? `Castor server${task.latest_job.via_agent_id ? ` via ${agentName(task.latest_job.via_agent_id)}` : ''}` : agentName(task.latest_job.agent_id)}</dd>
						{#if task.latest_job.route_info}<dt>Route</dt><dd class="truncate text-on-surface" title={task.latest_job.route_info.label}>{task.latest_job.route_info.name}</dd>{/if}
					{/if}
					<dt>ID</dt><dd class="truncate font-mono text-on-surface" title={task.id}>{task.id.slice(0, 8)}</dd>
				</dl>
			</div>
		</aside>
	</div>
	{#if task.kind === 'command' && editable}
		<!-- Phones: Run within thumb's reach, always in view. -->
		<div class="sticky bottom-0 -mx-4 flex gap-2 border-t border-outline-variant bg-surface-container-high px-4 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] sm:hidden">
			{#if activeJob}
				<Button variant="tonal" icon="stop_circle" class="flex-1" onclick={() => cancel(activeJob!)}>Cancel run</Button>
			{:else}
				<Button icon={task.shell ? 'keyboard' : 'play_arrow'} class="flex-1" onclick={run} disabled={!command.trim() || libDirty}>{task.shell ? 'Open a shell' : task.latest_job?.status === 'failed' ? 'Re-run' : 'Run'}</Button>
			{/if}
		</div>
	{/if}
</Dialog>

{#if intoOpen}
	<MoveWritingDialog bind:open={intoOpen} projectId={task.project_id} what="card" fromTaskId={task.id} onpick={moveInto} />
{/if}
{#if parseOpen}
	<ParseOutputDialog bind:open={parseOpen} projectId={task.project_id} source={parseSource ?? {}} taskId={task.id} onparsed={parsed} />
{/if}

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
{#if snapshot}
	{#key snapshot}
		<SnapshotDialog
			bind:open={() => !!snapshot, (v) => !v && (snapshot = null)}
			jobId={snapshot.jobId}
			taskId={task.id}
			title={task.title}
			range={snapshot.range}
			attachment={snapshot.attachment}
			canAttach={editable}
			onsaved={(a) => {
				savedSnapshot(a);
				tab = 'attachments';
			}}
		/>
	{/key}
{/if}
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

{#if movingComment}
	<MoveWritingDialog
		projectId={task.project_id}
		what="comment"
		fromTaskId={task.id}
		timeline
		onpick={moveComment}
		bind:open={() => !!movingComment, (v) => !v && (movingComment = null)}
	/>
{/if}
