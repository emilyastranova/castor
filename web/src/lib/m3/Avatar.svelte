<script lang="ts">
	import { initials } from '$lib/format';
	interface Props {
		name: string;
		size?: number;
		tone?: 'primary' | 'tertiary' | 'secondary';
		icon?: string;
		/** A picture; initials show while it's missing or if it fails to load. */
		src?: string | null;
	}
	let { name, size = 32, tone = 'primary', icon, src = null }: Props = $props();
	const tones = {
		primary: 'bg-primary-container text-on-primary-container',
		secondary: 'bg-secondary-container text-on-secondary-container',
		tertiary: 'bg-tertiary-container text-on-tertiary-container'
	};
	let failed = $state<string | null>(null);
	let showImage = $derived(!!src && failed !== src);
</script>

<span
	class="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full label-medium {tones[tone]}"
	style:width="{size}px"
	style:height="{size}px"
	style:font-size={size >= 56 ? `${Math.round(size * 0.36)}px` : undefined}
	title={name}
>
	{#if showImage}
		<img src={src} alt="" class="size-full object-cover" referrerpolicy="no-referrer" loading="lazy" onerror={() => (failed = src)} />
	{:else if icon}<span class="material-symbols-rounded" style:font-size="{size * 0.6}px">{icon}</span>{:else}{initials(name)}{/if}
</span>
