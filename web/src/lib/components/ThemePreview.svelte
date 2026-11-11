<script lang="ts">
	import type { Theme } from '$lib/api';

	/** A small picture of a theme in its own colours: a surface with two lines of text and its
	 *  accent. A picture of the theme, so deliberately not themed itself. */
	let { theme, class: klass = '' }: { theme: Pick<Theme, 'preview' | 'spec'>; class?: string } = $props();
	let [bg, surface, ink, accent] = $derived(theme.preview);
	let square = $derived(!!(theme.spec?.shape as { square?: boolean } | undefined)?.square);
</script>

<span class="flex h-16 items-end gap-2 p-2.5 {klass}" style:background={bg} aria-hidden="true">
	<span class="flex flex-1 flex-col gap-1.5 p-2 {square ? '' : 'rounded-xs'}" style:background={surface}>
		<span class="block h-1.5 w-3/4 {square ? '' : 'rounded-full'}" style:background={ink}></span>
		<span class="block h-1.5 w-1/2 opacity-50 {square ? '' : 'rounded-full'}" style:background={ink}></span>
	</span>
	<span class="block h-5 w-10 shrink-0 {square ? '' : 'rounded-full'}" style:background={accent}></span>
</span>
