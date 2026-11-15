import type { Task } from '$lib/api';

/** A card being dragged on the board, so activity cards can offer to take it as a subtask
 *  (BoardColumn sets it while a drag is on; TaskCard shows its drop strip). */
class DropInto {
	dragging = $state<Task | null>(null);

	/** Whether `target` can take the dragged card as a subtask. */
	accepts(target: Task): boolean {
		const card = this.dragging;
		return !!card && card.id !== target.id && !!target.subtasks?.total && !card.subtasks?.total && !card.run && !target.run;
	}
}

export const dropInto = new DropInto();
