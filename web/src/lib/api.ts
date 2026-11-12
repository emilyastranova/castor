import type { TagSuggestion } from './tags';

/** Typed client for the Castor API. Works in the browser and in SvelteKit load functions. */

export type UUID = string;

export type UserRole = 'viewer' | 'operator' | 'admin';

export interface User {
	id: UUID;
	username: string;
	display_name: string;
	/** Set by admins (or verified by a sign-on provider); single sign-on links accounts by it. */
	email?: string;
	/** viewer: read-only; operator: works in their projects; admin: everything. */
	role: UserRole;
	is_admin: boolean;
	disabled_at: string | null;
	created_at: string;
	/** What to show as their picture (null: initials). */
	avatar_url: string | null;
	avatar_kind: AvatarKind;
	avatar_link: string;
	gravatar_email: string;
	/** An uploaded picture exists (so it can be switched back to). */
	has_upload: boolean;
	/** Their theme colour (#rrggbb), or '' for the default palette. */
	theme_seed: string;
	/** Their theme: "" for Castor's own, null (never picked) for the instance default
	 *  (lib/themes.svelte.ts `effective`). */
	theme: string | null;
	/** The Companion on their timelines (off by default): their own picture (null: the built-in
	 *  one) and its seat line, 0–1 from the top. */
	companion?: boolean;
	companion_url?: string | null;
	companion_seat?: number;
}

export type AvatarKind = 'initials' | 'upload' | 'url' | 'gravatar';

/** A user as everyone sees them (pickers, member lists). */
export interface UserBrief {
	id: UUID;
	username: string;
	display_name: string;
	role: UserRole;
	avatar_url: string | null;
}

export interface Client {
	id: UUID;
	name: string;
	notes: string;
	created_at: string;
}

export interface Project {
	id: UUID;
	codename: string;
	name: string;
	kind: string;
	status: string;
	client_id: UUID | null;
	description: string;
	start_date: string | null;
	end_date: string | null;
	settings: Record<string, unknown>;
	created_at: string;
	updated_at: string;
}

export interface ProjectSummary extends Project {
	client_name: string | null;
	task_count: number;
	done_count: number;
	running_count: number;
}

export type ColumnRole = 'backlog' | 'todo' | 'active' | 'review' | 'done' | 'reported';

export interface Column {
	id: UUID;
	project_id: UUID;
	name: string;
	position: number;
	role: ColumnRole | null;
	wip_limit: number | null;
}

export type TaskKind = 'manual' | 'command';
export type JobStatus =
	| 'queued'
	| 'dispatched'
	| 'running'
	| 'succeeded'
	| 'failed'
	| 'cancelled'
	| 'lost';

export interface Job {
	id: UUID;
	task_id: UUID;
	agent_id: UUID | null;
	/** Shared by the jobs of one run on several agents; each goes to its target agent. */
	batch_id?: UUID | null;
	target_agent_id?: UUID | null;
	command: string;
	image: string | null;
	tool?: string | null;
	container_flags?: string[];
	status: JobStatus;
	exit_code: number | null;
	error: string | null;
	queued_at: string;
	dispatched_at: string | null;
	started_at: string | null;
	finished_at: string | null;
	stdout_bytes: number;
	stderr_bytes: number;
	/** Parsed outputs the command definition declared at dispatch (null for free-form commands). */
	output_specs: OutputSpec[] | null;
	/** Runs in an interactive terminal: raw terminal output; people can type into it. */
	tty?: boolean;
	/** The terminal's size as the person in control set it. */
	tty_size?: { cols: number; rows: number } | null;
	/** The prompt an automated run is waiting at (until someone types). */
	waiting_input?: string | null;
	/** Who took the terminal over. */
	input_by?: UUID | null;
}

export interface LogChunk {
	seq: number;
	stream: 'stdout' | 'stderr' | 'system';
	data: string;
	ts: string;
}

export interface HostResult {
	ip: string;
	hostnames: string[];
	status: string;
}

export interface ServiceResult {
	host: string;
	port: number;
	proto: string;
	state: string;
	name: string;
	product: string;
	version: string;
}

export interface UrlResult {
	url: string;
	status: number | null;
	length: number | null;
	words: number | null;
	lines: number | null;
}

export interface SmbHostResult {
	host: string;
	port: number;
	name: string;
	domain: string;
	os: string;
	signing: boolean;
	smbv1: boolean;
}

export interface PlaybookInputSpec {
	name: string;
	type: 'string' | 'number' | 'boolean' | 'list' | 'varset';
	/** varset inputs: the variable kind to pick a set of. */
	kind?: string | null;
	description: string;
	required: boolean;
	default: unknown;
	example: unknown;
	choices?: string[] | null;
}

/** A server-side step between commands: reshapes upstream outputs (no card, no agent). */
export interface TransformSpec {
	/** `{{ steps.<id>.outputs.<name> }}` reference(s); several are concatenated. */
	input: string | string[];
	/** Each op is a one-key mapping (`{ filter: {...} }`, `{ pluck: 'ip' }`) or a bare name. */
	ops: (Record<string, unknown> | string)[];
	output: { name: string; type: string | null };
}

export interface PlaybookStepSpec {
	id: string;
	name: string;
	kind: 'command' | 'transform';
	command_ref: string | null;
	transform: TransformSpec | null;
	description: string;
	/** Includes the steps referenced in args / transform input (added by the server). */
	depends_on: string[];
	args: Record<string, unknown>;
	skip_if_empty: boolean;
	/** Command variable slot → `{{ inputs.x }}` or a variable set name. */
	vars?: Record<string, string>;
	/** Run only when this holds (see PlaybookCondition); otherwise the step is not_taken. */
	when?: PlaybookCondition | null;
	join?: 'all' | 'any' | 'always';
	on_failure?: 'stop' | 'continue' | 'ask' | 'ai';
	/** on_failure: ai — what the AI resolver may do on its own. */
	ai?: { allow?: ResolveAction[]; edit_command?: boolean; draft_parsers?: boolean; confidence?: number; max_retries?: number; instructions?: string } | null;
	expect?: { outputs?: Record<string, Record<string, unknown>>; log?: Record<string, unknown> } | null;
	/** One card and job per item; args use {{ item }} / {{ item.field }}. */
	for_each?: { items: string; max_items?: number; concurrency?: number | null } | null;
}

/** A `when:` condition as written in the YAML. */
export type PlaybookCondition =
	| PlaybookCondition[]
	| { all: PlaybookCondition[] }
	| { any: PlaybookCondition[] }
	| { not: PlaybookCondition }
	| { step: string; status: string | string[] }
	| ({ value: unknown } & Record<string, unknown>);

export type PlaybookStepStatus =
	| 'pending'
	| 'queued'
	| 'running'
	| 'succeeded'
	| 'failed'
	| 'cancelled'
	| 'skipped'
	| 'not_taken'
	| 'attention'
	| 'blocked';

/** One test of a condition or an expectation, with what was found. */
export interface ConditionCheck {
	text: string;
	met: boolean;
	actual?: string;
}

export type ResolveAction = 'retry' | 'continue' | 'skip' | 'fail';

/** What the run stores per step: transform results, steps that never got a job, how a
 *  condition was evaluated, expectations, and decisions on paused steps. */
/** A decision on a paused step: by a person, or the AI (with its model, confidence and any
 *  command change). */
export interface StepResolution {
	action: ResolveAction;
	by: string;
	note: string;
	reason: string;
	at: string;
	ai?: boolean;
	/** A person applied the AI's suggestion (by is the person). */
	suggested?: boolean;
	model?: string;
	confidence?: number | null;
	command_before?: string;
	command_after?: string;
	/** The change in words, e.g. "replaced `--fast-mode` with `--quick`". */
	change?: string;
}

export interface PlaybookStepResult {
	status?: 'succeeded' | 'failed' | 'skipped' | 'not_taken' | 'attention';
	outputs?: Record<string, unknown>;
	type?: string;
	count?: number;
	input_count?: number;
	error?: string;
	reason?: string;
	/** Paused because this output wasn't in the format its parser reads. */
	parse?: { output: string; parser: string };
	finished_at?: string;
	/** The job this result judges (expect, or a decision); a newer job supersedes it. */
	job_id?: string;
	condition?: { met: boolean; checks: ConditionCheck[] };
	expect?: ConditionCheck[];
	resolution?: StepResolution;
	/** Every decision on the step in this run, oldest first. */
	history?: StepResolution[];
	/** The AI resolver's suggestion while the step waits (and why it didn't act, if it didn't). */
	ai?: AiSuggestion & { note?: string };
	/** for_each steps: how many item cards were made. */
	items?: number;
}

export type FeedKind = 'comment' | 'job' | 'card' | 'move' | 'board' | 'file' | 'playbook' | 'library' | 'note' | 'asset' | 'plugin' | 'agent' | 'admin';

export interface ProjectRef {
	id: UUID;
	codename: string;
	name: string;
}

/** One timeline entry on the dashboard (an activity event with what it's about). */
export interface FeedItem {
	id: number;
	kind: FeedKind;
	type: string;
	created_at: string;
	message: string;
	project: ProjectRef | null;
	task: { id: UUID; name: string } | null;
	actor: { type: 'user' | 'agent' | 'system'; name: string; avatar_url: string | null; username?: string | null };
	comment: { id: UUID; body: string; edited: boolean; mentions: string[] } | null;
	job: { id: UUID; status: string; exit_code: number | null; seconds: number | null; agent: string | null; command: string } | null;
	run_id: string | null;
	step_id: string | null;
	/** 'mcp': done through an AI assistant. */
	via?: string | null;
	/** Where a library item lives (library events). */
	link?: string | null;
	/** A post written straight into the timeline (type post.created), as it is now. */
	post?: { id: UUID; body: string; edited: boolean; mentions: string[]; tags: string[]; author_id: UUID | null } | null;
	/** An AI summary of a run (type summary.written), as it is now. */
	summary?: { id: UUID; text: string; model: string } | null;
}

/** What the AI wrote about a finished job or playbook run (server summaries.py). */
export interface AiSummary {
	id: UUID;
	job_id: UUID | null;
	run_id: UUID | null;
	task_id: UUID | null;
	status: 'writing' | 'done' | 'failed';
	text: string;
	model: string;
	error: string;
	/** Written by itself (the project has summaries on), not asked for. */
	automatic: boolean;
	updated_at: string | null;
}

