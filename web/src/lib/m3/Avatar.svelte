<script lang="ts">
	import { initials } from '$lib/format';
	interface Props {
		name: string;
		size?: number;
		tone?: 'primary' | 'tertiary' | 'secondary';
		icon?: string;
	}
	let { name, size = 32, tone = 'primary', icon }: Props = $props();
	const tones = {
		primary: 'bg-primary-container text-on-primary-container',
		secondary: 'bg-secondary-container text-on-secondary-container',
		tertiary: 'bg-tertiary-container text-on-tertiary-container'
	};
</script>

<span
	class="inline-flex shrink-0 items-center justify-center rounded-full label-medium {tones[tone]}"
	style:width="{size}px"
	style:height="{size}px"
	title={name}
>
	{#if icon}<span class="material-symbols-rounded" style:font-size="{size * 0.6}px">{icon}</span>{:else}{initials(name)}{/if}
</span>
