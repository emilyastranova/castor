<script lang="ts">
	import { renderMarkdown } from '$lib/markdown';
	import { openImagesIn } from '$lib/lightbox.svelte';
	import { people, chipMentions } from '$lib/people.svelte';
	import { me } from '$lib/session';

	/** Rendered markdown (comments): images open in the viewer, @mentions of people on the
	 *  project show as chips. */
	let { text, projectId = null, class: klass = '' }: { text: string; projectId?: string | null; class?: string } = $props();

	let root = $state<HTMLDivElement>();
	let html = $derived(renderMarkdown(text));
	let who = $derived(people.list(projectId));
	$effect(() => {
		void html; // re-chip whenever the rendered text (or the people) change
		if (root) chipMentions(root, who, me()?.id);
	});
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div bind:this={root} class="prose-m3 {klass}" onclick={openImagesIn}>{@html html}</div>