/** What a moved comment or post became. */
export interface MoveResult {
	kind: 'comment' | 'post';
	comment?: Comment | null;
	post?: ProjectPost | null;
}

/** A post on a project's timeline: a comment on nothing. */
export interface ProjectPost {
	id: UUID;
	project_id: UUID;
	author_id: UUID | null;
	author_name: string;
	author_avatar: string | null;
	body: string;
	mentions: string[];
	/** Its #hashtags. */
	tags: string[];
	created_at: string;
	edited_at: string | null;
	via?: string | null;
}

export interface DashboardSummary {
	counts: { open_cards: number; comments_24h: number; cards_24h: number; jobs_succeeded_24h: number; jobs_failed_24h: number };
	attention: { kind: 'mention' | 'decision' | 'failed' | 'approval' | 'input'; title: string; detail: string; since: string | null; project: ProjectRef | null; link: string; for_you: boolean; notification_id?: string }[];
	live_jobs: { job_id: UUID; status: string; task: { id: UUID; name: string }; project: ProjectRef | null; agent: string | null; queued_at: string; started_at: string | null }[];
	live_runs: { run_id: UUID; name: string; project: ProjectRef | null; done: number; total: number; running: number; waiting: number; started_at: string }[];
	agents: { online: number; slots: number; busy: number };
	mine: { assigned: number; opened: number; mentions: number };
	facts: string[];
	pinned: { project: ProjectRef | null; note_id: UUID; title: string; color: string; line: string; updated_at: string }[];
	found: Record<string, number>;
}

/** Something a project found (Phase 3f): a host, service, URL, domain, user, credential, share. */
export interface AssetType {
	name: string;
	label: string;
	plural: string;
	icon: string;
	identity: string[];
	default_field: string;
	fields: { name: string; label: string; type: 'text' | 'number' | 'bool' | 'list' }[];
}

export interface Asset {
	id: UUID;
	type: string;
	key: string;
	label: string;
	fields: Record<string, unknown>;
	has_secret: boolean;
	in_scope: boolean;
	/** What people know about it (markdown). */
	description: string;
	/** The scope groups it falls in (Internal, External…). */
	scope_groups: string[];
	first_seen: string;
	last_seen: string;
	links: number;
	/** confirmed: in the inventory. suggested: only mentioned somewhere, waiting for someone. dismissed: not one. */
	status: 'confirmed' | 'suggested' | 'dismissed';
	/** How sure Castor is (0–100): a person 100, a tool's results 85, a mention 40–50. */
	confidence: number;
	/** Suggested ones: where they were mentioned, newest first. */
	mentions: AssetMention[];
}

/** A project's scope: named groups of what's in it, and exclusions that win over every group. */
export interface Scope {
	groups: { name: string; include: string[] }[];
	exclude: string[];
	/** Every group's entries together. */
	include: string[];
}

export interface AssetVerdict {
	id: UUID;
	verdict: 'real' | 'noise' | 'unsure';
	reason: string;
}

export interface AssetMention {
	source: string;
	where: string;
	excerpt: string;
	name?: string | null;
	at: string;
	link: string | null;
}

export interface AssetSource {
	kind: 'parser' | 'manual' | 'import' | 'output' | 'comment' | 'post' | 'card' | 'note' | 'ai';
	parser: string | null;
	at: string;
	job_id: UUID | null;
	task: { id: UUID; title: string; link: string } | null;
	by: string | null;
	confidence: number;
	/** In words: "a comment", "a tool's results"… */
	where: string;
	excerpt: string | null;
	link: string | null;
}

/** A kind of relationship between assets: how it reads from each end, and the types it connects. */
export interface AssetLinkKind {
	name: string;
	/** From the "from" asset: "works on". */
	forward: string;
	/** From the "to" asset: "accepts". */
	reverse: string;
	/** The reverse as a filter word: "has_admin". */
	reverse_name: string;
	/** [from type, to type] pairs; empty: any two. */
	pairs: [string, string][];
	description: string;
	/** Details usually recorded on it (offered when adding one by hand). */
	details: { name: string; label: string }[];
}

export interface AssetEnd {
	id: UUID;
	type: string;
	label: string;
	status: Asset['status'];
}

/** A relationship between two assets. */
export interface AssetLink {
	id: UUID;
	kind: string;
	forward: string;
	reverse: string;
	from: AssetEnd;
	to: AssetEnd;
	/** Details: the protocol and port a login worked over… */
	fields: Record<string, unknown>;
	note: string;
	source: AssetSource['kind'];
	parser: string | null;
	confidence: number;
	job_id: UUID | null;
	by: string | null;
	task: { id: UUID; title: string; link: string } | null;
	where: string;
	first_seen: string;
	last_seen: string;
}

/** A relationship seen from one of its assets. */
export interface AssetRelation extends AssetLink {
	outgoing: boolean;
	/** How it reads from here: "works on" or "accepts". */
	label: string;
	/** The asset at the other end. */
	asset: AssetEnd;
}

export interface AssetDetail extends Omit<Asset, 'links'> {
	provenance: Record<string, { value: unknown; source: AssetSource }[]>;
	links: AssetRelation[];
	/** A file's downloaded copy (a job artifact). */
	attachment: { id: UUID; filename: string; size: number; content_type: string } | null;
	history: { fields: Record<string, unknown>; source: AssetSource }[];
	triggers: { id: UUID; trigger: string; status: string; reason: string; at: string; task_id: UUID | null; run_id: UUID | null }[];
}

export interface AssetCondition {
	field: string;
	op: 'equals' | 'not_equals' | 'contains' | 'exists' | 'missing' | 'gt' | 'lt' | 'gte' | 'lte';
	value?: unknown;
}

export interface AssetTrigger {
	id: UUID;
	project_id: UUID;
	name: string;
	enabled: boolean;
	asset_type: string;
	on_event: 'created' | 'updated' | 'any' | 'linked';
	/** on_event "linked": a new relationship of this kind from the asset… */
	link_kind?: string | null;
	/** …to an asset of this type (any if unset). */
	other_type?: string | null;
	conditions: AssetCondition[];
	action: 'command' | 'playbook';
	target: string;
	args: Record<string, unknown>;
	once_per_asset: boolean;
	max_per_hour: number;
	fires: Record<string, number>;
	created_at: string;
}

export interface AssetTriggerFire {
	id: UUID;
	status: 'started' | 'waiting' | 'skipped' | 'limited' | 'failed';
	reason: string;
	at: string;
	trigger: { id: UUID; name: string; action: string; target: string };
	asset: { id: UUID; type: string; label: string };
	/** A relationship trigger: the asset at the other end. */
	other: { id: UUID; type: string; label: string } | null;
	task_id: UUID | null;
	run_id: UUID | null;
}

/** A command argument filled from the project's assets. */
export interface AssetRef {
	assets: { type: string; where: string; field: string };
}

/** Someone on the team, with what's on their plate (counted in projects you can see). */
export interface PersonBrief {
	id: UUID;
	username: string;
	display_name: string;
	role: UserRole;
	avatar_url: string | null;
	open_assigned: number;
	last_active: string | null;
}

export interface Person extends PersonBrief {
	created_at: string;
	projects: ProjectRef[];
	counts: { open_assigned: number; comments_30d: number; cards_30d: number; runs_30d: number; mentions_30d: number };
	assigned: { id: UUID; title: string; kind: 'manual' | 'command'; priority: number; column: string; column_role: ColumnRole | null; status: string | null; project: ProjectRef | null; updated_at: string }[];
	mentions: { id: UUID; body: string; created_at: string; author: { name: string; username: string | null; avatar_url: string | null }; task: { id: UUID; title: string }; project: ProjectRef | null; link: string }[];
}

/** One search result (GET /api/search). */
export interface SearchHit {
	id: string;
	title: string;
	snippet: string;
	link: string | null;
	icon: string;
	project: ProjectRef | null;
	at: string | null;
	/** Show the snippet in a monospace font (console output). */
	mono?: boolean;
	avatar?: string | null;
}

export interface SearchGroup {
	type: string;
	label: string;
	hits: SearchHit[];
	more: boolean;
}

export interface ProjectNote {
	id: UUID;
	project_id: UUID;
	title: string;
	body: string;
	position: number;
	/** A sticky-note color name (NoteColorPicker.NOTE_COLORS). */
	color: string;
	created_at: string;
	updated_at: string;
	updated_by: { id: string; name: string; avatar_url: string | null } | null;
	/** Earlier versions kept (see notes.history). */
	versions: number;
}

export interface NoteVersion {
	index: number;
	title: string;
	body: string;
	by_name: string;
	at: string;
}

export interface ToolAgentState {
	agent_id: UUID;
	agent_name: string;
	/** How it would get the tool: image (pull), build (recipe) or host; null if it can't. */
	way: 'image' | 'build' | 'host' | null;
	/** build: cached there; image/host: unknown (null) until a job or a prepare. */
	ready: boolean | null;
	state: 'preparing' | 'ready' | 'failed' | null;
	error: string | null;
	at: string | null;
}

export interface ToolDefinition {
	id: string;
	origin: 'builtin' | 'user';
	name: string;
	description: string;
	spec: {
		id: string;
		name: string;
		description: string;
		tags: string[];
		homepage: string;
		image: string | null;
		build: { base: string; install: string; check: string } | null;
		host: { check: string; install: string } | null;
	};
	source_yaml: string;
	version: number;
	used_by: string[];
	recipe_tag: string | null;
	agents: ToolAgentState[];
}

export interface AppNotification {
	id: UUID;
	user_id: UUID;
	project_id: UUID | null;
	kind: string;
	severity: 'info' | 'success' | 'warning' | 'error';
	title: string;
	body: string;
	link: string;
	subject: string;
	payload: Record<string, unknown>;
	read_at: string | null;
	created_at: string;
}

export interface PlaybookSpec {
	id: string;
	name: string;
	schema_version: number;
	description: string;
	tags: string[];
	inputs: PlaybookInputSpec[];
	steps: PlaybookStepSpec[];
	/** Where the visual editor placed each step. */
	layout?: Record<string, { x: number; y: number }>;
}

export interface PlaybookCheck {
	ok: boolean;
	errors: string[];
	/** Normalised (kinds and implied depends_on filled in), when it parses. */
	spec: PlaybookSpec | null;
}

