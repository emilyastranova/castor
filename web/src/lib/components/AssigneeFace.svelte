<script lang="ts">
	import { Avatar, Icon } from '$lib/m3';
	import type { Mentionable } from '$lib/people.svelte';

	/** One assignee's face on a card: a person's avatar, or a group's badge. */
	let { who, size = 20 }: { who: Mentionable; size?: number } = $props();
</script>

{#if who.group}
	<span class="flex items-center justify-center rounded-full bg-secondary-container text-on-secondary-container" style="width:{size}px;height:{size}px"><Icon name="groups" size={Math.round(size * 0.7)} /></span>
{:else}
	<Avatar name={who.display_name} src={who.avatar_url} {size} />
{/if}
