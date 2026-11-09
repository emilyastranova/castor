<script lang="ts">
	import { renderMarkdown } from '$lib/markdown';
	import { openImagesIn } from '$lib/lightbox.svelte';
	import { people, chipMentions, showMentionTip, hideMentionTip } from '$lib/people.svelte';
	import { me } from '$lib/session';
	import { goto } from '$app/navigation';

	/** Rendered markdown (comments): images open in the viewer, @mentions of people on the
	 *  project show as chips. */
	let { text, projectId = null, class: klass = '' }: { text: string; projectId?: string | null; class?: string } = $props();

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
		void html; // re-chip whenever the rendered text (or the people) change
		if (root) chipMentions(root, who, me()?.id);
	});
	// Don't leave a tooltip behind when the comment goes away (e.g. navigating from a chip).
	$effect(() => hideMentionTip);
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div
	bind:this={root}
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
	{@html html}
</div>