export interface PlaybookDefinition {
	id: string;
	origin: 'builtin' | 'user';
	name: string;
	description: string;
	tags: string[];
	spec: PlaybookSpec;
	source_yaml: string;
	version: number;
	created_at: string;
	updated_at: string;
}

export type PlaybookRunStatus = 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface RunSummary {
	run_id: UUID;
	playbook_id: string;
	status: PlaybookRunStatus;
	done: number;
	total: number;
	running: number;
	attention: number;
	failed: number;
	step_task_ids: Record<string, string>;
}

export interface PlaybookRun {
	id: UUID;
	playbook_id: string;
	playbook_name: string;
	project_id: UUID;
	status: PlaybookRunStatus;
	inputs: Record<string, unknown>;
	step_task_ids: Record<string, string>;
	step_results: Record<string, PlaybookStepResult>;
	step_status: Record<string, PlaybookStepStatus>;
	spec_snapshot: PlaybookSpec;
	finished_at: string | null;
	created_at: string;
	/** The run's card on the board. */
	card_id: UUID | null;
	tasks: Task[];
	agent_selector?: AgentSelector;
	/** Started with it on the other agents (run on every agent with some tags). */
	sibling_run_ids?: UUID[];
}

export interface OutputSpec {
	name: string;
	type: string | null;
	parser: string | null;
	/** Path or glob in the job's work directory, as /work/… ({{ work }}/… in YAML); null: stdout. */
	source: string | null;
	/** Parser options (see the Parsers page). */
	options?: Record<string, unknown>;
}

export interface ParserOption {
	name: string;
	kind: 'boolean' | 'number' | 'string' | 'list';
	description: string;
	required: boolean;
	default: unknown;
	example: unknown;
}

export interface ParserInfo {
	name: string;
	type: string;
	description: string;
	options: ParserOption[];
	/** Built-in sample input for trying the parser. */
	sample: string;
	/** Options that suit the sample (e.g. a pattern for the regex parser). */
	sample_options: Record<string, unknown>;
	/** builtin: code in Castor; user: a definition in the library (YAML, no options). */
	origin: 'builtin' | 'user';
	title: string;
	source_yaml: string;
	version: number;
	/** Commands with an output that names it. */
	used_by: string[];
}

/** A parser definition tried on some output, without saving it. */
export interface ParserCheck {
	ok: boolean;
	errors: string[];
	type: string | null;
	value: unknown;
	count: number;
}

/** A parser the AI wrote for some output, already run on it. */
export interface ParserDraft {
	yaml: string;
	preview: unknown;
	count: number;
	problems: string[];
	notes: string;
	model: string;
}

/** A parser the AI drafted for a job output its parser couldn't read. */
export interface ParserProposal {
	id: UUID;
	job_id: UUID;
	project_id: UUID;
	output_name: string;
	failed_parser: string;
	failure: string;
	status: 'drafting' | 'ready' | 'failed' | 'applied' | 'dismissed';
	yaml: string;
	preview: unknown;
	count: number;
	problems: string[];
	notes: string;
	model: string;
	applied_by: string;
	saved_as: string;
	/** Drafted on its own, for a playbook step waiting on the output. */
	automatic: boolean;
	/** After keeping it: what happened to the card's command. */
	command_note?: string;
	created_at: string;
	updated_at: string;
}

export interface ParseTestResult {
	type: string;
	value: unknown;
	error: string | null;
	option_errors: Record<string, string>;
	elapsed_ms: number;
}

/** A parsed result of a job; `type` says the shape of `value` (null when `error` is set). */
export interface JobOutput {
	name: string;
	type: string;
	parser: string;
	sources: string[];
	value: unknown;
	error: string | null;
	created_at: string;
}

export interface JobDetail extends Job {
	logs: LogChunk[];
	outputs: JobOutput[];
}

export type ArgType = 'string' | 'number' | 'boolean' | 'list' | 'varset';

export interface ArgSpec {
	name: string;
	type: ArgType;
	description: string;
	required: boolean;
	default: unknown;
	example: unknown;
	regex: string | null;
	choices: string[] | null;
	separator: string;
	/** varset playbook inputs: the variable kind the chosen set must have. */
	kind?: string | null;
	/** Dropdown options read from a variable set (the server fills them into `choices`). */
	choices_from?: { set: string; field: string } | null;
	/** Rendered as "<flag> <value>" (booleans: "<flag>" when on), or nothing when empty/off. */
	flag?: string | null;
	/** Name of a boolean argument: shown and used only while it is on. */
	show_if?: string | null;
}

/** A variable set a command needs; its template uses {{ name.field }}. */
export interface VarSlot {
	name: string;
	kind: string;
	description: string;
	required: boolean;
}

export type VarFieldType = 'text' | 'number' | 'boolean' | 'host' | 'port' | 'list' | 'secret' | 'file' | 'secret_file';

export interface VarField {
	name: string;
	type: VarFieldType;
	label: string;
	description: string;
	required: boolean;
	default: unknown;
	/** Text: pick one (dropdown). List: pick any (toggles). */
	choices?: string[] | null;
}

export interface VariableKind {
	id: string;
	origin: 'builtin' | 'user';
	name: string;
	description: string;
	fields: VarField[];
	set_count: number;
}

export interface VariableSet {
	id: UUID;
	kind_id: string;
	name: string;
	description: string;
	/** Empty: usable in every project. */
	project_ids: UUID[];
	/** Plain fields only. */
	values: Record<string, unknown>;
	/** Secret fields: true when a value is stored (values are never sent). */
	secrets: Record<string, boolean>;
	files: Record<string, { filename: string; size: number }>;
	missing: string[];
	/** A one-off set entered on a card or at run time; not in the library. */
	ephemeral: boolean;
	created_at: string;
	updated_at: string;
}

export interface CommandSpec {
	id: string;
	name: string;
	description: string;
	tags: string[];
	image: string | null;
	/** A tool from the library (Tools) the command runs with, instead of an image. */
	tool?: string | null;
	container_flags?: string[];
	/** Runs in an interactive terminal (a pty). */
	tty?: boolean;
	args: ArgSpec[];
	vars?: VarSlot[];
	command: string;
	outputs: OutputSpec[];
}

export interface CommandDefinition {
	id: string;
	origin: 'builtin' | 'user';
	name: string;
	description: string;
	tags: string[];
	spec: CommandSpec;
	source_yaml: string;
	version: number;
	updated_at: string;
	in_use: boolean;
	/** Board cards that run it, in the projects you can see. */
	card_count: number;
	/** Playbooks with a step that runs it. */
	playbooks: string[];
}

export interface CommandUsage {
	cards: { id: UUID; title: string; project_id: UUID; project_codename: string }[];
	card_count: number;
	playbooks: { id: string; name: string }[];
}

/** 422 detail for invalid command arguments. */
export interface ArgErrors {
	message: string;
	errors: Record<string, string>;
}

export interface Task {
	/** Run in an interactive terminal (or its library command says so). */
	tty?: boolean;
	/** Chat/webhook alerts for its runs (or, on a run card, the playbook run). */
	alerts?: AlertRules | null;
	id: UUID;
	project_id: UUID;
	column_id: UUID;
	position: number;
	title: string;
	description: string;
	kind: TaskKind;
	command: string;
	/** Library command this card runs; `command` then holds the rendered line. */
	command_ref: string | null;
	command_args: Record<string, unknown>;
	image: string | null;
	tool?: string | null;
	container_flags?: string[];
	agent_selector: AgentSelector;
	/** The latest run went to several agents at once (one job each). */
	batch?: BatchSummary | null;
	run_on_move: boolean;
	timeout_s: number | null;
	priority: number;
	tags: string[];
	assignees: string[];
	/** Group ids: everyone in them who can see the card. */
	group_assignees: string[];
	custom_fields: Record<string, unknown>;
	due_at: string | null;
	created_by: UUID | null;
	playbook_run_id?: UUID | null;
	playbook_step_id?: string | null;
	/** Item cards of a for_each step: the item's position. */
	playbook_item?: number | null;
	depends_on?: string[];
	/** Variable slot → set id. */
	var_bindings?: Record<string, UUID>;
	created_at: string;
	updated_at: string;
	latest_job: Job | null;
	/** Set on a playbook run's card: where the run is (its steps are hidden cards). */
	run?: RunSummary | null;
	comment_count: number;
	attachment_count: number;
}

export interface Board {
	project: Project;
	columns: Column[];
	tasks: Task[];
}

export interface Comment {
	id: UUID;
	task_id: UUID;
	author_id: UUID | null;
	author_name: string;
	author_avatar?: string | null;
	body: string;
	/** Ids of the people it @mentions. */
	mentions: string[];
	created_at: string;
	updated_at: string;
	/** Set when the body was changed after posting. */
	edited_at: string | null;
	/** 'mcp': written (or last edited) through an AI assistant. */
	via?: string | null;
}

export interface Attachment {
	id: UUID;
	project_id: UUID;
	task_id: UUID | null;
	job_id: UUID | null;
	kind: 'file' | 'screenshot' | 'artifact';
	filename: string;
	content_type: string;
	size: number;
	created_at: string;
}

export interface Activity {
	id: number;
	project_id: UUID | null;
	task_id: UUID | null;
	actor_type: 'user' | 'agent' | 'system';
	actor_id: string | null;
	actor_name: string;
	type: string;
	message: string;
	payload: Record<string, unknown>;
	created_at: string;
}

export type AgentStatus = 'idle' | 'busy' | 'offline';

/** A per-agent credential; unbound (agent_id null) until an agent enrolls with it. */
export interface AgentToken {
	id: UUID;
	label: string;
	/** The token's first characters, to recognise it. */
	prefix: string;
	tags: string[];
	agent_id: UUID | null;
	agent_name: string | null;
	created_at: string;
	last_used_at: string | null;
	rotated_at: string | null;
	revoked_at: string | null;
}

/** A personal API token: acts as its owner, narrowed to its scopes and projects. */
export interface ApiToken {
	id: UUID;
	name: string;
	/** The token's first characters, to recognise it. */
	prefix: string;
	scopes: string[];
	/** Projects it's limited to (ids); null: every project its owner can see. */
	project_ids: string[] | null;
	projects: string[];
	user: UserBrief | null;
	created_at: string;
	expires_at: string | null;
	last_used_at: string | null;
	last_used_ip: string;
	revoked_at: string | null;
}

