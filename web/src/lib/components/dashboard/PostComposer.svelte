<script lang="ts">
	import { api, ApiError } from '$lib/api';
	import { Button, TextField } from '$lib/m3';
	import { MentionSuggest } from '$lib/mentionSuggest.svelte';
	import { PastedImages } from '$lib/pastedImages.svelte';
	import { people } from '$lib/people.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import MentionMenu from '../MentionMenu.svelte';
	import NameImageDialog from '../NameImageDialog.svelte';

	/** Writing a post on a project's timeline (or editing one): markdown, @mentions, and
	 *  pasted screenshots, which are stored on the project rather than on a card. */
	interface Props {
		projectId: string;
		/** Editing: the post's text so far. */
		initial?: string;
		label?: string;
		submitLabel?: string;
		/** Save the text; throw to keep the box as it is. */
		onsubmit: (body: string) => Promise<void>;
		oncancel?: () => void;
		autofocus?: boolean;
	}
	let { projectId, initial = '', label = 'Post to the timeline', submitLabel = 'Post', onsubmit, oncancel, autofocus = false }: Props = $props();

	// svelte-ignore state_referenced_locally
	let body = $state(initial);
	let saving = $state(false);
	const target = { get: () => body, set: (v: string) => (body = v) };
	const suggest = new MentionSuggest(() => people.mentionables(projectId), target);
	const images = new PastedImages(async (file) => api().attachments.url((await api().notes.uploadImage(projectId, file)).id, true));

	let canSubmit = $derived(!!body.trim() && !saving && images.uploading === 0);

	async function submit() {
		if (!canSubmit) return;
		saving = true;
		try {
			await onsubmit(body);
			body = '';
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : "Couldn't save the post");
		} finally {
			saving = false;
		}
	}
	function keys(e: KeyboardEvent) {
		if (suggest.onkeydown(e)) return;
		if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
			e.preventDefault();
			void submit();
		} else if (e.key === 'Escape' && oncancel) {
			e.preventDefault();
			oncancel();
		}
	}
</script>

<div class="flex flex-col gap-2">
	<div class="relative">
		<TextField
			{label}
			bind:value={body}
			multiline
			markdown
			rows={initial ? 4 : 2}
			{autofocus}
			supporting="Markdown · paste a screenshot to add it · @ to mention · Ctrl+Enter to {submitLabel.toLowerCase()}{oncancel ? ' · Esc to cancel' : ''}"
			oninput={suggest.oninput}
			onkeydown={keys}
			onblur={suggest.close}
			onpaste={images.pasteInto(target)}
		/>
		<MentionMenu {suggest} />
	</div>
	{#if body.trim() || oncancel || images.uploading > 0}
		<div class="flex items-center justify-end gap-2">
			{#if images.uploading > 0}<span class="mr-auto body-small text-on-surface-variant">Uploading image…</span>{/if}
			{#if oncancel}<Button variant="text" onclick={oncancel}>Cancel</Button>{/if}
			<Button icon={initial ? 'check' : 'send'} loading={saving} disabled={!canSubmit} onclick={submit}>{submitLabel}</Button>
		</div>
	{/if}
</div>

{#if images.pending[0]}
	{#key images.pending[0]}
		<NameImageDialog file={images.pending[0].file} remaining={images.pending.length - 1} onconfirm={(name) => images.confirm(name)} oncancel={() => images.cancel()} />
	{/key}
{/if}
