<script lang="ts">
	import Icon from './Icon.svelte';

	interface Option {
		value: string;
		label: string;
	}
	interface Props {
		label: string;
		value?: string;
		options: Option[];
		supporting?: string;
		disabled?: boolean;
		class?: string;
		onchange?: (value: string) => void;
	}
	let { label, value = $bindable(''), options, supporting, disabled, class: klass = '', onchange }: Props =
		$props();
	const id = `sel-${Math.random().toString(36).slice(2, 8)}`;
</script>

<div class="flex flex-col {klass}">
	<div class="relative h-14 rounded-xs border border-outline hover:border-on-surface focus-within:border-primary focus-within:ring-1 focus-within:ring-primary">
		<label for={id} class="pointer-events-none absolute -top-2 left-3 bg-surface px-1 body-small text-on-surface-variant">{label}</label>
		<select
			{id}
			class="h-full w-full appearance-none bg-transparent pl-4 pr-10 text-on-surface body-large outline-none disabled:opacity-40"
			bind:value
			{disabled}
			onchange={(e) => onchange?.((e.currentTarget as HTMLSelectElement).value)}
		>
			{#each options as o (o.value)}
				<option value={o.value}>{o.label}</option>
			{/each}
		</select>
		<Icon name="arrow_drop_down" class="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant" />
	</div>
	{#if supporting}<p class="px-4 pt-1 body-small text-on-surface-variant">{supporting}</p>{/if}
</div>
