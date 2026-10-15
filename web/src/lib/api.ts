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

export type ColumnRole = 'backlog' | 'todo' | 'active' | 'review' | 'done';

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
	status: JobStatus;
	exit_code: number | null;
	error: string | null;
	queued_at: string;
	dispatched_at: string | null;
	started_at: string | null;
	finished_at: string | null;
	stdout_bytes: number;
	stderr_bytes: number;
}

export interface LogChunk {
	seq: number;
	stream: 'stdout' | 'stderr' | 'system';
	data: string;
	ts: string;
}

export interface JobDetail extends Job {
	logs: LogChunk[];
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
	image: string | null;
	agent_selector: { agent_id?: UUID; tags?: string[] };
	run_on_move: boolean;
	timeout_s: number | null;
	priority: number;
	tags: string[];
	assignees: string[];
	custom_fields: Record<string, unknown>;
	due_at: string | null;
	created_by: UUID | null;
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
		message: string
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
		try {
			const data = await res.json();
			message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data);
		} catch {
			/* no body */
		}
		throw new ApiError(res.status, message);
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
			cancel: (id: UUID) => post<Job>(`/jobs/${id}/cancel`)
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
