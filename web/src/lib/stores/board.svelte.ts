/** Board state for one project, kept in sync with server events. */
import { SvelteMap } from 'svelte/reactivity';
import type { Activity, Board, Column, Job, Project, Task, UUID } from '$lib/api';
import type { ServerEvent } from '$lib/realtime.svelte';

export class BoardStore {
	project = $state<Project>()!;
	columns = $state<Column[]>([]);
	tasks = new SvelteMap<UUID, Task>();
	activity = $state<Activity[]>([]);

	constructor(board: Board, activity: Activity[] = []) {
		this.load(board, activity);
	}

	load(board: Board, activity: Activity[] = []): void {
		this.project = board.project;
		this.columns = [...board.columns].sort((a, b) => a.position - b.position);
		this.tasks.clear();
		for (const t of board.tasks) this.tasks.set(t.id, t);
		this.activity = activity;
	}

	tasksIn(columnId: UUID): Task[] {
		return [...this.tasks.values()]
			.filter((t) => t.column_id === columnId)
			.sort((a, b) => a.position - b.position || a.created_at.localeCompare(b.created_at));
	}

	get taskList(): Task[] {
		return [...this.tasks.values()];
	}

	upsertTask(task: Task): void {
		if (task.playbook_step_id) return; // a run's steps live in its card, not on the board
		this.tasks.set(task.id, task);
	}

	/** Optimistic move; the server's task.moved event will confirm or correct it. */
	moveLocal(taskId: UUID, columnId: UUID, position: number): void {
		const t = this.tasks.get(taskId);
		if (t) this.tasks.set(taskId, { ...t, column_id: columnId, position });
	}

	apply(event: ServerEvent): void {
		if (event.project_id && event.project_id !== this.project.id) return;
		switch (event.name) {
			case 'task.created':
			case 'task.updated':
			case 'task.moved': {
				const task = event.data as Task;
				if (task.project_id === this.project.id && !task.playbook_step_id) this.tasks.set(task.id, task);
				break;
			}
			case 'task.deleted':
				this.tasks.delete((event.data as { id: UUID }).id);
				break;
			case 'job.updated': {
				const job = event.data as Job;
				const t = this.tasks.get(job.task_id);
				if (!t) break;
				const current = t.latest_job;
				if (!current || current.id === job.id || job.queued_at >= current.queued_at) {
					this.tasks.set(t.id, { ...t, latest_job: job });
				}
				break;
			}
			case 'column.created':
			case 'column.updated': {
				const col = event.data as Column;
				const rest = this.columns.filter((c) => c.id !== col.id);
				this.columns = [...rest, col].sort((a, b) => a.position - b.position);
				break;
			}
			case 'column.deleted':
				this.columns = this.columns.filter((c) => c.id !== (event.data as { id: UUID }).id);
				break;
			case 'project.updated':
				this.project = event.data as Project;
				break;
			case 'activity.created': {
				const a = event.data as Activity;
				if (a.project_id === this.project.id) this.activity = [a, ...this.activity].slice(0, 200);
				break;
			}
		}
	}
}
