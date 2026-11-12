<script lang="ts">
	import { Icon } from '$lib/m3';

	/** A plugin's own icon (its castor-plugin.yaml `icon`), or the extension glyph when it has none.
	 *  Logos sit on a fixed white plate, as app icons do: a brand's colours are made for it, and
	 *  a dark mark (Tenable's navy) would vanish on a dark theme. */
	interface Props {
		src?: string | null;
		size?: number;
		class?: string;
	}
	let { src = null, size = 40, class: klass = '' }: Props = $props();
	let broken = $state(false);
	$effect(() => {
		void src;
		broken = false;
	});
</script>

{#if src && !broken}
	<span
		class="grid shrink-0 place-items-center overflow-hidden rounded-md bg-white ring-1 ring-outline-variant {klass}"
		style:width="{size}px"
		style:height="{size}px"
		style:padding="{Math.round(size * 0.14)}px"
	>
		<img {src} alt="" class="size-full object-contain" onerror={() => (broken = true)} />
	</span>
{:else}
	<span class="grid shrink-0 place-items-center rounded-md bg-secondary-container text-on-secondary-container {klass}" style:width="{size}px" style:height="{size}px">
		<Icon name="extension" size={Math.round(size * 0.55)} />
	</span>
{/if}
