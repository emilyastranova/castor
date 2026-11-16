<script lang="ts">
	import { splitSpoilers } from '$lib/spoilers';

	/** Plain text (a card's title…) with its `||spoilers||` as bars until clicked (lib/spoilers.ts). */
	let { text }: { text: string | null | undefined } = $props();
	let pieces = $derived(splitSpoilers(text));
</script>

{#each pieces as p, i (i)}{#if p.spoiler}<span class="spoiler" tabindex="0" role="button" aria-label="Spoiler: press to show">{p.text}</span>{:else}{p.text}{/if}{/each}