export interface ApiScope {
	name: string;
	label: string;
	description: string;
	group: string;
	/** The least role that can use it. */
	role: UserRole;
}

/** Where a card or a playbook run's steps go: any agent ({}), one agent, any one agent with
 *  the tags, or (all) every connected agent with them. */
export interface AgentSelector {
	agent_id?: UUID;
	tags?: string[];
	all?: boolean;
}

export interface BatchSummary {
	total: number;
	active: number;
	succeeded: number;
	failed: number;
	status: 'running' | 'succeeded' | 'failed';
	job_ids: UUID[];
}

export interface Agent {
	id: UUID;
	name: string;
	hostname: string;
	os: string;
	username: string;
	version: string;
	capabilities: { runtimes?: string[]; default_image?: string };
	tags: string[];
	max_concurrency: number;
	status: AgentStatus;
	last_seen_at: string | null;
	created_at: string;
	running_jobs: number;
	connected: boolean;
	/** Set for an SSH host Castor connects to, rather than an agent that dials in. */
	ssh_host_id?: UUID | null;
}

export type SshHostState = 'connecting' | 'connected' | 'error' | 'host_key_changed' | 'disabled';

export interface SshHost {
	id: UUID;
	agent_id: UUID;
	name: string;
	/** An address, or a Host alias from the SSH config. */
	host: string;
	/** Null (port) or empty (username): from the SSH config. */
	port: number | null;
	username: string;
	workdir: string;
	network: string;
	allow_host: boolean;
	allow_host_install: boolean;
	enabled: boolean;
	tags: string[];
	max_concurrency: number;
	/** How the SSH config reaches it: each hop as user@host:port, the host itself last. */
	route: string[];
	route_error: string | null;
	/** Jump hosts' key fingerprints by hop, trusted and waiting to be accepted. */
	jump_host_keys: Record<string, string | null>;
	jump_host_keys_pending: Record<string, string | null>;
	/** Goes in the account's ~/.ssh/authorized_keys. */
	public_key: string;
	key_fingerprint: string | null;
	host_key_fingerprint: string | null;
	/** A different key the host presented, waiting to be accepted. */
	host_key_pending_fingerprint: string | null;
	state: SshHostState;
	error: string | null;
	connected: boolean;
}

/** The SSH config (ssh_config format) SSH hosts are reached with. */
export interface SshConfig {
	text: string;
	errors: string[];
	warnings: string[];
	/** Each SSH host's route with this config, by name, or why it has none. */
	routes: Record<string, string[]>;
	route_errors: Record<string, string>;
}

export interface SshHostIn {
	name: string;
	host: string;
	port: number | null;
	username: string;
	workdir: string;
	network: string;
	allow_host: boolean;
	allow_host_install: boolean;
	enabled: boolean;
	tags: string[];
	max_concurrency: number;
	generate_key?: boolean;
	private_key?: string;
	passphrase?: string;
}

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string,
		/** The response's `detail` when it is an object (e.g. per-argument errors). */
		public detail: unknown = null
	) {
		super(message);
	}
}

type Fetch = typeof fetch;

async function request<T>(
	fetchFn: Fetch,
	method: string,
	path: string,
	body?: unknown,
	init: RequestInit = {}
): Promise<T> {
	const headers = new Headers(init.headers);
	let payload: BodyInit | undefined;
	if (body instanceof FormData) payload = body;
	else if (body !== undefined) {
		headers.set('content-type', 'application/json');
		payload = JSON.stringify(body);
	}
	const res = await fetchFn(`/api${path}`, { ...init, method, headers, body: payload, credentials: 'same-origin' });
	if (!res.ok) {
		let message = res.statusText;
		let detail: unknown = null;
		try {
			const data = await res.json();
			detail = data.detail ?? null;
			message =
				typeof data.detail === 'string'
					? data.detail
					: typeof data.detail?.message === 'string'
						? data.detail.message
						: JSON.stringify(data.detail ?? data);
		} catch {
			/* no body */
		}
		throw new ApiError(res.status, message, detail);
	}
	if (res.status === 204) return undefined as T;
	return (await res.json()) as T;
}

