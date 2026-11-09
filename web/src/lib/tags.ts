import { api } from '$lib/api';

/** A tag in use, offered as people type one (m3/TagInput). */
export interface TagSuggestion {
	tag: string;
	/** How often it's used, shown next to it ("12 cards"). */
	count?: number;
	/** Uses in the current context (this project), ranked first. */
	here?: number;
}

export type TagKind = 'cards' | 'agents' | 'library';

/** Tags in use of a kind, most used first; [] if they can't be read (no permission). */
export async function loadTags(kind: TagKind, projectId?: string): Promise<TagSuggestion[]> {
	try {
		const client = api();
		return kind === 'cards' ? await client.tags.cards(projectId) : kind === 'agents' ? await client.tags.agents() : await client.tags.library();
	} catch {
		return [];
	}
}
