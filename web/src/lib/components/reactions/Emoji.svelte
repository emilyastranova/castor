<script lang="ts">
	import { customEmojis } from '$lib/emojis.svelte';
	import { emojiName, isCustom } from '$lib/emoji';

	/** One emoji: Unicode as text, a custom one (":name:") as its picture. */
	let { emoji, size = 20, class: klass = '' }: { emoji: string; size?: number; class?: string } = $props();
	const url = $derived(isCustom(emoji) ? customEmojis.url(emoji) : null);
	$effect(() => {
		if (isCustom(emoji)) customEmojis.load();
	});
</script>

{#if isCustom(emoji)}
	{#if url}
		<img src={url} alt={emoji} title={emoji} class="inline-block object-contain align-[-0.2em] {klass}" style:width="{size}px" style:height="{size}px" />
	{:else if customEmojis.loaded}
		<span class="font-mono text-[0.8em] {klass}" title="{emoji} (removed)">{emoji}</span>
	{:else}
		<span class="inline-block {klass}" style:width="{size}px" style:height="{size}px" aria-label={emoji}></span>
	{/if}
{:else}
	<span class="inline-block leading-none {klass}" style:font-size="{size * 0.9}px" role="img" aria-label={emojiName(emoji)}>{emoji}</span>
{/if}
