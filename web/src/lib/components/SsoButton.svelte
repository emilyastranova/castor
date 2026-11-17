<script lang="ts">
	import type { SsoBranding } from '$lib/api';
	import { Icon } from '$lib/m3';

	/** A sign-on button as its provider's branding says: words, colour, style and logo (a Material
	 *  Symbols icon, a square logo before the words, or a horizontal wordmark instead of them). Used by
	 *  the sign-in page and, without `href`, as the live preview in Settings → Sign-on. */
	interface Props {
		name: string;
		branding: SsoBranding;
		logoUrl?: string | null;
		logoDarkUrl?: string | null;
		href?: string;
		/** A smaller one, for lists. */
		compact?: boolean;
	}
	let { name, branding, logoUrl = null, logoDarkUrl = null, href, compact = false }: Props = $props();

	const words = $derived((branding.label || 'Continue with {name}').replaceAll('{name}', name));
	const logo = $derived((branding.logo === 'icon' || branding.logo === 'wordmark') && !logoUrl ? 'symbol' : branding.logo);
	const custom = $derived(branding.style !== 'theme' && /^#[0-9a-f]{6}$/i.test(branding.color));
	const style = $derived.by(() => {
		if (!custom) return '';
		if (branding.style === 'filled') {
			const text = branding.text_color || autoText(branding.color);
			return `background:${branding.color};color:${text};border-color:${branding.color}`;
		}
		return `border-color:${branding.color};color:${branding.color}`;
	});

	/** White when it's readable (4.5:1), else whichever reads better: the server's rule. */
	function autoText(hex: string): string {
		const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
		const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
		const white = 1.05 / (lum + 0.05);
		const black = (lum + 0.05) / 0.05;
		return white >= 4.5 || white >= black ? '#ffffff' : '#000000';
	}
</script>

{#snippet inside()}
	{#if logo === 'wordmark'}
		<img src={logoUrl} alt="" class="sso-logo-light {compact ? 'h-4' : 'h-6'} max-w-[70%] object-contain" />
		{#if logoDarkUrl}<img src={logoDarkUrl} alt="" class="sso-logo-dark {compact ? 'h-4' : 'h-6'} max-w-[70%] object-contain" />{/if}
		<span class="sr-only">{words}</span>
	{:else}
		{#if logo === 'icon'}
			<img src={logoUrl} alt="" class="sso-logo-light {compact ? 'size-4' : 'size-5'} object-contain" />
			{#if logoDarkUrl}<img src={logoDarkUrl} alt="" class="sso-logo-dark {compact ? 'size-4' : 'size-5'} object-contain" />{/if}
		{:else if logo === 'symbol'}
			<Icon name={branding.symbol || 'key'} size={compact ? 16 : 20} class={custom ? '' : 'text-primary'} />
		{/if}
		<span class="truncate">{words}</span>
	{/if}
{/snippet}

{#if href}
	<!-- A full page load: the server sends the browser on to the provider. -->
	<a
		{href}
		data-sveltekit-reload
		class="m3-sso-button state-layer focus-ring flex {compact ? 'h-9 px-3' : 'h-12 px-4'} min-w-0 items-center justify-center gap-2 rounded-full border border-outline label-large text-on-surface {branding.style === 'filled' && custom ? 'shadow-e1' : ''}"
		{style}
		aria-label={words}
	>
		{@render inside()}
	</a>
{:else}
	<div
		class="m3-sso-button flex {compact ? 'h-9 px-3' : 'h-12 px-4'} min-w-0 items-center justify-center gap-2 rounded-full border border-outline label-large text-on-surface {branding.style === 'filled' && custom ? 'shadow-e1' : ''}"
		{style}
		role="img"
		aria-label={`Preview: ${words}`}
	>
		{@render inside()}
	</div>
{/if}

<style>
	.sso-logo-dark {
		display: none;
	}
	:global(html[data-theme='dark']) .sso-logo-dark {
		display: inline;
	}
	:global(html[data-theme='dark']) .sso-logo-light:has(+ .sso-logo-dark) {
		display: none;
	}
	@media (prefers-color-scheme: dark) {
		:global(html:not([data-theme='light'])) .sso-logo-dark {
			display: inline;
		}
		:global(html:not([data-theme='light'])) .sso-logo-light:has(+ .sso-logo-dark) {
			display: none;
		}
	}
</style>
