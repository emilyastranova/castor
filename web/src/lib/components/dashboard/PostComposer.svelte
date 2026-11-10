<script lang="ts">
	import { api, ApiError } from '$lib/api';
	import { PastedImages } from '$lib/pastedImages.svelte';
	import { people } from '$lib/people.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { me } from '$lib/session';
	import { Avatar } from '$lib/m3';
	import MarkdownEditor from '../MarkdownEditor.svelte';
	import NameImageDialog from '../NameImageDialog.svelte';

	/** Writing a post on a project's timeline (or editing one). Pasted, dropped or picked
	 *  screenshots are stored on the project rather than on a card. */
	interface Props {
		projectId: string;
		/** Editing: the post's text so far. */
		initial?: string;
		/** Save the text; throw to keep the box as it is. */
		onsubmit: (body: string) => Promise<void>;
		oncancel?: () => void;
		autofocus?: boolean;
	}
	let { projectId, initial, onsubmit, oncancel, autofocus = false }: Props = $props();
	let editing = $derived(initial !== undefined);

	// svelte-ignore state_referenced_locally
	let body = $state(initial ?? '');
	let saving = $state(false);
	const images = new PastedImages(async (file) => api().attachments.url((await api().notes.uploadImage(projectId, file)).id, true));

	async function submit(text: string) {
		saving = true;
		try {
			await onsubmit(text);
			if (!editing) body = '';
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : "Couldn't save the post");
			throw err;
		} finally {
			saving = false;
		}
	}
</script>

<MarkdownEditor
	bind:value={body}
	label={editing ? 'Edit post' : 'Post to the timeline'}
	placeholder={editing ? undefined : 'Write a post for the timeline: notes, findings, screenshots…'}
	{projectId}
	mentionables={() => people.mentionables(projectId)}
	{images}
	submitLabel={editing ? 'Save' : 'Post'}
	submitIcon={editing ? 'check' : 'send'}
	onsubmit={submit}
	{oncancel}
	busy={saving}
	collapsible={!editing}
	{autofocus}
	minRows={editing ? 4 : 3}
	hashtags={(tag) => `/projects/${projectId}/dashboard?tag=${encodeURIComponent(tag)}`}
	draftKey={editing ? undefined : `post:${projectId}`}
>
	{#snippet leading()}
		{@const who = me()}
		{#if who}<Avatar name={who.display_name} src={who.avatar_url} size={32} />{/if}
	{/snippet}
</MarkdownEditor>

{#if images.pending[0]}
	{#key images.pending[0]}
		<NameImageDialog file={images.pending[0].file} remaining={images.pending.length - 1} onconfirm={(name) => images.confirm(name)} oncancel={() => images.cancel()} />
	{/key}
{/if}