/** Create an API client bound to a fetch implementation (pass SvelteKit's `fetch` in load functions). */
export function api(fetchFn: Fetch = fetch) {
	const get = <T>(path: string) => request<T>(fetchFn, 'GET', path);
	const post = <T>(path: string, body?: unknown) => request<T>(fetchFn, 'POST', path, body);
	const patch = <T>(path: string, body?: unknown) => request<T>(fetchFn, 'PATCH', path, body);
	const del = (path: string) => request<void>(fetchFn, 'DELETE', path);

	return {
		auth: {
			login: (username: string, password: string) =>
				post<{ token: string; user: User }>('/auth/login', { username, password }),
			logout: () => post<void>('/auth/logout'),
			me: () => get<User>('/auth/me'),
			updateMe: (body: { display_name?: string; theme_seed?: string; theme?: string | null; companion?: boolean; companion_seat?: number }) => patch<User>('/auth/me', body),
			/** Your own companion picture (only you see it). */
			uploadCompanion: (file: File) => {
				const form = new FormData();
				form.append('file', file);
				return post<User>('/auth/me/companion', form);
			},
			/** Back to the built-in companion. */
			resetCompanion: () => request<User>(fetchFn, 'DELETE', '/auth/me/companion'),
			changePassword: (current_password: string, new_password: string) =>
				post<void>('/auth/password', { current_password, new_password }),
			/** What the sign-in page offers: providers to sign in with, and whether passwords work. */
			signInOptions: () => get<{ providers: { id: string; name: string }[]; password_login: boolean }>('/auth/sso')
		},
		groups: {
			list: () => get<Group[]>('/groups'),
			get: (id: UUID) => get<GroupDetail>(`/groups/${id}`),
			create: (body: GroupIn) => post<GroupDetail>('/groups', body),
			update: (id: UUID, body: GroupIn) => request<GroupDetail>(fetchFn, 'PUT', `/groups/${id}`, body),
			setMembers: (id: UUID, userIds: UUID[]) => request<GroupDetail>(fetchFn, 'PUT', `/groups/${id}/members`, userIds),
			remove: (id: UUID) => del(`/groups/${id}`)
		},
		sso: {
			list: () => get<SsoProvider[]>('/sso-providers'),
			create: (body: SsoProviderIn) => post<SsoProvider>('/sso-providers', body),
			update: (id: string, body: SsoProviderIn) => request<SsoProvider>(fetchFn, 'PUT', `/sso-providers/${id}`, body),
			remove: (id: string) => del(`/sso-providers/${id}`),
			test: (issuer: string) => post<{ ok: boolean; error: string | null; issuer: string | null; authorization_endpoint: string | null }>('/sso-providers/test', { issuer })
		},
		users: {
			list: () => get<User[]>('/users'),
			directory: () => get<UserBrief[]>('/users/directory'),
			create: (body: { username: string; display_name?: string; role: UserRole; password: string; email?: string; all_projects?: boolean }) =>
				post<User>('/users', body),
			update: (id: UUID, body: { display_name?: string; role?: UserRole; disabled?: boolean; password?: string; email?: string }) =>
				patch<User>(`/users/${id}`, body),
			/** For good: their sign-ins, tokens and memberships go; their work stays, by a deleted user. */
			remove: (id: UUID) => del(`/users/${id}`),
			setAvatar: (id: UUID, body: { kind: AvatarKind; url?: string; gravatar_email?: string }) =>
				request<User>(fetchFn, 'PUT', `/users/${id}/avatar`, body),
			uploadAvatar: (id: UUID, file: File) => {
				const form = new FormData();
				form.append('file', file);
				return post<User>(`/users/${id}/avatar/upload`, form);
			}
		},
		clients: {
			list: () => get<Client[]>('/clients'),
			create: (body: { name: string; notes?: string }) => post<Client>('/clients', body)
		},
		projects: {
			members: (id: UUID) => get<UserBrief[]>(`/projects/${id}/members`),
			/** Everyone who can see the project (members and admins): assignees, @mentions. */
			people: (id: UUID) => get<UserBrief[]>(`/projects/${id}/people`),
			setMembers: (id: UUID, userIds: UUID[]) => request<UserBrief[]>(fetchFn, 'PUT', `/projects/${id}/members`, userIds),
			/** The groups the project is shared with (everyone in them can see it). */
			groups: (id: UUID) => get<Group[]>(`/projects/${id}/groups`),
			setGroups: (id: UUID, groupIds: UUID[]) => request<Group[]>(fetchFn, 'PUT', `/projects/${id}/groups`, groupIds),
			list: () => get<ProjectSummary[]>('/projects'),
			get: (id: UUID) => get<Project>(`/projects/${id}`),
			create: (body: Partial<Project> & { codename: string; name: string }) =>
				post<Project>('/projects', body),
			update: (id: UUID, body: Partial<Project>) => patch<Project>(`/projects/${id}`, body),
			remove: (id: UUID) => del(`/projects/${id}`),
			board: (id: UUID) => get<Board>(`/projects/${id}/board`),
			activity: (id: UUID, limit = 50) => get<Activity[]>(`/projects/${id}/activity?limit=${limit}`),
			createColumn: (id: UUID, body: Partial<Column> & { name: string }) =>
				post<Column>(`/projects/${id}/columns`, body),
			updateColumn: (id: UUID, columnId: UUID, body: Partial<Column>) =>
				patch<Column>(`/projects/${id}/columns/${columnId}`, body),
			removeColumn: (id: UUID, columnId: UUID) => del(`/projects/${id}/columns/${columnId}`),
			createTask: (id: UUID, body: Partial<Task> & { title: string }) =>
				post<Task>(`/projects/${id}/tasks`, body)
		},
		tasks: {
			/** A manual card's own AI summary (from its description and comments). */
			summary: (id: UUID) => get<AiSummary | null>(`/tasks/${id}/summary`),
			summarize: (id: UUID) => post<AiSummary>(`/tasks/${id}/summary`),
			get: (id: UUID) => get<Task>(`/tasks/${id}`),
			update: (id: UUID, body: Partial<Task>) => patch<Task>(`/tasks/${id}`, body),
			remove: (id: UUID) => del(`/tasks/${id}`),
			/** A copy at the top of To Do; comments, files and finished runs only if asked. */
			duplicate: (id: UUID, body: { assignees?: boolean; comments?: boolean; attachments?: boolean; output?: boolean }) =>
				post<Task>(`/tasks/${id}/duplicate`, body),
			move: (id: UUID, column_id: UUID, position: number) =>
				post<Task>(`/tasks/${id}/move`, { column_id, position }),
			run: (id: UUID) => post<Job>(`/tasks/${id}/run`),
			jobs: (id: UUID) => get<Job[]>(`/tasks/${id}/jobs`),
			activity: (id: UUID) => get<Activity[]>(`/tasks/${id}/activity`),
			comments: (id: UUID) => get<Comment[]>(`/tasks/${id}/comments`),
			addComment: (id: UUID, body: string) => post<Comment>(`/tasks/${id}/comments`, { body }),
			updateComment: (id: UUID, commentId: UUID, body: string) =>
				patch<Comment>(`/tasks/${id}/comments/${commentId}`, { body }),
			removeComment: (id: UUID, commentId: UUID) => del(`/tasks/${id}/comments/${commentId}`),
			/** To another card of the project, or (null) out to its timeline as a post. */
			moveComment: (id: UUID, commentId: UUID, toTaskId: UUID | null) =>
				post<MoveResult>(`/tasks/${id}/comments/${commentId}/move`, { to_task_id: toTaskId }),
			attachments: (id: UUID) => get<Attachment[]>(`/tasks/${id}/attachments`),
			upload: (id: UUID, file: File, kind?: string) => {
				const form = new FormData();
				form.append('file', file);
				if (kind) form.append('kind', kind);
				return post<Attachment>(`/tasks/${id}/attachments`, form);
			}
		},
		attachments: {
			remove: (id: UUID) => del(`/attachments/${id}`),
			url: (id: UUID, inline = false) => `/api/attachments/${id}/download${inline ? '?inline=true' : ''}`
		},
		jobs: {
			list: (status?: JobStatus) => get<Job[]>(`/jobs${status ? `?status=${status}` : ''}`),
			get: (id: UUID, afterSeq = -1) => get<JobDetail>(`/jobs/${id}?after_seq=${afterSeq}`),
			cancel: (id: UUID) => post<Job>(`/jobs/${id}/cancel`),
			summary: (id: UUID) => get<AiSummary | null>(`/jobs/${id}/summary`),
			summarize: (id: UUID) => post<AiSummary>(`/jobs/${id}/summary`),
			artifacts: (id: UUID) => get<Attachment[]>(`/jobs/${id}/artifacts`),
			outputs: (id: UUID) => get<JobOutput[]>(`/jobs/${id}/outputs`),
			parse: (id: UUID) => post<JobOutput[]>(`/jobs/${id}/outputs/parse`),
			/** What an output's parser read (its files or stdout), to write a parser against. */
			outputInput: (id: UUID, name: string) =>
				get<{ text: string; sources: string[]; truncated: boolean; parser: string; type: string; error: string | null }>(
					`/jobs/${id}/outputs/${encodeURIComponent(name)}/input`
				),
			parserProposals: (id: UUID) => get<ParserProposal[]>(`/jobs/${id}/parser-proposals`),
			draftParser: (id: UUID, name: string) => post<ParserProposal>(`/jobs/${id}/outputs/${encodeURIComponent(name)}/parser-proposal`)
		},
		parserProposals: {
			/** With `saveAs`, keep it in the library; `useInCommand` also points the card's command (if yours) at it. */
			apply: (id: UUID, saveAs?: string, useInCommand = false) =>
				post<ParserProposal>(`/parser-proposals/${id}/apply`, { save_as: saveAs || null, use_in_command: useInCommand }),
			dismiss: (id: UUID) => post<ParserProposal>(`/parser-proposals/${id}/dismiss`)
		},
		parsers: {
			list: () => get<ParserInfo[]>('/parsers'),
			get: (name: string) => get<ParserInfo>(`/parsers/${encodeURIComponent(name)}`),
			test: (name: string, input: string, options: Record<string, unknown>) =>
				post<ParseTestResult>(`/parsers/${encodeURIComponent(name)}/test`, { input, options }),
			create: (yaml: string) => post<ParserInfo>('/parsers', { yaml }),
			update: (name: string, yaml: string) => request<ParserInfo>(fetchFn, 'PUT', `/parsers/${encodeURIComponent(name)}`, { yaml }),
			remove: (name: string) => del(`/parsers/${encodeURIComponent(name)}`),
			/** Check a definition and run it on `input` (or its own sample), without saving. */
			validate: (yaml: string, input?: string) => post<ParserCheck>('/parsers/validate', { yaml, input: input ?? null }),
			draft: (body: { sample: string; type?: string | null; hint?: string; current?: string }) => post<ParserDraft>('/parsers/draft', body),
			/** What a definition can say (formats, output types and their fields, limits), from the server. */
			reference: () =>
				get<{
					formats: string[];
					types: { type: string; fields: { name: string; kind: string; required: boolean }[]; free: boolean }[];
					field_types: string[];
					limits: { regex_seconds: number; js_seconds: number; js_memory_mb: number; records: number; test_input_mb: number };
				}>('/parsers/reference')
		},
		playbooks: {
			list: () => get<PlaybookDefinition[]>('/playbooks'),
			get: (id: string) => get<PlaybookDefinition>(`/playbooks/${id}`),
			create: (yaml: string) => post<PlaybookDefinition>('/playbooks', { yaml }),
			update: (id: string, yaml: string) =>
				request<PlaybookDefinition>(fetchFn, 'PUT', `/playbooks/${id}`, { yaml }),
			remove: (id: string) => del(`/playbooks/${id}`),
			run: (id: string, projectId: UUID, inputs: Record<string, unknown> = {}, alerts: AlertRules | null = null, agentSelector: AgentSelector = {}) =>
				post<PlaybookRun>(`/playbooks/${id}/run`, { project_id: projectId, inputs, agent_selector: agentSelector, ...(alerts ? { alerts } : {}) }),
			validate: (yaml: string) => post<PlaybookCheck>('/playbooks/validate', { yaml }),
			/** The editor's spec as YAML, validated (YAML is returned even when invalid). */
			toYaml: (spec: PlaybookSpec) => post<PlaybookCheck & { yaml: string }>('/playbooks/to-yaml', { spec })
		},
		playbookRuns: {
			summary: (id: UUID) => get<AiSummary | null>(`/playbook-runs/${id}/summary`),
			summarize: (id: UUID) => post<AiSummary>(`/playbook-runs/${id}/summary`),
			list: (params?: { project_id?: string; playbook_id?: string; limit?: number }) => {
				const q = new URLSearchParams();
				if (params?.project_id) q.set('project_id', params.project_id);
				if (params?.playbook_id) q.set('playbook_id', params.playbook_id);
				if (params?.limit) q.set('limit', String(params.limit));
				const qs = q.toString();
				return get<PlaybookRun[]>(`/playbook-runs${qs ? `?${qs}` : ''}`);
			},
			get: (id: UUID) => get<PlaybookRun>(`/playbook-runs/${id}`),
			cancel: (id: UUID) => post<PlaybookRun>(`/playbook-runs/${id}/cancel`),
			resolve: (id: UUID, stepId: string, action: ResolveAction, note = '') =>
				post<PlaybookRun>(`/playbook-runs/${id}/steps/${stepId}/resolve`, { action, note }),
			/** Ask the AI resolver about a paused step (it only suggests). */
			aiSuggest: (id: UUID, stepId: string) => post<AiSuggestion>(`/playbook-runs/${id}/steps/${stepId}/ai/suggest`),
			/** Do what the AI suggested, as you. */
			aiApply: (id: UUID, stepId: string) => post<PlaybookRun>(`/playbook-runs/${id}/steps/${stepId}/ai/apply`)
		},
		notifications: {
			list: (unreadOnly = false, limit = 50) =>
				get<{ items: AppNotification[]; unread: number }>(`/notifications?unread_only=${unreadOnly}&limit=${limit}`),
			read: (id: UUID) => post<AppNotification>(`/notifications/${id}/read`),
			readAll: () => post<void>('/notifications/read-all')
		},
		commands: {
			list: () => get<CommandDefinition[]>('/commands'),
			get: (id: string) => get<CommandDefinition>(`/commands/${id}`),
			usage: (id: string) => get<CommandUsage>(`/commands/${id}/usage`),
			create: (yaml: string) => post<CommandDefinition>('/commands', { yaml }),
			update: (id: string, yaml: string) =>
				request<CommandDefinition>(fetchFn, 'PUT', `/commands/${id}`, { yaml }),
			remove: (id: string) => del(`/commands/${id}`),
			render: (id: string, args: Record<string, unknown>) =>
				post<{ command: string; image: string | null; container_flags?: string[] }>(`/commands/${id}/render`, { args })
		},
		variables: {
			kinds: () => get<VariableKind[]>('/variables/kinds'),
			createKind: (body: Pick<VariableKind, 'id' | 'name' | 'description' | 'fields'>) =>
				post<VariableKind>('/variables/kinds', body),
			updateKind: (id: string, body: Pick<VariableKind, 'id' | 'name' | 'description' | 'fields'>) =>
				request<VariableKind>(fetchFn, 'PUT', `/variables/kinds/${id}`, body),
			removeKind: (id: string) => del(`/variables/kinds/${id}`),
			sets: (params?: { kind?: string; project_id?: UUID }) => {
				const q = new URLSearchParams();
				if (params?.kind) q.set('kind', params.kind);
				if (params?.project_id) q.set('project_id', params.project_id);
				const qs = q.toString();
				return get<VariableSet[]>(`/variables/sets${qs ? `?${qs}` : ''}`);
			},
			getSet: (id: UUID) => get<VariableSet>(`/variables/sets/${id}`),
			createSet: (body: {
				kind_id: string;
				name?: string;
				description?: string;
				project_ids?: UUID[];
				values?: Record<string, unknown>;
				ephemeral?: boolean;
			}) => post<VariableSet>('/variables/sets', body),
			updateSet: (
				id: UUID,
				body: { name?: string; description?: string; project_ids?: UUID[]; values?: Record<string, unknown>; ephemeral?: boolean }
			) => patch<VariableSet>(`/variables/sets/${id}`, body),
			removeSet: (id: UUID) => del(`/variables/sets/${id}`),
			uploadFile: (id: UUID, field: string, file: File) => {
				const form = new FormData();
				form.append('file', file);
				return request<VariableSet>(fetchFn, 'PUT', `/variables/sets/${id}/files/${field}`, form);
			},
			removeFile: (id: UUID, field: string) =>
				request<VariableSet>(fetchFn, 'DELETE', `/variables/sets/${id}/files/${field}`),
			/** Reading a secret is recorded in the activity feed. Files come back base64-encoded. */
			reveal: (id: UUID, field: string) =>
				post<{ field: string; type: VarFieldType; value: unknown; filename: string | null }>(
					`/variables/sets/${id}/reveal/${field}`
				)
		},
		agents: {
			list: () => get<Agent[]>('/agents'),
			update: (id: UUID, body: { tags?: string[]; max_concurrency?: number }) =>
				patch<Agent>(`/agents/${id}`, body),
			remove: (id: UUID) => del(`/agents/${id}`)
		},
		tags: {
			/** Card tags in the projects you can see, this project's first. */
			cards: (projectId?: UUID) => get<TagSuggestion[]>(`/tags/cards${projectId ? `?project_id=${projectId}` : ''}`),
			agents: () => get<TagSuggestion[]>('/tags/agents'),
			library: () => get<TagSuggestion[]>('/tags/library'),
			/** Rename a tag on a project's cards, or merge several (onto an existing tag merges). */
			renameCards: (projectId: UUID, body: { from: string[]; to: string }) =>
				post<{ changed: Record<string, number> }>(`/projects/${projectId}/tags/rename`, body),
			/** Agents, tokens, and the cards and runs that target agents by the tags (admins). */
			renameAgents: (body: { from: string[]; to: string }) => post<{ changed: Record<string, number> }>('/tags/agents/rename', body)
		},
		sshHosts: {
			list: () => get<SshHost[]>('/ssh-hosts'),
			create: (body: SshHostIn) => post<SshHost>('/ssh-hosts', body),
			update: (id: UUID, body: SshHostIn) => request<SshHost>(fetchFn, 'PUT', `/ssh-hosts/${id}`, body),
			acceptHostKey: (id: UUID) => post<SshHost>(`/ssh-hosts/${id}/accept-host-key`),
			reconnect: (id: UUID) => post<SshHost>(`/ssh-hosts/${id}/reconnect`),
			remove: (id: UUID) => del(`/ssh-hosts/${id}`),
			config: () => get<SshConfig>('/ssh-config'),
			checkConfig: (text: string) => post<SshConfig>('/ssh-config/check', { text }),
			saveConfig: (text: string) => request<SshConfig>(fetchFn, 'PUT', '/ssh-config', { text })
		},
		agentTokens: {
			list: () => get<{ tokens: AgentToken[]; shared_secret_allowed: boolean }>('/agent-tokens'),
			/** The token is in the reply only this once. */
			create: (body: { label?: string; tags?: string[] }) => post<AgentToken & { token: string }>('/agent-tokens', body),
			rotate: (id: UUID) => post<AgentToken & { token: string }>(`/agent-tokens/${id}/rotate`),
			revoke: (id: UUID) => post<AgentToken>(`/agent-tokens/${id}/revoke`),
			remove: (id: UUID) => del(`/agent-tokens/${id}`)
		},
		apiTokens: {
			scopes: () => get<ApiScope[]>('/tokens/scopes'),
			/** Yours; with everyone (admins), every user's. */
			list: (everyone = false) => get<ApiToken[]>(`/tokens${everyone ? '?everyone=true' : ''}`),
			/** The token is in the reply only this once. */
			create: (body: { name: string; scopes: string[]; project_ids: UUID[] | null; expires_in_days: number | null }) =>
				post<ApiToken & { token: string }>('/tokens', body),
			revoke: (id: UUID) => post<ApiToken>(`/tokens/${id}/revoke`),
			remove: (id: UUID) => del(`/tokens/${id}`)
		},
		themes: {
			list: () => get<Theme[]>('/themes'),
			get: (id: string) => get<Theme>(`/themes/${id}`),
			create: (yaml: string) => post<Theme>('/themes', { yaml }),
			update: (id: string, yaml: string) => request<Theme>(fetchFn, 'PUT', `/themes/${id}`, { yaml }),
			remove: (id: string) => del(`/themes/${id}`),
			validate: (yaml: string) => post<{ ok: boolean; errors: string[]; theme: Theme | null }>('/themes/validate', { yaml }),
			/** The instance default (public: the sign-in page shows it). */
			default: () => get<DefaultTheme>('/themes/default')
		},
		plugins: {
			list: () => get<Plugin[]>('/plugins'),
			get: (id: string) => get<Plugin>(`/plugins/${id}`),
			/** A .zip (or an https URL to one): what it is and asks for, before anything runs. */
			preview: (src: { file?: File; url?: string }) => {
				const form = new FormData();
				if (src.file) form.append('file', src.file);
				if (src.url) form.append('url', src.url);
				return request<PluginPreview>(fetchFn, 'POST', '/plugins/preview', form);
			},
			install: (body: { token: string; source: string; approve: boolean; project_ids: UUID[]; all_projects: boolean; settings?: Record<string, unknown>; secrets?: Record<string, string | null> }) =>
				post<Plugin>('/plugins', body),
			update: (id: string, body: { enabled?: boolean; all_projects?: boolean; project_ids?: UUID[]; settings?: Record<string, unknown>; secrets?: Record<string, string | null> }) =>
				patch<Plugin>(`/plugins/${id}`, body),
			restart: (id: string) => post<Plugin>(`/plugins/${id}/restart`),
			remove: (id: string) => del(`/plugins/${id}`),
			logs: (id: string, limit = 200) => get<PluginLogEntry[]>(`/plugins/${id}/logs?limit=${limit}`),
			runs: (id: string) => get<PluginRun[]>(`/plugins/${id}/runs`),
			/** The actions of the plugins enabled in a project. */
			actions: (projectId: UUID) => get<ProjectPluginAction[]>(`/projects/${projectId}/plugin-actions`),
			run: (pluginId: string, action: string, ctx: { project_id: UUID; task_id?: UUID; asset_id?: UUID }, file?: File) => {
				const form = new FormData();
				form.append('project_id', ctx.project_id);
				if (ctx.task_id) form.append('task_id', ctx.task_id);
				if (ctx.asset_id) form.append('asset_id', ctx.asset_id);
				if (file) form.append('file', file);
				return request<PluginRun>(fetchFn, 'POST', `/plugins/${pluginId}/actions/${action}`, form);
			},
			getRun: (id: UUID) => get<PluginRun>(`/plugin-runs/${id}`),
			projectRuns: (projectId: UUID) => get<PluginRun[]>(`/projects/${projectId}/plugin-runs`)
		},
		tools: {
			list: () => get<ToolDefinition[]>('/tools'),
			get: (id: string) => get<ToolDefinition>(`/tools/${id}`),
			create: (yaml: string) => post<ToolDefinition>('/tools', { yaml }),
			update: (id: string, yaml: string) => request<ToolDefinition>(fetchFn, 'PUT', `/tools/${id}`, { yaml }),
			remove: (id: string) => del(`/tools/${id}`),
			validate: (yaml: string) =>
				post<{ ok: boolean; errors: string[]; recipe_tag?: string | null; dockerfile?: string | null }>('/tools/validate', { yaml }),
			prepare: (id: string, agentIds: string[] = []) =>
				post<{ started: string[]; skipped: Record<string, string> }>(`/tools/${id}/prepare`, { agent_ids: agentIds })
		},
		assets: {
			types: () => get<AssetType[]>('/assets/types'),
			list: (projectId: UUID, params: { type?: string; where?: string; limit?: number; status?: Asset['status']; scope?: string } = {}) => {
				const q = new URLSearchParams();
				if (params.type) q.set('type', params.type);
				if (params.where) q.set('where', params.where);
				if (params.limit !== undefined) q.set('limit', String(params.limit));
				if (params.status) q.set('status', params.status);
				if (params.scope) q.set('scope', params.scope);
				return get<{ items: Asset[]; total: number; counts: Record<string, number>; suggested: number }>(`/projects/${projectId}/assets?${q}`);
			},
			confirm: (id: UUID) => post<Asset>(`/assets/${id}/confirm`),
			dismiss: (id: UUID) => post<Asset>(`/assets/${id}/dismiss`),
			decide: (projectId: UUID, action: 'confirm' | 'dismiss', ids: UUID[]) =>
				post<{ done: number }>(`/projects/${projectId}/assets/suggested`, { action, ids }),
			merge: (id: UUID, into: UUID) => post<Asset>(`/assets/${id}/merge`, { into }),
			/** The AI's advice on suggested assets (nothing changes): real, noise or unsure, with a reason. */
			sortSuggested: (projectId: UUID, guidance: string, ids: UUID[] = []) =>
				post<{ verdicts: AssetVerdict[]; model: string; judged: number; left: number }>(`/projects/${projectId}/assets/suggested/sort`, { guidance, ids }),
			get: (id: UUID) => get<AssetDetail>(`/assets/${id}`),
			add: (projectId: UUID, body: { type: string; fields: Record<string, unknown>; secret?: string }) => post<Asset>(`/projects/${projectId}/assets`, body),
			import: (projectId: UUID, type: string, text: string) =>
				post<{ added: number; updated: number; skipped: string[] }>(`/projects/${projectId}/assets/import`, { type, text }),
			update: (id: UUID, fields: Record<string, unknown>) => patch<Asset>(`/assets/${id}`, { fields }),
			/** Its description (markdown). */
			describe: (id: UUID, description: string) => patch<Asset>(`/assets/${id}`, { description }),
			/** Add one entry to a scope group (made if new). */
			addToScope: (projectId: UUID, entry: string, group: string) => post<Scope>(`/projects/${projectId}/scope/add`, { entry, group }),
			remove: (id: UUID) => del(`/assets/${id}`),
			reveal: (id: UUID) => post<{ secret: string }>(`/assets/${id}/reveal`),
			collect: (projectId: UUID) => post<{ added: number; updated: number; suggested: number }>(`/projects/${projectId}/assets/collect`),
			scope: (projectId: UUID) => get<Scope>(`/projects/${projectId}/scope`),
			setScope: (projectId: UUID, body: { groups?: { name: string; include: string[] }[]; include?: string[]; exclude: string[] }) =>
				request<Scope>(fetchFn, 'PUT', `/projects/${projectId}/scope`, body),
			triggers: (projectId: UUID) => get<AssetTrigger[]>(`/projects/${projectId}/asset-triggers`),
			createTrigger: (projectId: UUID, body: Omit<AssetTrigger, 'id' | 'project_id' | 'fires' | 'created_at'>) =>
				post<AssetTrigger>(`/projects/${projectId}/asset-triggers`, body),
			updateTrigger: (id: UUID, body: Omit<AssetTrigger, 'id' | 'project_id' | 'fires' | 'created_at'>) =>
				request<AssetTrigger>(fetchFn, 'PUT', `/asset-triggers/${id}`, body),
			removeTrigger: (id: UUID) => del(`/asset-triggers/${id}`),
			fires: (projectId: UUID, status?: string) => get<AssetTriggerFire[]>(`/projects/${projectId}/asset-trigger-fires${status ? `?status=${status}` : ''}`),
			approve: (id: UUID) => post<{ status: string; reason: string }>(`/asset-trigger-fires/${id}/approve`),
			skip: (id: UUID) => post<{ status: string }>(`/asset-trigger-fires/${id}/skip`),
			linkKinds: () => get<AssetLinkKind[]>('/assets/link-kinds'),
			/** A project's relationships between confirmed assets, newest first; `counts` per kind ignore `kind`. */
			links: (projectId: UUID, params: { kind?: string; q?: string; type?: string; asset?: UUID; limit?: number } = {}) => {
				const q = new URLSearchParams();
				for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') q.set(k, String(v));
				return get<{ items: AssetLink[]; total: number; counts: Record<string, number> }>(`/projects/${projectId}/asset-links?${q}`);
			},
			/** `out`: this asset → the other; `in`: the other → this one. */
			link: (id: UUID, body: { kind: string; to_id: UUID; direction?: 'out' | 'in'; note?: string; fields?: Record<string, unknown> }) =>
				post<AssetRelation>(`/assets/${id}/links`, body),
			updateLink: (id: UUID, body: { note?: string; fields?: Record<string, unknown> }) => patch<AssetLink>(`/asset-links/${id}`, body),
			unlink: (id: UUID) => del(`/asset-links/${id}`)
		},
		people: {
			team: () => get<PersonBrief[]>('/people'),
			get: (username: string) => get<Person>(`/people/${encodeURIComponent(username)}`)
		},
		search: (q: string, limit = 6) => get<{ query: string; groups: SearchGroup[]; note?: string }>(`/search?q=${encodeURIComponent(q)}&limit=${limit}`),
		notes: {
			list: (projectId: UUID) => get<ProjectNote[]>(`/projects/${projectId}/notes`),
			create: (projectId: UUID, body: { title: string; body: string; color?: string }) => post<ProjectNote>(`/projects/${projectId}/notes`, body),
			update: (id: UUID, body: { title?: string; body?: string; color?: string }) => patch<ProjectNote>(`/notes/${id}`, body),
			remove: (id: UUID) => del(`/notes/${id}`),
			history: (id: UUID) => get<NoteVersion[]>(`/notes/${id}/history`),
			restore: (id: UUID, index: number) => post<ProjectNote>(`/notes/${id}/restore/${index}`),
			order: (projectId: UUID, ids: UUID[]) => post<ProjectNote[]>(`/projects/${projectId}/notes/order`, { ids }),
			/** An image for a note: stored on the project (not a card); embed its download URL. */
			uploadImage: (projectId: UUID, file: File) => {
				const form = new FormData();
				form.append('file', file);
				return post<Attachment>(`/projects/${projectId}/attachments`, form);
			}
		},
		posts: {
			list: (projectId: UUID, tag?: string) => get<ProjectPost[]>(`/projects/${projectId}/posts${tag ? `?tag=${encodeURIComponent(tag)}` : ''}`),
			/** Hashtags the project's posts use, most used first. */
			tags: (projectId: UUID) => get<{ tag: string; count: number }[]>(`/projects/${projectId}/post-tags`),
			create: (projectId: UUID, body: string) => post<ProjectPost>(`/projects/${projectId}/posts`, { body }),
			update: (id: UUID, body: string) => patch<ProjectPost>(`/posts/${id}`, { body }),
			remove: (id: UUID) => del(`/posts/${id}`),
			/** Onto a card of the project, where it becomes a comment. */
			move: (id: UUID, toTaskId: UUID) => post<MoveResult>(`/posts/${id}/move`, { to_task_id: toTaskId })
		},
		dashboard: {
			summary: (projectId?: string | null) => get<DashboardSummary>(`/dashboard/summary${projectId ? `?project_id=${projectId}` : ''}`),
			feed: (params: { project_id?: string | null; kinds?: FeedKind[]; mine?: boolean; actor?: string | null; before?: number | null; limit?: number; tag?: string | null } = {}) => {
				const q = new URLSearchParams();
				if (params.project_id) q.set('project_id', params.project_id);
				if (params.actor) q.set('actor', params.actor);
				if (params.mine) q.set('mine', 'true');
				if (params.kinds?.length) q.set('kinds', params.kinds.join(','));
				if (params.before) q.set('before', String(params.before));
				if (params.limit) q.set('limit', String(params.limit));
				if (params.tag) q.set('tag', params.tag);
				return get<{ items: FeedItem[]; next_before: number | null }>(`/dashboard/feed?${q}`);
			}
		},
		activity: {
			recent: (limit = 50) => get<Activity[]>(`/activity?limit=${limit}`)
		},
		ai: {
			status: () => get<{ enabled: boolean; resolver: boolean }>('/ai/status'),
			kinds: () => get<AiKind[]>('/ai/kinds'),
			providers: () => get<AiProvider[]>('/ai/providers'),
			createProvider: (body: AiProviderIn) => post<AiProvider>('/ai/providers', body),
			updateProvider: (id: UUID, body: AiProviderIn) => request<AiProvider>(fetchFn, 'PUT', `/ai/providers/${id}`, body),
			removeProvider: (id: UUID) => del(`/ai/providers/${id}`),
			/** One short request with these settings (id: a saved provider's secrets fill blanks). */
			test: (body: AiProviderIn & { model: string; id?: UUID }) => post<{ ok: boolean; text?: string; ms?: number; error?: string }>('/ai/providers/test', body),
			models: (body: AiProviderIn & { id?: UUID }) => post<{ models: string[]; error?: string }>('/ai/providers/models', body),
			usage: () => get<{ calls: AiCall[]; calls_today: number; input_tokens_today: number; output_tokens_today: number }>('/ai/usage'),
			generate: (kind: 'command' | 'tool' | 'playbook' | 'variable_kind', prompt: string, current = '') =>
				post<AiDraft>('/ai/generate', { kind, prompt, current }),
			regex: (sample: string, want: string) =>
				post<{ options: Record<string, unknown>; preview: string[]; count: number; explanation: string; model: string }>('/ai/regex', { sample, want }),
			explain: (jobId: UUID) => post<{ text: string; model: string }>(`/ai/explain/${jobId}`)
		},
		alerts: {
			presets: () => get<AlertPresets>('/alert-presets'),
			channels: () => get<AlertChannel[]>('/alert-channels'),
			/** Names to pick from on a card or run (anyone). */
			brief: () => get<{ id: UUID; name: string; kind: string }[]>('/alert-channels/brief'),
			create: (body: AlertChannelIn) => post<AlertChannel>('/alert-channels', body),
			update: (id: UUID, body: AlertChannelIn) => request<AlertChannel>(fetchFn, 'PUT', `/alert-channels/${id}`, body),
			remove: (id: UUID) => del(`/alert-channels/${id}`),
			test: (id: UUID) => post<AlertTest>(`/alert-channels/${id}/test`),
			/** Try settings before saving (id: an existing channel's stored secrets fill blanks). */
			testDraft: (body: AlertChannelIn & { id?: UUID }) => post<AlertTest>('/alert-channels/test', body),
			deliveries: (id: UUID) => get<AlertDelivery[]>(`/alert-channels/${id}/deliveries`)
		},
		settings: {
			get: () => get<AppSettings>('/settings'),
			update: (changes: Partial<AppSettings>) => patch<AppSettings>('/settings', changes)
		},
		transfer: {
			/** Upload an archive (or a library .yaml), or re-preview a staged one with a passphrase. */
			preview: (source: { file: File } | { token: string; filename: string }, passphrase?: string) => {
				const form = new FormData();
				if ('file' in source) form.append('file', source.file);
				else {
					form.append('token', source.token);
					form.append('filename', source.filename);
				}
				if (passphrase) form.append('passphrase', passphrase);
				return post<ImportPreview>('/import/preview', form);
			},
			run: (body: { token: string; filename: string; choices: Record<string, ImportChoice>; passphrase?: string; create_users?: boolean }) =>
				post<ImportResult>('/import', body),
			history: () => get<TransferEvent[]>('/transfer/history')
		}
	};
}

