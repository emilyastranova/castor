<script lang="ts">
	interface Tab {
		id: string;
		label: string;
		icon?: string;
		badge?: number;
	}
	interface Props {
		tabs: Tab[];
		value: string;
		onchange?: (id: string) => void;
		class?: string;
	}
	let { tabs, value = $bindable(), onchange, class: klass = '' }: Props = $props();
</script>

<!-- Too many tabs for the width (phones): the row scrolls by itself, not the page. -->
<div class="flex overflow-x-auto border-b border-outline-variant [scrollbar-width:none] [&::-webkit-scrollbar]:hidden {klass}" role="tablist">
	{#each tabs as tab (tab.id)}
		<button
			type="button"
			role="tab"
			aria-selected={value === tab.id}
			class="state-layer relative flex h-12 shrink-0 items-center gap-2 px-4 title-small whitespace-nowrap transition-colors {value === tab.id ? 'text-primary' : 'text-on-surface-variant'}"
			onclick={() => {
				value = tab.id;
				onchange?.(tab.id);
			}}
		>
			{#if tab.icon}<span class="material-symbols-rounded text-[20px]">{tab.icon}</span>{/if}
			{tab.label}
			{#if tab.badge}<span class="rounded-full bg-surface-container-highest px-1.5 label-small">{tab.badge}</span>{/if}
			{#if value === tab.id}
				<span class="absolute inset-x-3 bottom-0 h-[3px] rounded-t-full bg-primary"></span>
			{/if}
		</button>
	{/each}
</div>
