<script lang="ts">
	import Icon from './Icon.svelte';
	interface Props {
		checked?: boolean;
		label?: string;
		disabled?: boolean;
		onchange?: (checked: boolean) => void;
	}
	let { checked = $bindable(false), label, disabled, onchange }: Props = $props();
</script>

<label class="inline-flex cursor-pointer items-center gap-3 select-none {disabled ? 'opacity-40' : ''}">
	<button
		type="button"
		role="switch"
		aria-checked={checked}
		{disabled}
		class="focus-ring relative h-8 w-13 shrink-0 rounded-full border-2 transition-colors {checked
			? 'border-primary bg-primary'
			: 'border-outline bg-surface-container-highest'}"
		onclick={() => {
			checked = !checked;
			onchange?.(checked);
		}}
	>
		<span
			class="absolute top-1/2 flex -translate-y-1/2 items-center justify-center rounded-full shadow-e1 transition-all {checked
				? 'left-[22px] size-6 bg-on-primary text-on-primary-container'
				: 'left-1 size-4 bg-outline'}"
		>
			{#if checked}<Icon name="check" size={16} />{/if}
		</span>
	</button>
	{#if label}<span class="body-medium text-on-surface">{label}</span>{/if}
</label>