export interface AlertField {
	name: string;
	label: string;
	type: 'text' | 'url' | 'password' | 'textarea' | 'select' | 'number';
	secret: boolean;
	required: boolean;
	placeholder: string;
	help: string;
	options: string[];
	default: string;
}

export interface AlertPreset {
	kind: string;
	label: string;
	icon: string;
	help: string;
	fields: AlertField[];
	/** The request it makes, to start a custom webhook from (null for the webhook itself). */
	template: { method: string; content_type: string; body: string; headers: string } | null;
}

export interface AlertPresets {
	presets: AlertPreset[];
	events: Record<string, string>;
	variables: Record<string, string>;
}

export interface AlertDelivery {
	id: UUID;
	event: string;
	title: string;
	ok: boolean;
	status: number | null;
	error: string;
	created_at: string;
}

export interface AlertChannel {
	id: UUID;
	name: string;
	kind: string;
	enabled: boolean;
	fields: Record<string, string>;
	/** Which secret fields hold a value (values never come back). */
	secrets: Record<string, boolean>;
	events: string[];
	long_minutes: number | null;
	last: AlertDelivery | null;
	failures_24h: number;
}

export interface AlertChannelIn {
	name: string;
	kind: string;
	enabled?: boolean;
	fields: Record<string, string>;
	events: string[];
	long_minutes: number | null;
}

