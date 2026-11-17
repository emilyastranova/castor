<script lang="ts">
	import type { EmojiSuggest } from '$lib/emojiSuggest.svelte';
	import Emoji from './Emoji.svelte';

	/** The emoji suggestions under a text box (its parent must be `relative`). */
	let { suggest }: { suggest: EmojiSuggest } = $props();
	let matches = $derived(suggest.open ? suggest.matches : []);
</script>

{#if matches.length}
	<ul class="absolute top-full left-0 z-40 mt-1 w-64 max-w-full overflow-hidden rounded-xs bg-surface-container py-1 shadow-e2" role="listbox" aria-label="Emoji">
		{#each matches as m, i (m.emoji)}
			<li role="option" aria-selected={i === suggest.index}>
				<!-- mousedown, so the text box keeps focus and its caret -->
				<button
					type="button"
					class="flex w-full items-center gap-2.5 px-3 py-1.5 text-left {i === suggest.index ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface hover:bg-on-surface/8'}"
					onmousedown={(e) => {
						e.preventDefault();
						suggest.pick(m);
					}}
					onmouseenter={() => (suggest.index = i)}
				>
					<Emoji emoji={m.emoji} size={20} />
					<span class="min-w-0 flex-1 truncate font-mono body-small">:{m.name}:</span>
				</button>
			</li>
		{/each}
	</ul>
{/if}
