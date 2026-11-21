<script lang="ts">
	import { brand } from '$lib/brand.svelte';

	/** The company's logo (Settings → Branding): its square mark or its wordmark, with the version for
	 *  dark themes under a dark theme when there is one. Nothing when that one isn't uploaded: the
	 *  caller shows its own fallback (`brand.info?.mark_url` says whether there is one). */
	interface Props {
		kind: 'mark' | 'wordmark';
		class?: string;
		alt?: string;
	}
	let { kind, class: klass = '', alt = '' }: Props = $props();
	const light = $derived(kind === 'mark' ? brand.info?.mark_url : brand.info?.wordmark_url);
	const dark = $derived(kind === 'mark' ? brand.info?.mark_dark_url : brand.info?.wordmark_dark_url);
</script>

{#if light}
	<img src={light} {alt} class="brand-light object-contain {klass}" />
	{#if dark}<img src={dark} {alt} class="brand-dark object-contain {klass}" />{/if}
{:else if dark}
	<img src={dark} {alt} class="object-contain {klass}" />
{/if}

<style>
	.brand-dark {
		display: none;
	}
	:global(html[data-theme='dark']) .brand-dark {
		display: block;
	}
	:global(html[data-theme='dark']) .brand-light:has(+ .brand-dark) {
		display: none;
	}
	@media (prefers-color-scheme: dark) {
		:global(html:not([data-theme='light'])) .brand-dark {
			display: block;
		}
		:global(html:not([data-theme='light'])) .brand-light:has(+ .brand-dark) {
			display: none;
		}
	}
</style>