export interface AlertTest {
	ok: boolean;
	status: number | null;
	error: string;
}

/** A card's (or a run's) alert settings. */
export interface AlertRules {
	channels: UUID[];
	on: 'finish' | 'failure' | 'none';
	long_minutes: number | null;
	decisions: boolean;
	/** People told in Castor (and on their desktop) when it finishes. */
	people?: UUID[];
}

/** Instance-wide settings (Settings → General). */
export interface AppSettings {
	/** Pinned notes a project can have (1–12). */
	notes_per_project: number;
	/** LLM models per purpose ("default", "resolver"), in order: later ones are fallbacks. */
	ai_chains: Record<string, { provider: UUID; model: string }[]>;
	/** Signing in with a password; off means single sign-on only (while a provider is on). */
	password_login: boolean;
	/** The theme for everyone who hasn't picked one, and the sign-in page ("" for Castor's own). */
	default_theme: string;
}

/** People put together: @slug mentions them all; a project shared with it is theirs to see. */
export interface Group {
	id: UUID;
	slug: string;
	name: string;
	description: string;
	/** A sign-on provider group this follows at each SSO sign-in; empty: by hand only. */
	sso_group: string;
	member_count: number;
	project_count: number;
	member_ids: UUID[];
}

export interface GroupDetail extends Group {
	members: UserBrief[];
}

