/** A project's Files view: how each sort of file looks, and grouping the list (pure). */
import type { FileCategory, FileSource, ProjectFile } from '$lib/api';

export const CATEGORY: Record<FileCategory, { label: string; icon: string }> = {
	image: { label: 'Images', icon: 'image' },
	text: { label: 'Text and logs', icon: 'article' },
	data: { label: 'Data', icon: 'data_object' },
	document: { label: 'Documents', icon: 'description' },
	archive: { label: 'Archives and captures', icon: 'folder_zip' },
	other: { label: 'Other', icon: 'draft' }
};

export const SOURCE: Record<FileSource, { label: string; short: string; icon: string }> = {
	upload: { label: 'Added by people', short: 'Added', icon: 'upload_file' },
	artifact: { label: 'Collected from jobs', short: 'Artifact', icon: 'output' },
	snapshot: { label: 'Snapshots', short: 'Snapshot', icon: 'photo_camera' },
	project: { label: 'In notes and posts', short: 'Note or post', icon: 'sticky_note_2' }
};

/** The extension, for a tile without a picture ("XML"); empty when there's none. */
export function extension(name: string): string {
	const dot = name.lastIndexOf('.');
	return dot > 0 && dot > name.length - 8 ? name.slice(dot + 1).toUpperCase() : '';
}

/** The name without the folders an artifact was collected from ("artifacts/ports.json" → "ports.json"). */
export function baseName(name: string): string {
	return name.slice(name.lastIndexOf('/') + 1) || name;
}

/** Pictures the browser shows as they are (a thumbnail for the others). */
export const isPicture = (f: ProjectFile) => f.category === 'image';
/** Text Castor can show in the preview. */
export const isReadable = (f: ProjectFile) => f.category === 'text' || f.category === 'data' || f.content_type.startsWith('text/');
export const isPdf = (f: ProjectFile) => f.content_type === 'application/pdf';

/** Where a file came from, in a few words: "Recon run › Find hosts", "Notes and posts". */
export function placeOf(f: ProjectFile): string {
	if (!f.card_id) return 'Notes and posts';
	return f.step ? `${f.card_title} › ${f.step}` : (f.card_title ?? 'A card');
}

/** A link to the card a file is on (a step's link opens the step on top of its run's card). */
export function cardHref(projectId: string, f: ProjectFile): string | null {
	return f.task_id ? `/projects/${projectId}?task=${f.task_id}` : null;
}

export type Grouping = 'day' | 'card' | 'none';
export interface FileGroup {
	key: string;
	label: string;
	items: ProjectFile[];
}

function dayLabel(d: Date, now: Date): string {
	const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
	const days = Math.round((start(now) - start(d)) / 86_400_000);
	if (days === 0) return 'Today';
	if (days === 1) return 'Yesterday';
	if (days > 1 && days < 7) return d.toLocaleDateString(undefined, { weekday: 'long' });
	return d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' });
}

/** Files in groups, keeping their order: by the day they were added, by card, or one group. */
export function groupFiles(files: ProjectFile[], by: Grouping, now = new Date()): FileGroup[] {
	if (by === 'none') return files.length ? [{ key: 'all', label: '', items: files }] : [];
	const groups = new Map<string, FileGroup>();
	for (const f of files) {
		let key: string;
		let label: string;
		if (by === 'day') {
			const d = new Date(f.created_at);
			key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
			label = dayLabel(d, now);
		} else {
			key = f.card_id ?? 'none';
			label = f.card_id ? (f.card_title ?? 'A card') : 'Notes and posts';
		}
		let g = groups.get(key);
		if (!g) groups.set(key, (g = { key, label, items: [] }));
		g.items.push(f);
	}
	return [...groups.values()];
}
