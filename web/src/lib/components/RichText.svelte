<script lang="ts">
	import { renderMarkdown } from '$lib/markdown';
	import { openImagesIn } from '$lib/lightbox.svelte';
	import { people, chipMentions, showMentionTip, hideMentionTip } from '$lib/people.svelte';
	import { me } from '$lib/session';
	import { goto } from '$app/navigation';
	import { chipHashtags } from '$lib/hashtags';
	import { copyableCode } from '$lib/copyCode';

	/** Rendered markdown (comments): images open in the viewer, @mentions of people on the
	 *  project show as chips. */
	let {
		text,
		projectId = null,
		class: klass = '',
		hashtags,
		activeTag = null,
		room = false
	}: {
		text: string;
		projectId?: string | null;
		class?: string;
		/** Make #hashtags links to this (timeline posts). */
		hashtags?: (tag: string) => string;
		/** The hashtag being filtered by, shown as chosen. */
		activeTag?: string | null;
		/** Keep the corner under a companion's legs clear: the text wraps around it. */
		room?: boolean;
	} = $props();

	let root = $state<HTMLDivElement>();
	/** A mention chip opens that person's page; an image opens the viewer. */
	function onclick(e: MouseEvent) {
		const chip = (e.target as HTMLElement).closest<HTMLElement>('.mention');
		if (chip?.dataset.group) {
			e.preventDefault(); // a group has no page
			return;
		}
		if (chip?.dataset.username) {
			e.preventDefault();
			e.stopPropagation();
			void goto(`/people/${chip.dataset.username.replace(/^@/, '')}`);
			return;
		}
		openImagesIn(e);
	}
	let html = $derived(renderMarkdown(text));
	let who = $derived(people.mentionables(projectId));
	$effect(() => {
		void html; // re-chip whenever the rendered text (or the people, or the chosen tag) change
		void activeTag;
		if (root) chipMentions(root, who, me()?.id);
		if (root && hashtags) chipHashtags(root, hashtags, activeTag);
	});
	// Don't leave a tooltip behind when the comment goes away (e.g. navigating from a chip).
	$effect(() => hideMentionTip);
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div
	bind:this={root}
	use:copyableCode
	class="prose-m3 {klass}"
	{onclick}
	onpointerover={(e) => {
		const chip = (e.target as HTMLElement).closest<HTMLElement>('.mention');
		if (chip) showMentionTip(chip);
	}}
	onpointerout={(e) => {
		const chip = (e.target as HTMLElement).closest('.mention');
		if (chip && !chip.contains(e.relatedTarget as Node | null)) hideMentionTip();
	}}
>
	{#if room}<span class="companion-room" aria-hidden="true"></span>{/if}
	{@html html}
</div>