export type GroupIn = Pick<Group, 'slug' | 'name' | 'description' | 'sso_group'>;

/** An OpenID Connect provider people can sign in with (Settings → Sign-on). */
export interface SsoProvider {
	id: string;
	name: string;
	issuer: string;
	client_id: string;
	has_secret: boolean;
	scopes: string;
	enabled: boolean;
	default_role: UserRole;
	allowed_domains: string[];
	allowed_groups: string[];
	groups_claim: string;
	role_map: { group: string; role: UserRole }[];
	link_by_email: boolean;
	/** Register this at the provider as the redirect URI. */
	redirect_uri: string;
	/** People who have signed in through it. */
	users: number;
}

export type SsoProviderIn = Omit<SsoProvider, 'has_secret' | 'redirect_uri' | 'users'> & { client_secret?: string };

export interface AiField {
	name: string;
	label: string;
	type: 'text' | 'url' | 'password' | 'textarea';
	secret: boolean;
	required: boolean;
	placeholder: string;
	help: string;
	default: string;
}

export interface AiKind {
	kind: string;
	label: string;
	help: string;
	/** Usual models (List models asks the provider). */
	models: string[];
	fields: AiField[];
}

export interface AiProvider {
	id: UUID;
	name: string;
	kind: string;
	enabled: boolean;
	fields: Record<string, string>;
	secrets: Record<string, boolean>;
}

/** A theme for the web UI (docs/THEME_SPEC.md), compiled by the server; Castor's own look is
 *  `id: ''` and comes from lib/themes.svelte.ts, not the API. */
export interface Theme {
	id: string;
	origin: 'builtin' | 'user' | 'castor';
	name: string;
	description: string;
	modes: 'both' | 'dark' | 'light';
	colour: 'palette' | 'accent' | 'fixed';
	default_colour: string;
	preview: string[];
	css: string;
	stylesheets: string[];
	source_yaml: string;
	version: number;
	used_by: number;
	spec: Record<string, unknown>;
	/** Text roles below 4.5:1 on their surfaces, worst first (server theme_contrast.py). */
	contrast?: ThemeContrast[];
}

export interface ThemeContrast {
	mode: 'light' | 'dark';
	text: string;
	surface: string;
	ratio: number;
	message: string;
}

/** A plugin's action (castor-plugin.yaml `actions`). */
export interface PluginActionSpec {
	id: string;
	label: string;
	description: string;
	icon: string;
	on: ('project' | 'card' | 'asset')[];
	asset_types: string[];
	file: { accept: string[]; max_mb: number; required: boolean } | null;
	timeout_minutes: number;
}

export interface PluginSettingSpec {
	name: string;
	label: string;
	description: string;
	type: 'text' | 'number' | 'bool' | 'select' | 'secret';
	default: unknown;
	options: string[];
	required: boolean;
}

export interface ScopeDetail {
	name: string;
	label: string;
	description: string;
}

/** An installed plugin (Settings → Plugins; docs/PLUGINS.md). */
export interface Plugin {
	id: string;
	name: string;
	version: string;
	publisher: string;
	description: string;
	homepage: string;
	license: string;
	runtime: 'sandboxed' | 'unrestricted';
	source: string;
	checksum: string;
	enabled: boolean;
	all_projects: boolean;
	project_ids: UUID[];
	status: 'stopped' | 'starting' | 'running' | 'crashed' | 'error';
	status_message: string;
	running: boolean;
	granted: { permissions?: string[]; network?: string[]; events?: string[] };
	permission_details: ScopeDetail[];
	actions: PluginActionSpec[];
	settings_spec: PluginSettingSpec[];
	settings: Record<string, unknown>;
	secrets_set: string[];
	missing_settings: string[];
	account: { id: UUID; username: string } | null;
	installed_by: string | null;
	installed_at: string;
	updated_at: string;
}

/** What a plugin release is and asks for, before it's installed. */
export interface PluginPreview {
	token: string;
	source: string;
	checksum: string;
	size: number;
	files: number;
	manifest: {
		id: string;
		name: string;
		version: string;
		publisher: string;
		description: string;
		homepage: string;
		license: string;
		runtime: 'sandboxed' | 'unrestricted';
		permissions: string[];
		permission_details: ScopeDetail[];
		network: string[];
		events: string[];
		actions: PluginActionSpec[];
		settings: PluginSettingSpec[];
	};
	/** What this Castor can't do yet: installing is refused while there are any. */
	problems: string[];
	existing: { version: string; same: boolean } | null;
	/** For an update: what it asks for that wasn't approved before. */
	new_permissions: Record<string, string[]>;
}

export interface ProjectPluginAction extends PluginActionSpec {
	plugin: { id: string; name: string; status: Plugin['status'] };
}

export interface PluginRun {
	id: UUID;
	plugin_id: string;
	plugin_name: string | null;
	action: string;
	label: string | null;
	project_id: UUID | null;
	task_id: UUID | null;
	asset_id: UUID | null;
	status: 'running' | 'succeeded' | 'failed';
	message: string;
	progress: { done?: number; total?: number; text?: string };
	file_name: string;
	file_size: number;
	started_at: string | null;
	finished_at: string | null;
}

export interface PluginLogEntry {
	id: number;
	level: 'debug' | 'info' | 'warn' | 'error';
	message: string;
	at: string;
	run_id: UUID | null;
}

/** What the browser needs to show the instance's default theme ("" id: Castor's own). */
export type DefaultTheme = Pick<Theme, 'id' | 'modes' | 'colour' | 'css' | 'stylesheets'>;

export interface AiProviderIn {
	name: string;
	kind: string;
	enabled?: boolean;
	fields: Record<string, string>;
}

export interface AiCall {
	id: UUID;
	purpose: string;
	provider_name: string;
	model: string;
	ok: boolean;
	error: string;
	input_tokens: number | null;
	output_tokens: number | null;
	ms: number;
	created_at: string;
}

export interface AiSuggestion {
	action: ResolveAction | 'ask';
	/** A corrected command, if it would change it. */
	command: string;
	reason: string;
	confidence: number;
	model: string;
	at: string;
	/** Why the resolver didn't act on it by itself. */
	note?: string;
}

export interface AiDraft {
	yaml: string;
	notes: string;
	/** Validation problems left after repairs. */
	problems: string[];
	model: string;
	spec: Record<string, unknown> | null;
}

export type ImportChoice = 'import' | 'skip' | 'replace' | 'copy';

export interface ImportItem {
	key: string;
	kind: 'parser' | 'command' | 'tool' | 'playbook' | 'variable_kind' | 'variable_set' | 'project';
	id: string;
	name: string;
	/** new; exists (yours); builtin; same (identical, nothing to do). */
	state: 'new' | 'exists' | 'builtin' | 'same';
	choices: ImportChoice[];
	default: ImportChoice;
	/** The id, name or codename "keep both" would use. */
	new_name: string;
	notes: string[];
}

export interface ImportPreview {
	token: string;
	filename: string;
	manifest: { format: number; castor_version?: string; created_at?: string; created_by?: string; scope?: string; contents?: Record<string, number>; includes?: { jobs: boolean; activity: boolean } };
	secrets: { mode: 'none' | 'passphrase'; unlocked: boolean };
	items: ImportItem[];
	people: { matched: string[]; missing: { username: string; display_name: string }[]; can_create: boolean };
	warnings: string[];
}

export interface ImportResult {
	imported: Record<string, string[]>;
	skipped: number;
	created_users: string[];
	projects: UUID[];
}

export interface ExportRequest {
	everything?: boolean;
	parsers?: string[];
	commands?: string[];
	tools?: string[];
	playbooks?: string[];
	themes?: string[];
	variable_kinds?: string[];
	variable_sets?: UUID[];
	projects?: UUID[];
	jobs?: boolean;
	activity?: boolean;
	passphrase?: string;
	label?: string;
}

export interface TransferEvent {
	id: number;
	type: 'transfer.export' | 'transfer.import';
	message: string;
	actor_name: string;
	created_at: string;
	payload: { file?: string; secrets?: string };
}

/** Ask for an archive and save it (the server streams a zip with its file name). */
export async function downloadExport(body: ExportRequest): Promise<string> {
	const res = await fetch('/api/export', {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(body),
		credentials: 'same-origin'
	});
	if (!res.ok) {
		let message = res.statusText;
		try {
			const data = await res.json();
			if (typeof data.detail === 'string') message = data.detail;
		} catch {
			/* no body */
		}
		throw new ApiError(res.status, message);
	}
	const name = /filename="?([^";]+)"?/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? 'castor-export.castor.zip';
	saveBlob(await res.blob(), name);
	return name;
}

/** Save text or a blob as a file. */
export function saveBlob(data: Blob | string, filename: string, type = 'application/yaml') {
	const blob = typeof data === 'string' ? new Blob([data], { type }) : data;
	const url = URL.createObjectURL(blob);
	const a = document.createElement('a');
	a.href = url;
	a.download = filename;
	document.body.append(a);
	a.click();
	a.remove();
	setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type Api = ReturnType<typeof api>;
