<script lang="ts">
	interface Props {
		name: string;
		/** An icon file instead of the symbol (a plugin's own): drawn as a silhouette in the text colour,
		 *  so it follows the theme and the active state like the symbols do. */
		src?: string | null;
		filled?: boolean;
		size?: number;
		class?: string;
	}
	let { name, src = null, filled = false, size = 24, class: klass = '' }: Props = $props();
</script>

{#if src}
	<span
		class="m3-icon-file inline-block shrink-0 bg-current {klass}"
		style:width="{size}px"
		style:height="{size}px"
		style:mask="url({JSON.stringify(src)}) center / contain no-repeat"
		style:-webkit-mask="url({JSON.stringify(src)}) center / contain no-repeat"
		aria-hidden="true"
	></span>
{:else}
	<span
		class="material-symbols-rounded {klass}"
		class:filled
		style:font-size="{size}px"
		style:width="{size}px"
		style:height="{size}px"
		aria-hidden="true">{name}</span
	>
{/if}
