<script lang="ts" module>
	/** Sticky-note colors (the server's api/notes.COLORS); shades come from app.css --note-*. */
	export const NOTE_COLORS = [
		{ name: 'default', label: 'Default' },
		{ name: 'red', label: 'Red' },
		{ name: 'orange', label: 'Orange' },
		{ name: 'yellow', label: 'Yellow' },
		{ name: 'green', label: 'Green' },
		{ name: 'teal', label: 'Teal' },
		{ name: 'blue', label: 'Blue' },
		{ name: 'navy', label: 'Navy' },
		{ name: 'purple', label: 'Purple' },
		{ name: 'pink', label: 'Pink' },
		{ name: 'brown', label: 'Brown' },
		{ name: 'gray', label: 'Gray' }
	] as const;

	export const noteBg = (color: string | null | undefined) => `var(--note-${color || 'default'}, var(--note-default))`;
</script>

<script lang="ts">
	import { Icon } from '$lib/m3';

	let { value = 'default', onpick, size = 28 }: { value?: string; onpick: (color: string) => void; size?: number } = $props();
</script>

<div class="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Note color">
	{#each NOTE_COLORS as c (c.name)}
		<button
			type="button"
			role="radio"
			aria-checked={value === c.name}
			aria-label={c.label}
			title={c.label}
			class="focus-ring flex shrink-0 items-center justify-center rounded-full border transition-transform hover:scale-110 {value === c.name ? 'border-2 border-primary' : 'border-outline-variant'}"
			style:width="{size}px"
			style:height="{size}px"
			style:background-color={noteBg(c.name)}
			onclick={(e) => {
				e.stopPropagation();
				onpick(c.name);
			}}
		>
			{#if value === c.name}
				<Icon name="check" size={16} class="text-on-surface" />
			{:else if c.name === 'default'}
				<Icon name="format_color_reset" size={16} class="text-on-surface-variant" />
			{/if}
		</button>
	{/each}
</div>
