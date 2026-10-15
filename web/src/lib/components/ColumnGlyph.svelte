<script lang="ts" module>
	import type { ColumnRole } from '$lib/api';

	/** Colour for each column role; columns without a role get a neutral dot. */
	export const roleColor: Record<ColumnRole | 'none', string> = {
		backlog: 'var(--md-sys-color-outline)',
		todo: 'var(--md-custom-color-blue)',
		active: 'var(--md-custom-color-amber)',
		review: 'var(--md-custom-color-violet)',
		done: 'var(--md-custom-color-green)',
		none: 'var(--md-sys-color-on-surface-variant)'
	};
</script>

<script lang="ts">
	interface Props {
		role: ColumnRole | null | undefined;
		size?: number;
		class?: string;
	}
	let { role, size = 16, class: klass = '' }: Props = $props();
	let color = $derived(roleColor[role ?? 'none']);
</script>

<!--
	A circle that fills as work progresses, so column state reads at a glance:
	backlog (dashed) → to do (empty) → in progress (half) → review (three quarters) → done (check).
-->
<svg
	class="shrink-0 {klass}"
	width={size}
	height={size}
	viewBox="0 0 16 16"
	fill="none"
	aria-hidden="true"
	style:color
>
	{#if role === 'done'}
		<circle cx="8" cy="8" r="7" fill="currentColor" />
		<path
			d="M4.9 8.2l2.1 2.1 4.1-4.4"
			stroke="var(--md-sys-color-surface-container-lowest)"
			stroke-width="1.7"
			stroke-linecap="round"
			stroke-linejoin="round"
		/>
	{:else if role === 'backlog'}
		<circle cx="8" cy="8" r="6.25" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2.1 1.9" />
	{:else if role === 'todo'}
		<circle cx="8" cy="8" r="6.25" stroke="currentColor" stroke-width="1.5" />
	{:else if role === 'active'}
		<circle cx="8" cy="8" r="6.25" stroke="currentColor" stroke-width="1.5" />
		<path d="M8 4a4 4 0 0 1 0 8z" fill="currentColor" />
	{:else if role === 'review'}
		<circle cx="8" cy="8" r="6.25" stroke="currentColor" stroke-width="1.5" />
		<path d="M8 4a4 4 0 1 1-4 4h4z" fill="currentColor" />
	{:else}
		<circle cx="8" cy="8" r="4" fill="currentColor" />
	{/if}
</svg>
