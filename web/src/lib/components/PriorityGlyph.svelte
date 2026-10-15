<script lang="ts" module>
	export const priorityLabels = ['None', 'Low', 'Medium', 'High'] as const;

	/** Options for a priority <Select>; values are the numeric levels as strings. */
	export const priorityOptions = priorityLabels.map((label, level) => ({ value: String(level), label }));

	/** Colour of the filled bars for each level (index 0 = none, nothing filled). */
	export const priorityColor = [
		'var(--md-sys-color-outline-variant)',
		'var(--md-custom-color-blue)',
		'var(--md-custom-color-amber)',
		'var(--md-sys-color-error)'
	];
</script>

<script lang="ts">
	interface Props {
		level: number | string | null | undefined;
		size?: number;
		class?: string;
	}
	let { level, size = 16, class: klass = '' }: Props = $props();

	let n = $derived(Math.max(0, Math.min(3, Number(level) || 0)));
	const bars = [
		{ x: 1, y: 8.5, h: 6.5 },
		{ x: 6, y: 5, h: 10 },
		{ x: 11, y: 1, h: 14 }
	];
</script>

<!-- Signal bars that fill with the level: none (all faint) → low (1) → medium (2) → high (3). -->
<svg class="shrink-0 {klass}" width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
	{#each bars as bar, i (i)}
		<rect
			x={bar.x}
			y={bar.y}
			width="4"
			height={bar.h}
			rx="1.25"
			fill={i < n ? priorityColor[n] : 'var(--md-sys-color-outline-variant)'}
		/>
	{/each}
</svg>
