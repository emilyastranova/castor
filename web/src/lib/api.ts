/** Typed client for the Castor API. Works in the browser and in SvelteKit load functions. */

export type UUID = string;

export interface User {
	id: UUID;
	username: string;
	display_name: string;
	is_admin: boolean;
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
	command: string;
	image: string | null;
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
	on_failure?: 'stop' | 'continue' | 'ask';
	expect?: { outputs?: Record<string, Record<string, unknown>>; log?: Record<string, unknown> } | null;
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
export interface PlaybookStepResult {
	status?: 'succeeded' | 'failed' | 'skipped' | 'not_taken' | 'attention';
	outputs?: Record<string, unknown>;
	type?: string;
	count?: number;
	input_count?: number;
	error?: string;
	reason?: string;
	finished_at?: string;
	/** The job this result judges (expect, or a decision); a newer job supersedes it. */
	job_id?: string;
	condition?: { met: boolean; checks: ConditionCheck[] };
	expect?: ConditionCheck[];
	resolution?: { action: ResolveAction; by: string; note: string; reason: string; at: string };
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
	tasks: Task[];
}

export interface OutputSpec {
	name: string;
	type: string | null;
	parser: string | null;
	/** Path or glob under /work; null means the parser reads stdout. */
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
	container_flags?: string[];
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
}

/** 422 detail for invalid command arguments. */
export interface ArgErrors {
	message: string;
	errors: Record<string, string>;
}

export interface Task {
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
	container_flags?: string[];
	agent_selector: { agent_id?: UUID; tags?: string[] };
	run_on_move: boolean;
	timeout_s: number | null;
	priority: number;
	tags: string[];
	assignees: string[];
	custom_fields: Record<string, unknown>;
	due_at: string | null;
	created_by: UUID | null;
	playbook_run_id?: UUID | null;
	playbook_step_id?: string | null;
	depends_on?: string[];
	/** Variable slot → set id. */
	var_bindings?: Record<string, UUID>;
	created_at: string;
	updated_at: string;
	latest_job: Job | null;
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
	body: string;
	created_at: string;
	updated_at: string;
	/** Set when the body was changed after posting. */
	edited_at: string | null;
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
			me: () => get<User>('/auth/me')
		},
		clients: {
			list: () => get<Client[]>('/clients'),
			create: (body: { name: string; notes?: string }) => post<Client>('/clients', body)
		},
		projects: {
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
			get: (id: UUID) => get<Task>(`/tasks/${id}`),
			update: (id: UUID, body: Partial<Task>) => patch<Task>(`/tasks/${id}`, body),
			remove: (id: UUID) => del(`/tasks/${id}`),
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
			artifacts: (id: UUID) => get<Attachment[]>(`/jobs/${id}/artifacts`),
			outputs: (id: UUID) => get<JobOutput[]>(`/jobs/${id}/outputs`),
			parse: (id: UUID) => post<JobOutput[]>(`/jobs/${id}/outputs/parse`)
		},
		parsers: {
			list: () => get<ParserInfo[]>('/parsers'),
			test: (name: string, input: string, options: Record<string, unknown>) =>
				post<ParseTestResult>(`/parsers/${encodeURIComponent(name)}/test`, { input, options })
		},
		playbooks: {
			list: () => get<PlaybookDefinition[]>('/playbooks'),
			get: (id: string) => get<PlaybookDefinition>(`/playbooks/${id}`),
			create: (yaml: string) => post<PlaybookDefinition>('/playbooks', { yaml }),
			update: (id: string, yaml: string) =>
				request<PlaybookDefinition>(fetchFn, 'PUT', `/playbooks/${id}`, { yaml }),
			remove: (id: string) => del(`/playbooks/${id}`),
			run: (id: string, projectId: UUID, inputs: Record<string, unknown> = {}) =>
				post<PlaybookRun>(`/playbooks/${id}/run`, { project_id: projectId, inputs })
		},
		playbookRuns: {
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
				post<PlaybookRun>(`/playbook-runs/${id}/steps/${stepId}/resolve`, { action, note })
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
		activity: {
			recent: (limit = 50) => get<Activity[]>(`/activity?limit=${limit}`)
		}
	};
}

export type Api = ReturnType<typeof api>;
