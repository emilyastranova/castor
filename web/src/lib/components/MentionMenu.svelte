<script lang="ts">
	import type { MentionSuggest } from '$lib/mentionSuggest.svelte';
	import { Avatar } from '$lib/m3';

	/** The suggestion list under a text box (its parent must be `relative`). */
	let { suggest }: { suggest: MentionSuggest } = $props();
	let matches = $derived(suggest.open ? suggest.matches : []);
</script>

{#if matches.length}
	<ul class="absolute top-full left-0 z-40 mt-1 w-72 max-w-full overflow-hidden rounded-xs bg-surface-container py-1 shadow-e2" role="listbox" aria-label="People to mention">
		{#each matches as u, i (u.id)}
			<li role="option" aria-selected={i === suggest.index}>
				<!-- mousedown, so the text box keeps focus and its caret -->
				<button
					type="button"
					class="flex w-full items-center gap-2 px-3 py-1.5 text-left {i === suggest.index ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface hover:bg-on-surface/8'}"
					onmousedown={(e) => {
						e.preventDefault();
						suggest.pick(u);
					}}
					onmouseenter={() => (suggest.index = i)}
				>
					<Avatar name={u.display_name} src={u.avatar_url} size={24} />
					<span class="min-w-0 flex-1 truncate body-medium">{u.display_name}</span>
					<span class="shrink-0 body-small text-on-surface-variant">@{u.username}</span>
				</button>
			</li>
		{/each}
	</ul>
{/if}
