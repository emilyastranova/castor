<script lang="ts">
	interface Props {
		variant?: 'linear' | 'circular';
		value?: number | null; // 0..1, null = indeterminate
		size?: number;
		class?: string;
	}
	let { variant = 'linear', value = null, size = 24, class: klass = '' }: Props = $props();
</script>

{#if variant === 'linear'}
	<div class="relative h-1 w-full overflow-hidden rounded-full bg-secondary-container {klass}" role="progressbar" aria-valuenow={value ?? undefined}>
		{#if value === null}
			<div class="absolute inset-y-0 w-1/3 animate-[indeterminate_1.4s_infinite] rounded-full bg-primary"></div>
		{:else}
			<div class="h-full rounded-full bg-primary transition-[width]" style:width="{Math.round(value * 100)}%"></div>
		{/if}
	</div>
{:else}
	<span
		class="inline-block animate-spin rounded-full border-[3px] border-secondary-container border-t-primary {klass}"
		style:width="{size}px"
		style:height="{size}px"
		role="progressbar"
	></span>
{/if}

<style>
	@keyframes indeterminate {
		0% { left: -35%; }
		60% { left: 100%; }
		100% { left: 100%; }
	}
</style>
